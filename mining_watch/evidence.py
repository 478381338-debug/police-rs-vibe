"""多源佐证融合：把人工/半自动因子汇总为七族证据链。

设计边界：
- score 是同一情景内的排序分，不是概率、置信度或 F1；
- 缺失因子写 null/缺项，不记 0；
- 合规因子按台账有效期处理，不套用 180 天机械衰减；
- 业务优先级不因本模块自动改写，只给 evidence_priority 建议。
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config_yantai_evidence.yaml"
DEFAULT_EVENTS = ROOT / "data" / "yantai_evidence" / "events.json"

FAMILY_ORDER = ("O", "S", "L", "D", "T", "C", "H")
DIRECT_FAMILIES = ("O", "S", "L", "D", "T")
STRUCTURE_FACTORS = {"F02", "F04", "F05", "F06", "F07", "F08"}
VALID_STRENGTHS = (0.0, 0.33, 0.67, 1.0)
TIER_ORDER = ("A", "B", "C", "D")

# 仅 Sentinel-2 时这些目标的强度上限；有高分/DSM 时由输入显式给更高值。
SENTINEL_STRENGTH_CAP = {"F02": 0.67, "F04": 0.33, "F05": 0.33, "F06": 0.67}
SENTINEL_RELIABILITY_CAP = {"F04": 0.35, "F07": 0.0}


def load_config(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_CONFIG
    with p.open("r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    for key in ("families", "factors", "scenarios", "evidence_tiers", "priority"):
        if key not in cfg:
            raise ValueError(f"证据配置缺少 {key}: {p}")
    return cfg


def load_event_inputs(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_EVENTS
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"as_of": None, "events": {}}
    return data if isinstance(data, dict) else {"as_of": None, "events": {}}


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _norm_strength(value: Any) -> float:
    v = float(value)
    for allowed in VALID_STRENGTHS:
        if abs(v - allowed) < 1e-9:
            return allowed
    raise ValueError(f"strength 只能是 0/0.33/0.67/1.0，收到 {value!r}")


def _time_decay(fid: str, observed_at: Any, as_of: date, fdef: dict) -> tuple[float, str]:
    obs = _as_date(observed_at)
    if obs is None:
        return 0.5, "unknown"
    days = max(0, (as_of - obs).days)
    if fid in {"F13", "F14", "F15"}:
        window = int(fdef.get("window_days", 365))
        if days > window:
            return 0.8, f"台账超 {window} 天未更新"
        return 1.0, "台账有效期内"
    if days <= 30:
        return 1.0, "0-30 天"
    if days <= 90:
        return 0.9, "31-90 天"
    if days <= 180:
        return 0.7, "91-180 天"
    return 0.4, ">180 天"


def _factor_entry(raw: dict, cfg: dict, as_of: date) -> dict:
    fid = str(raw.get("id", "")).upper()
    fdef = cfg["factors"].get(fid)
    if not fdef:
        raise ValueError(f"未知因子 {fid!r}")
    if raw.get("strength") is None:
        raise ValueError(f"{fid} 缺少 strength；缺失因子应直接不写")
    strength = _norm_strength(raw["strength"])
    source = str(raw.get("source") or "")
    reliability = float(raw.get("reliability", fdef["reliability"]))
    note = str(raw.get("note") or "")
    if "sentinel" in source.lower():
        cap = SENTINEL_STRENGTH_CAP.get(fid)
        if cap is not None and strength > cap:
            note = (note + f"；仅 Sentinel-2，强度按 {cap:.2f} 封顶").strip("；")
            strength = cap
        rcap = SENTINEL_RELIABILITY_CAP.get(fid)
        if rcap is not None:
            if rcap <= 0:
                raise ValueError(f"{fid} 在 10 m Sentinel-2 下不可判，应直接不写")
            if reliability > rcap:
                note = (note + f"；仅 Sentinel-2，可靠性按 {rcap:.2f} 封顶").strip("；")
                reliability = rcap
    if not 0.0 <= reliability <= 1.0:
        raise ValueError(f"{fid} reliability 超出 0–1：{reliability}")
    decay, decay_reason = _time_decay(fid, raw.get("observed_at"), as_of, fdef)
    adjusted = round(strength * reliability * decay, 3)
    return {
        "id": fid,
        "family": fdef["family"],
        "name": fdef["name"],
        "strength": strength,
        "reliability": round(reliability, 3),
        "time_decay": decay,
        "decay_reason": decay_reason,
        "adjusted": adjusted,
        "observed_at": raw.get("observed_at"),
        "source": source or None,
        "note": note or None,
    }


def _negative_entry(raw: dict, cfg: dict) -> dict:
    nid = str(raw.get("id", "")).upper()
    ndef = cfg.get("negatives", {}).get(nid)
    if not ndef:
        raise ValueError(f"未知负因子 {nid!r}")
    strength = _norm_strength(raw.get("strength", 0))
    return {
        "id": nid,
        "name": ndef.get("name", nid),
        "strength": strength,
        "note": str(raw.get("note") or ""),
    }


def _quality_entry(raw: dict, cfg: dict) -> dict:
    qid = str(raw.get("id", "")).upper()
    qdef = cfg.get("quality", {}).get(qid)
    if not qdef:
        raise ValueError(f"未知质量门 {qid!r}")
    return {
        "id": qid,
        "name": qdef.get("name", qid),
        "strength": _norm_strength(raw.get("strength", 1.0)),
        "note": str(raw.get("note") or ""),
    }


def _family_scores(factors: Iterable[dict]) -> dict[str, float]:
    out: dict[str, float] = {}
    for fam in FAMILY_ORDER:
        vals = sorted(
            (f["adjusted"] for f in factors if f["family"] == fam),
            reverse=True,
        )
        if not vals:
            out[fam] = 0.0
        elif len(vals) == 1:
            out[fam] = round(vals[0], 3)
        else:
            out[fam] = round(min(1.0, vals[0] + 0.2 * vals[1]), 3)
    return out


def _downgrade(tier: str, steps: int, physical_ok: bool) -> str:
    idx = min(len(TIER_ORDER) - 1, TIER_ORDER.index(tier) + max(0, steps))
    if TIER_ORDER[idx] == "D" and physical_ok:
        idx = 2  # 有物理族时，负因子最多降到 C，不直接清空
    return TIER_ORDER[idx]


def _tier(score: float, family_scores: dict[str, float], required_ok: bool,
          has_veto: bool, f17: float, direct67: int, cfg: dict) -> str:
    count50 = sum(1 for v in family_scores.values() if v >= 0.5)
    a = cfg["evidence_tiers"].get("A", {})
    b = cfg["evidence_tiers"].get("B", {})
    if (
        score >= float(a.get("min_score", 70))
        and count50 >= int(a.get("min_families", 3))
        and (required_ok or not a.get("require_physical", True))
        and (not has_veto or not a.get("require_no_veto", True))
        and (f17 >= 0.67 or direct67 >= 2)
    ):
        return "A"
    if (
        score >= float(b.get("min_score", 55))
        and count50 >= int(b.get("min_families", 2))
        and (required_ok or not b.get("require_physical", True))
        and (not has_veto or not b.get("require_no_veto", True))
    ):
        return "B"
    if score >= float(cfg["evidence_tiers"].get("C", {}).get("min_score", 40)) or count50 >= 1:
        return "C"
    return "D"


def evaluate(scenario_id: str, factors: list[dict], *,
             negatives: list[dict] | None = None,
             quality: list[dict] | None = None,
             config: dict | None = None,
             as_of: str | date | None = None) -> dict:
    cfg = config or load_config()
    scenario = cfg["scenarios"].get(scenario_id)
    if not scenario:
        raise ValueError(f"未知情景 {scenario_id!r}")
    if as_of is None:
        as_of_date = date.today()
    else:
        as_of_date = _as_date(as_of) or date.today()

    factor_entries = [_factor_entry(f, cfg, as_of_date) for f in factors if f.get("strength") is not None]
    negative_entries = [_negative_entry(n, cfg) for n in (negatives or [])]
    quality_entries = [_quality_entry(q, cfg) for q in (quality or [])]

    family_scores = _family_scores(factor_entries)
    weights = scenario.get("weights", {})
    score = round(100.0 * sum(float(weights.get(f, 0)) * family_scores[f] for f in FAMILY_ORDER), 1)
    required_any = list(scenario.get("required_family_any", []))
    required_ok = any(family_scores.get(f, 0) >= 0.5 for f in required_any)
    direct67 = sum(1 for f in DIRECT_FAMILIES if family_scores.get(f, 0) >= 0.67)
    f17 = max((x["adjusted"] for x in factor_entries if x["id"] == "F17"), default=0.0)
    vetoes = [n for n in negative_entries if n["strength"] >= 0.67]
    has_veto = bool(vetoes)
    tier = _tier(score, family_scores, required_ok, has_veto, f17, direct67, cfg)

    quality_gate = quality_entries[0] if quality_entries else None
    compliance_observation = any(n["id"] == "N03" and n["strength"] >= 0.67 for n in negative_entries)
    hard_farmland = any(n["id"] == "N02" and n["strength"] >= 1.0 for n in negative_entries)
    direct_structure = any(
        f["id"] in STRUCTURE_FACTORS and f["strength"] >= 0.67 for f in factor_entries
    )
    farmland_observation = hard_farmland and not direct_structure

    if quality_gate:
        tier = "C" if any(v >= 0.5 for v in family_scores.values()) else "D"
    elif compliance_observation:
        tier = "C" if any(v >= 0.5 for v in family_scores.values()) else "D"
    elif farmland_observation:
        # 强农田负因子且没有坑口/建筑/道路/机械等直接结构证据：
        # 光谱变化优先解释为农事活动，不用软降级继续保留采矿线索。
        tier = "D"
    else:
        n_down = max((n["strength"] for n in vetoes if n["id"] in {"N01", "N02"}), default=0.0)
        if n_down >= 0.67:
            tier = _downgrade(tier, 2 if n_down >= 1.0 else 1, required_ok)

    priority = cfg.get("priority", {}).get("base", {}).get(tier, "Q")
    if quality_gate or compliance_observation or farmland_observation:
        priority = "Q"

    present = {f["id"] for f in factor_entries}
    key_factors = list(scenario.get("key_factors", []))
    missing = [fid for fid in key_factors if fid not in present]
    missing_notes = [
        f"{fid} {cfg['factors'][fid]['name']}" for fid in missing if fid in cfg["factors"]
    ]
    support = [
        f"{f['id']} {f['name']}（{f['adjusted']:.2f}）"
        for f in sorted(factor_entries, key=lambda x: x["adjusted"], reverse=True)
        if f["adjusted"] > 0
    ]
    counter = [f"{n['id']} {n['name']}（{n['strength']:.2f}）" for n in negative_entries if n["strength"] > 0]
    family_chain = []
    for fam in FAMILY_ORDER:
        members = [f for f in factor_entries if f["family"] == fam]
        if not members and family_scores[fam] <= 0:
            continue
        members.sort(key=lambda x: x["adjusted"], reverse=True)
        summary = "；".join((m.get("note") or m["name"]) for m in members[:2])
        family_chain.append({
            "family": fam,
            "label": cfg["families"][fam]["label"],
            "score": family_scores[fam],
            "factors": [m["id"] for m in members],
            "summary": summary,
        })

    note = "score 是同一情景内的排序分，未校准，不是概率；缺失族按 0 计分，只表示证据不足。"
    if quality_gate:
        note += f" 质量门 {quality_gate['id']} 触发，优先级 Q（不派单）。"
    if compliance_observation:
        note += " N03 合法工程/修复成立，转合规观察，不派单。"
    if farmland_observation:
        note += " N02 强农田负因子成立且无直接结构证据，按农田管理负样本处理，不派单。"

    return {
        "scenario": {
            "primary": scenario_id,
            "label": scenario.get("name", scenario_id),
            "secondary": [],
            "rationale": "",
        },
        "evidence_factors": factor_entries,
        "corroboration": {
            "score": score,
            "evidence_tier": tier,
            "priority": priority,
            "family_scores": family_scores,
            "required_family_any": required_any,
            "required_family_ok": required_ok,
            "direct_families_0_67": direct67,
            "vetoes": vetoes,
            "quality_gate": quality_gate,
            "compliance_observation": compliance_observation,
            "farmland_observation": farmland_observation,
            "missing": missing,
            "missing_notes": missing_notes,
            "top_support": support[:5],
            "top_counter": counter[:5],
            "note": note,
            "as_of": as_of_date.isoformat(),
        },
        "evidence_chain": family_chain,
        "factor_summary": {
            "support": support[:5],
            "counter": counter[:5],
            "missing": missing_notes,
        },
        "next_checks": [],
    }


def attach_events(events: list[dict], *,
                  inputs: dict | None = None,
                  config: dict | None = None) -> list[dict]:
    """给事件追加多源佐证字段；没有输入的事件保持原样。"""
    cfg = config or load_config()
    data = inputs or load_event_inputs()
    by_id = (data.get("events") or {}) if isinstance(data, dict) else {}
    as_of = data.get("as_of")
    for event in events:
        raw = by_id.get(event.get("id"))
        if not raw:
            continue
        result = evaluate(
            raw.get("scenario", "S1"),
            raw.get("factors") or [],
            negatives=raw.get("negatives") or [],
            quality=raw.get("quality") or [],
            config=cfg,
            as_of=as_of,
        )
        result["next_checks"] = list(raw.get("next_checks") or [])
        result["corroboration"]["next_checks"] = result["next_checks"]
        event.update(result)
        event.setdefault("legacy_priority", event.get("priority"))
        event["evidence_priority"] = result["corroboration"]["priority"]
        event["evidence_tier"] = result["corroboration"]["evidence_tier"]
        event["evidence_scenario"] = result["scenario"]
        if result["corroboration"].get("farmland_observation"):
            event["activity_status"] = "非采掘变化（农田管理）"
            event["compliance_status"] = "不适用"
            event["priority"] = "Q"
            event["lifecycle"] = "待排除"
            event["disposition_status"] = "待排除"
            event["evidence_correction"] = (
                "N02=1.0 且无坑口/建筑/道路/机械等直接结构证据；"
                "按农田管理负样本处理，不派单。"
            )
        cons = list(event.get("counter_evidence") or [])
        for item in result["factor_summary"]["counter"]:
            if item not in cons:
                cons.append(item)
        event["counter_evidence"] = cons
    return events


def scenario_prior(layer: str | None) -> str:
    """没有人工因子时，仅给候选一个情景先验；不代表证据。"""
    return {
        "mine_candidate": "S1",
        "other_bare": "S1",
        "uncertain": "S1",
        "farmland_management": "S1",
    }.get(str(layer or ""), "S1")


SCENARIO_SECONDARY = {
    "mine_candidate": ["S6"],   # 堆场/排土场
    "other_bare": ["S7"],       # 废弃矿山复采
}

# 方案 §8.1 的字段映射阈值。只把已测量字段翻成因子，不是新的检测阈值。
DERIVE_MIN_AREA_HA = 0.5        # S1 单条主证据面积门槛；20 m 下 13 px≈0.52 ha
DERIVE_DELTA_BSI = 0.10         # 与旧 priority 同阈值，只作“有变化”的入口
DERIVE_PERSISTENT_BARE_YEARS = 2
DERIVE_CROPLAND_HARD = 0.60     # ≥ 记 N02=1.0（不硬删除，转不派单）
DERIVE_CROPLAND_SOFT = 0.30     # ≥ 记 N02=0.33（软反证）
DERIVE_VALID_FRAC_MIN = 0.80    # < 记 Q01
DERIVE_SENTINEL_CAP = 0.67      # 单源 Sentinel-2 光学不给 1.0


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def derive_candidate_factors(props: dict, as_of: Any = None) -> dict:
    """把候选 GeoJSON 属性翻成因子/负因子/质量门（方案 §8.1）。

    只用候选里已测量的字段：缺字段就不写该因子，不补 0、不猜结论；F02 形态
    与 F14/F15 许可关系一律不派生（10 m 判不了台阶/坑口，邻近也不等于许可）。
    """
    layer = props.get("layer")
    area = _num(props.get("area_ha"))
    dbsi = _num(props.get("delta_bsi"))
    bare_years = _num(props.get("bare_years")) or 0.0
    persistent = bool(props.get("persistent_bare")) or bare_years >= DERIVE_PERSISTENT_BARE_YEARS
    summer_frac = _num(props.get("summer_bare_frac"))
    observed_at = props.get("last_alarm") or props.get("last_clear") or props.get("t1")
    if observed_at is None and as_of is not None:
        # 持续裸/规则补全对象由目标年夏季合成定义，本身不带报警日期；
        # 按分析期末日计，避免把“当年观测”误判成“日期未知”而衰减一半。
        observed_at = str(as_of)
    factors: list[dict] = []
    negatives: list[dict] = []
    quality: list[dict] = []
    notes: list[str] = []

    # F01 光学变化：ΔBSI（幅度）与持续性（多年裸）是两条输入，合并成一个强度
    changed = dbsi is not None and dbsi >= DERIVE_DELTA_BSI
    weak_change = dbsi is not None and dbsi > 0
    summer_bare = bool(props.get("summer_bare")) and (summer_frac or 0.0) >= 0.5
    if dbsi is None and not persistent:
        strength = None
        notes.append("无 ΔBSI 且非持续裸：F01 不写")
    elif persistent or changed:
        strength = 0.67
    elif weak_change or summer_bare:
        strength = 0.33
    else:
        strength = None
    if strength is not None:
        parts = [f"ΔBSI={dbsi:.3f}" if dbsi is not None else "ΔBSI 缺失"]
        if area is not None:
            parts.append(f"图斑 {area:.2f} ha")
        if persistent:
            parts.append(f"持续裸 {bare_years:.0f} 年")
        if summer_frac is not None:
            parts.append(f"夏季裸 {summer_frac:.0%}")
        if area is not None and area < DERIVE_MIN_AREA_HA:
            strength = min(strength, 0.33)
            parts.append("面积<0.5 ha，强度封顶 0.33")
        parts.append("单源光学，强度上限 0.67")
        factors.append({
            "id": "F01",
            "strength": strength,
            "observed_at": observed_at,
            "source": "Sentinel-2 L2A（候选属性）",
            "note": "；".join(parts),
            "derived": True,
        })

    # F13 邻近已知矿点：只是上下文，不是许可范围
    if props.get("near_known"):
        dist = _num(props.get("distance_known_m"))
        kind = props.get("nearest_known_kind") or ""
        factors.append({
            "id": "F13",
            "strength": 0.33,
            "observed_at": None,
            "source": "候选字段 near_known/distance_known_m",
            "note": (
                f"距 {props.get('nearest_known') or '已知矿点'}"
                + (f" {dist:.0f} m" if dist is not None else "")
                + (f"（{kind}）" if kind else "")
                + "；邻近不是许可范围，也不是违法证据"
            ),
            "derived": True,
        })

    # N01 物候：夏季返绿优先按物候解释
    if str(props.get("phenology_class") or "") == "green_summer":
        ndvi = _num(props.get("summer_ndvi"))
        negatives.append({
            "id": "N01",
            "strength": 0.67,
            "note": "夏季返绿（phenology_class=green_summer"
            + (f"，summer_ndvi={ndvi:.2f}" if ndvi is not None else "")
            + "）：物候/农事解释优先，不能排除季节性裸化",
        })

    # N02 农田：WorldCover 耕地占比
    crop = _num(props.get("cropland_frac"))
    if crop is not None and crop >= DERIVE_CROPLAND_HARD:
        negatives.append({
            "id": "N02",
            "strength": 1.0,
            "note": f"WorldCover 2021 耕地占比 {crop:.0%}：按农田管理负样本处理，不硬删除",
        })
    elif crop is not None and crop >= DERIVE_CROPLAND_SOFT:
        negatives.append({
            "id": "N02",
            "strength": 0.33,
            "note": f"WorldCover 2021 耕地占比 {crop:.0%}：需排除农田整治/翻耕",
        })

    # 质量门：观测质量与基线
    valid = _num(props.get("valid_frac"))
    if valid is not None and valid < DERIVE_VALID_FRAC_MIN:
        quality.append({
            "id": "Q01",
            "strength": 1.0,
            "note": f"对象有效观测比例 {valid:.2f} < {DERIVE_VALID_FRAC_MIN:.2f}；q<0.8 的日期不自动判读",
        })
    if props.get("baseline_insufficient") is True:
        quality.append({
            "id": "Q03",
            "strength": 1.0,
            "note": "基线有效月数不足 8 个月；先补基线，不参与结论",
        })

    return {
        "scenario": {
            "primary": scenario_prior(layer),
            "secondary": list(SCENARIO_SECONDARY.get(str(layer or ""), [])),
            "rationale": f"layer={layer or 'unknown'}；按方案 §8.1 由候选属性派生，未使用高分/许可/现场证据",
        },
        "factors": factors,
        "negatives": negatives,
        "quality": quality,
        "notes": notes,
    }


def next_checks_for(corroboration: dict) -> list[str]:
    """按缺口给下一步：先档案、后高分、再现场。"""
    missing = set(corroboration.get("missing") or [])
    checks: list[str] = []
    if {"F14", "F15"} & missing:
        checks.append("调取采矿权/用地/环评许可台账，核对边界、有效期与矿种")
    if {"F02", "F03", "F04", "F06"} & missing:
        checks.append("调高分影像（GF-2/GF-7）判坑口、台阶、堆场与入场道路")
    if "F07" in missing:
        checks.append("现场或视频核查机械、运输车辆与作业时段")
    if "F10" in missing:
        checks.append("需要时补 InSAR 形变/相干性（C 波段分层阈值）")
    if "F17" in missing:
        checks.append("现场核查并留存照片、笔录与定位")
    return checks or ["补齐至少一个物理族因子后再评估"]


def score_candidate(props: dict, *, config: dict | None = None,
                    as_of: str | date | None = None) -> dict:
    """候选属性 → 证据评分。as_of 应为分析期末日，否则证据按“距今”衰减。"""
    cfg = config or load_config()
    raw = derive_candidate_factors(props, as_of=as_of)
    result = evaluate(
        raw["scenario"]["primary"],
        raw["factors"],
        negatives=raw["negatives"],
        quality=raw["quality"],
        config=cfg,
        as_of=as_of,
    )
    result["scenario"].update({
        "secondary": raw["scenario"]["secondary"],
        "rationale": raw["scenario"]["rationale"],
    })
    result["next_checks"] = next_checks_for(result["corroboration"])
    result["corroboration"]["next_checks"] = result["next_checks"]
    result["derivation"] = {
        "method": "candidate_attributes",
        "config_version": cfg.get("version"),
        "notes": raw["notes"],
    }
    return result


def attach_candidate_evidence(props: dict, *, config: dict | None = None,
                              as_of: str | date | None = None) -> dict:
    """把 score_candidate 的结果写回候选属性（只追加，不改现有字段）。"""
    result = score_candidate(props, config=config, as_of=as_of)
    props["scenario"] = result["scenario"]
    props["evidence_factors"] = result["evidence_factors"]
    props["corroboration"] = result["corroboration"]
    props["evidence_chain"] = result["evidence_chain"]
    props["factor_summary"] = result["factor_summary"]
    props["next_checks"] = result["next_checks"]
    props["derivation"] = result["derivation"]
    props["evidence_tier"] = result["corroboration"]["evidence_tier"]
    props["evidence_priority"] = result["corroboration"]["priority"]
    props["evidence_score"] = result["corroboration"]["score"]
    return result