"""给候选 GeoJSON 追加多源佐证字段（离线脚本，不覆盖原文件）。

用法：
  python scripts/yantai_corroborate.py \
      --candidates data/yantai_2025_v2/candidates_mining_belt.geojson \
      --evidence data/yantai_evidence/events.json \
      --out data/yantai_2025_v2/candidates_mining_belt.corroborated.geojson

说明：
- 只有 evidence 中显式列出的因子才计分；没有因子输入的候选只写情景先验、
  evidence_tier=D、priority=Q 和“无因子输入”，不伪造证据。
- 候选与证据的匹配键依次为 properties.id、properties.event_id；匹配不到就按空输入处理。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mining_watch import evidence as evidence_mod  # noqa: E402

DEFAULT_CANDIDATES = ROOT / "data" / "yantai_2025_v2" / "candidates_mining_belt.geojson"
DEFAULT_EVIDENCE = ROOT / "data" / "yantai_evidence" / "events.json"
DEFAULT_OUT = ROOT / "data" / "yantai_2025_v2" / "candidates_mining_belt.corroborated.geojson"


def _key(props: dict) -> str | None:
    for name in ("id", "event_id", "candidate_id"):
        value = props.get(name)
        if value:
            return str(value)
    return None


def _empty_result(scenario: str, cfg: dict) -> dict:
    scen = cfg["scenarios"].get(scenario, {})
    return {
        "scenario": {
            "primary": scenario,
            "label": scen.get("name", scenario),
            "secondary": [],
            "rationale": "无人工因子输入，仅按图层给情景先验",
        },
        "evidence_factors": [],
        "corroboration": {
            "score": 0.0,
            "evidence_tier": "D",
            "priority": "Q",
            "family_scores": {f: 0.0 for f in evidence_mod.FAMILY_ORDER},
            "required_family_any": list(scen.get("required_family_any", [])),
            "required_family_ok": False,
            "direct_families_0_67": 0,
            "vetoes": [],
            "quality_gate": None,
            "compliance_observation": False,
            "missing": list(scen.get("key_factors", [])),
            "missing_notes": [],
            "top_support": [],
            "top_counter": [],
            "note": "无因子输入；不生成证据结论。",
            "as_of": None,
        },
        "evidence_chain": [],
        "factor_summary": {"support": [], "counter": [], "missing": []},
        "next_checks": ["补充至少一个物理族因子（O/S/D/L）后再评估"],
        "evidence_priority": "Q",
        "evidence_tier": "D",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="候选 GeoJSON 多源佐证标注")
    parser.add_argument("--candidates", type=Path, default=DEFAULT_CANDIDATES)
    parser.add_argument("--evidence", type=Path, default=DEFAULT_EVIDENCE)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 个，0 表示全部")
    parser.add_argument("--derive", action="store_true",
                        help="没有人工因子时，按方案 §8.1 用候选属性派生因子")
    parser.add_argument("--as-of", default=None,
                        help="评估基准日（YYYY-MM-DD）；派生模式建议填分析期末日")
    parser.add_argument("--aoi", default=None, help="写入 summary 时用的 AOI 名")
    parser.add_argument("--update-summary", type=Path, default=None,
                        help="把 evidence_fusion 计数合并写回 summary.json")
    args = parser.parse_args()

    if args.out.resolve() == args.candidates.resolve():
        print("拒绝覆盖原候选文件；请换 --out", file=sys.stderr)
        return 2
    cfg = evidence_mod.load_config(args.config)
    data = json.loads(args.candidates.read_text(encoding="utf-8"))
    evidence_data = evidence_mod.load_event_inputs(args.evidence)
    by_key = evidence_data.get("events") or {}
    as_of = args.as_of or evidence_data.get("as_of")
    features = data.get("features") or []
    if args.limit:
        features = features[: args.limit]

    counts = {"A": 0, "B": 0, "C": 0, "D": 0}
    priorities = {"P1": 0, "P2": 0, "P3": 0, "Q": 0}
    by_layer: dict[str, dict] = {}
    derived = 0
    for feature in features:
        props = feature.setdefault("properties", {})
        layer = props.get("layer")
        raw = by_key.get(_key(props) or "") or {}
        scenario = raw.get("scenario") or evidence_mod.scenario_prior(layer)
        if any(raw.get(k) for k in ("factors", "negatives", "quality")):
            result = evidence_mod.evaluate(
                scenario,
                raw.get("factors") or [],
                negatives=raw.get("negatives") or [],
                quality=raw.get("quality") or [],
                config=cfg,
                as_of=as_of,
            )
            result["next_checks"] = list(raw.get("next_checks") or [])
            result["corroboration"]["next_checks"] = result["next_checks"]
        elif args.derive:
            result = evidence_mod.score_candidate(props, config=cfg, as_of=as_of)
            result["next_checks"] = result["next_checks"] or list(raw.get("next_checks") or [])
            derived += 1
        else:
            result = _empty_result(scenario, cfg)
        props["scenario"] = result["scenario"]
        props["evidence_factors"] = result["evidence_factors"]
        props["corroboration"] = result["corroboration"]
        props["evidence_chain"] = result["evidence_chain"]
        props["factor_summary"] = result["factor_summary"]
        props["next_checks"] = result["next_checks"]
        tier = result["corroboration"]["evidence_tier"]
        priority = result["corroboration"]["priority"]
        props["evidence_tier"] = tier
        props["evidence_priority"] = priority
        props["evidence_score"] = result["corroboration"]["score"]
        counts[tier] = counts.get(tier, 0) + 1
        priorities[priority] = priorities.get(priority, 0) + 1
        bucket = by_layer.setdefault(str(layer or "unknown"), {"A": 0, "B": 0, "C": 0, "D": 0})
        bucket[tier] += 1

    data["features"] = features
    data.setdefault("metadata", {})
    if isinstance(data["metadata"], dict):
        data["metadata"]["corroboration"] = {
            "source": str(args.candidates),
            "evidence": str(args.evidence),
            "method": "candidate_attributes" if derived else "manual_events",
            "as_of": as_of,
            "counts": counts,
            "priorities": priorities,
            "by_layer": by_layer,
            "note": (
                "按方案 §8.1 用候选属性派生因子；缺失字段不补、不记 0。"
                if derived else
                "无因子输入的候选为 D/Q，仅表示证据不足，不代表无活动。"
            ),
        }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"写入 {args.out}，{len(features)} 个要素，等级 {counts}，优先级 {priorities}"
          + (f"，派生 {derived} 个" if args.derive else ""))
    if args.update_summary:
        summary = json.loads(args.update_summary.read_text(encoding="utf-8"))
        block = summary.setdefault("evidence_fusion", {})
        block.setdefault("method", "candidate_attributes")
        block["config"] = "config_yantai_evidence.yaml"
        block["config_version"] = cfg.get("version")
        block["as_of"] = as_of
        block["note"] = (
            "证据等级表示证据完备度，不是概率；单源光学最多 C 级。"
            "旧 priority（ΔBSI≥0.10）保留为 legacy_priority，不参与派单。"
        )
        key = args.aoi or Path(args.candidates).stem
        block.setdefault("by_aoi", {})[key] = {
            "counts": counts,
            "priorities": priorities,
            "by_layer": by_layer,
            "candidates": Path(args.candidates).name,
        }
        merged = {"A": 0, "B": 0, "C": 0, "D": 0}
        merged_pri = {"P1": 0, "P2": 0, "P3": 0, "Q": 0}
        for item in block["by_aoi"].values():
            for k, v in item.get("counts", {}).items():
                merged[k] = merged.get(k, 0) + v
            for k, v in item.get("priorities", {}).items():
                merged_pri[k] = merged_pri.get(k, 0) + v
        block["counts"] = merged
        block["priorities"] = merged_pri
        args.update_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print(f"更新 {args.update_summary}：{merged}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())