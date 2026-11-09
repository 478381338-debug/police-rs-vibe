"""法条与证据清单检索。

数据是人工整理的草稿库，不是法律数据库：
- 每条都带 review_status=draft_requires_legal_review；
- 只做关键词/灾种检索和证据方向提示；
- 不自动给出构罪、处罚或移送结论。
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path

from app import config

KB_PATH = Path(config.DATA) / "legal" / "knowledge_base.json"

HAZARD_LABELS = {
    "fire_scar": "火烧迹地",
    "mining": "盗采/非法采矿",
    "mining_damage": "矿山破坏",
    "landslide": "滑坡",
    "debris_flow": "泥石流",
    "snowline": "雪线变化",
    "construction": "违建/施工",
    "dumping": "固废/倾倒",
    "water_anomaly": "水体异常",
    "wetland": "湿地占用",
    "veg_degradation": "植被退化",
    "bare_soil": "裸土/农用地",
    "road_intrusion": "道路侵入",
    "fire_risk": "火灾风险",
    "flood_risk": "洪涝风险",
    "drought_risk": "干旱风险",
    "geohazard_risk": "地质灾害风险",
}

HAZARD_ALIASES = {
    "fire": "fire_risk",
    "flood": "flood_risk",
    "drought": "drought_risk",
    "geohazard": "geohazard_risk",
}

CLASS_TO_HAZARD = {
    "盗采痕迹": "mining",
    "疑似盗采": "mining",
    "非法采矿": "mining",
    "矿山": "mining",
    "矿山破坏": "mining_damage",
    "疑似违建": "construction",
    "违法建设": "construction",
    "施工": "construction",
    "建筑": "construction",
    "垃圾堆放": "dumping",
    "固废": "dumping",
    "倾倒": "dumping",
    "水体异常": "water_anomaly",
    "水质异常": "water_anomaly",
    "湿地": "wetland",
    "湿地占用": "wetland",
    "植被退化": "veg_degradation",
    "裸土": "bare_soil",
    "道路侵入": "road_intrusion",
    "道路": "road_intrusion",
    "火烧迹地": "fire_scar",
    "火迹": "fire_scar",
    "过火": "fire_scar",
}

# 自然风险类灾种没有直接对应的刑法条文时，至少给出通用取证和移送清单。
GENERAL_IDS = [
    "criminal-procedure-law-50",
    "criminal-procedure-law-128-141",
    "administrative-penalty-law-27",
]

# 灾种到主要刑法条文的推荐关系，用于案件清单把最相关的一条排在前面。
HAZARD_PRIMARY = {
    "mining": "criminal-law-343",
    "mining_damage": "criminal-law-343",
    "dumping": "criminal-law-338",
    "water_anomaly": "criminal-law-338",
    "construction": "criminal-law-342",
    "bare_soil": "criminal-law-342",
    "road_intrusion": "criminal-law-342",
    "veg_degradation": "criminal-law-345",
    "fire_scar": "criminal-law-345",
    "wetland": "criminal-law-342",
}

# 关键词到主要法条的规则：(命中词, 条目 id, 规则名称, 对应灾种)。
# 用于把“占用农田地”这类口语说法归到具体罪名，不替代法律适用判断。
PRIMARY_RULES: list[tuple[tuple[str, ...], str, str, str | None]] = [
    (
        (
            "非法占用农用地", "占用农用地", "占用农田", "农田地", "农田", "耕地",
            "基本农田", "改变用途", "硬化", "取土", "堆场", "占地",
        ),
        "criminal-law-342",
        "农用地占用",
        "bare_soil",
    ),
    (
        (
            "非法采矿", "盗采", "无证开采", "越界开采", "采矿许可证", "矿产品",
            "破坏性采矿", "采矿", "采砂", "挖砂", "砂石", "黏土",
        ),
        "criminal-law-343",
        "矿产资源开采",
        "mining",
    ),
    (
        (
            "污染环境", "倾倒", "排放", "有毒物质", "危险废物", "暗管", "渗坑",
            "渗井", "废水", "排污", "污染",
        ),
        "criminal-law-338",
        "污染环境",
        "dumping",
    ),
    (
        ("固体废物", "洋垃圾", "进口废物", "堆放", "处置"),
        "criminal-law-339",
        "固体废物",
        "dumping",
    ),
    (
        ("非法捕捞", "禁渔", "电鱼", "地笼", "渔获"),
        "criminal-law-340",
        "水产品捕捞",
        "water_anomaly",
    ),
    (
        ("野生动物", "珍贵濒危", "非法狩猎", "禁猎", "猎夹", "猎套", "捕鸟"),
        "criminal-law-341",
        "野生动物",
        "veg_degradation",
    ),
    (
        ("国家重点保护植物", "珍贵树木", "重点保护植物"),
        "criminal-law-344",
        "重点保护植物",
        "veg_degradation",
    ),
    (
        ("盗伐", "滥伐", "林木", "蓄积量", "采伐许可证", "砍伐", "砍树"),
        "criminal-law-345",
        "林木采伐",
        "veg_degradation",
    ),
    (
        ("毁林开垦", "采石", "采土", "尾矿", "矿渣"),
        "forest-law-39",
        "毁林开垦",
        "mining_damage",
    ),
    (
        ("自然保护区", "保护区", "开垦", "烧荒", "放牧", "狩猎"),
        "nature-reserve-regulation-26",
        "自然保护区",
        "construction",
    ),
]

# 口语说法到库内用语的扩展，解决“农田地/农用地”“挖砂/采砂”这类字面不一致。
SYNONYMS = {
    "农田": ("农用地", "耕地"),
    "农田地": ("农用地", "耕地"),
    "农地": ("农用地", "耕地"),
    "耕地": ("农用地", "基本农田"),
    "基本农田": ("耕地", "农用地"),
    "占用": ("非法占用农用地", "改变用途"),
    "占地": ("非法占用农用地", "占用"),
    "硬化": ("改变用途", "占用"),
    "取土": ("采土",),
    "挖土": ("取土", "采土"),
    "采砂": ("砂石", "采矿"),
    "挖砂": ("采砂", "砂石"),
    "采矿": ("非法采矿", "矿产品"),
    "盗采": ("非法采矿",),
    "排污": ("排放", "污染环境"),
    "污水": ("废水", "污染环境"),
    "倒垃圾": ("倾倒", "固体废物"),
    "垃圾": ("固体废物", "倾倒"),
    "砍树": ("采伐", "林木"),
    "砍伐": ("采伐", "林木", "滥伐"),
    "毁林": ("毁林开垦", "林地"),
    "猎捕": ("非法狩猎", "野生动物"),
    "电鱼": ("非法捕捞", "禁渔区"),
    "洋垃圾": ("固体废物", "进口"),
}

_TOKEN_SPLIT = re.compile(r"[\s,，、;；:：/／|()（）\[\]【】\"'“”‘’]+")
_FUZZY_MIN = 0.34

_LOCK = threading.Lock()
_CACHE: dict = {"mtime": None, "data": None}


def _load() -> dict:
    mtime = KB_PATH.stat().st_mtime
    with _LOCK:
        if _CACHE["data"] is not None and _CACHE["mtime"] == mtime:
            return _CACHE["data"]
    data = json.loads(KB_PATH.read_text(encoding="utf-8"))
    with _LOCK:
        _CACHE["mtime"] = mtime
        _CACHE["data"] = data
    return data


def meta() -> dict:
    data = _load()
    return {
        "version": data.get("version"),
        "review_status": data.get("review_status"),
        "disclaimer": data.get("disclaimer"),
        "count": len(data.get("entries", [])),
        "hazards": HAZARD_LABELS,
    }


def entries() -> list[dict]:
    return list(_load().get("entries", []))


def get_entry(entry_id: str) -> dict | None:
    for entry in entries():
        if entry.get("id") == entry_id:
            return entry
    return None


def _norm(text: object) -> str:
    return _TOKEN_SPLIT.sub("", str(text or "").lower())


def _bigrams(text: str) -> set[str]:
    text = _norm(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[index:index + 2] for index in range(len(text) - 1)}


def _similarity(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return 2 * len(a & b) / (len(a) + len(b))


def _rule_matches(entry_id: str, query: str) -> tuple[int, list[str], str, str | None]:
    q = _norm(query)
    for patterns, rule_id, label, hazard in PRIMARY_RULES:
        if rule_id != entry_id:
            continue
        hits = [pattern for pattern in patterns if _norm(pattern) in q]
        if not hits:
            return 0, [], "", None
        bonus = sum(10 + 2 * len(_norm(pattern)) for pattern in hits)
        return bonus, hits, label, hazard
    return 0, [], "", None


def _infer_hazard(query: str) -> str | None:
    q = _norm(query)
    if not q:
        return None
    best: tuple[int, str] | None = None
    for patterns, _entry_id, _label, hazard in PRIMARY_RULES:
        if not hazard:
            continue
        hits = [len(_norm(pattern)) for pattern in patterns if _norm(pattern) in q]
        if hits and (best is None or max(hits) > best[0]):
            best = (max(hits), hazard)
    return best[1] if best else None


def _score(entry: dict, query: str) -> tuple[int, list[str], str, str | None]:
    """返回 (得分, 命中词, 命中说明, 规则对应灾种)。"""
    q = _norm(query)
    if not q:
        return 1, [], "", None
    heading = _norm(entry.get("heading"))
    law = _norm(entry.get("law"))
    article = _norm(entry.get("article"))
    summary = _norm(entry.get("summary"))
    keywords = [_norm(item) for item in entry.get("keywords", [])]
    keyword_text = "".join(keywords)

    terms = [q]
    for word, expansions in SYNONYMS.items():
        if _norm(word) in q:
            terms.extend(_norm(item) for item in expansions)
    terms.extend(_norm(term) for term in _TOKEN_SPLIT.split(str(query or "")) if term)
    unique_terms = list(dict.fromkeys(term for term in terms if term))

    score = 0
    matched: list[str] = []
    for term in unique_terms:
        hit = False
        if term in heading:
            score += 8
            hit = True
        if term in keyword_text:
            score += 6
            hit = True
        if term in law:
            score += 3
            hit = True
        if term in article:
            score += 3
            hit = True
        if term in summary:
            score += 2
            hit = True
        if hit:
            matched.append(term)

    rule_score, rule_hits, rule_label, rule_hazard = _rule_matches(str(entry.get("id", "")), query)
    if rule_hits:
        score += rule_score
        matched = list(dict.fromkeys(rule_hits + matched))

    best_similarity = max(
        [_similarity(q, heading), *(_similarity(q, keyword) for keyword in keywords)],
        default=0.0,
    )
    if best_similarity >= _FUZZY_MIN:
        score += int(best_similarity * 24)

    if rule_hits:
        reason = f"按“{rule_label}”规则命中：{'、'.join(rule_hits[:4])}"
    elif matched:
        reason = "命中：" + "、".join(matched[:5])
    elif best_similarity >= _FUZZY_MIN:
        reason = f"字面相近 {round(best_similarity * 100)}%"
    else:
        reason = ""
    return score, matched, reason, rule_hazard


def search(query: str = "", hazard: str | None = None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    query = (query or "").strip()
    scored = []
    for entry in entries():
        if hazard and hazard not in entry.get("hazards", []):
            continue
        score, matched, reason, rule_hazard = _score(entry, query)
        if query and score < 5:
            continue
        item = {**entry, "score": score, "match_reason": reason} if query else entry
        scored.append((score, str(entry.get("id", "")), item, matched, rule_hazard))
    scored.sort(key=lambda row: (-row[0], row[1]))
    items = [row[2] for row in scored[:limit]]

    primary = None
    if query and scored and scored[0][0] >= 8:
        top = scored[0]
        primary = {
            "id": top[2].get("id"),
            "law": top[2].get("law"),
            "article": top[2].get("article"),
            "heading": top[2].get("heading"),
            "score": top[0],
            "match_reason": top[2].get("match_reason"),
            "matched_terms": top[3][:6],
        }

    inferred = _infer_hazard(query) if query else None
    suggestions: list[dict] = []
    if query and not items:
        for entry in entries():
            if hazard and hazard not in entry.get("hazards", []):
                continue
            heading = _norm(entry.get("heading"))
            keywords = [_norm(item) for item in entry.get("keywords", [])]
            similarity = max(
                [_similarity(query, heading), *(_similarity(query, keyword) for keyword in keywords)],
                default=0.0,
            )
            if similarity >= 0.2:
                suggestions.append((similarity, entry))
        suggestions.sort(key=lambda row: -row[0])
        suggestions = [
            {
                "id": entry.get("id"),
                "law": entry.get("law"),
                "article": entry.get("article"),
                "heading": entry.get("heading"),
            }
            for _, entry in suggestions[:3]
        ]

    data = _load()
    return {
        "query": query,
        "hazard": hazard,
        "inferred_hazard": inferred,
        "inferred_hazard_label": HAZARD_LABELS.get(inferred, "") if inferred else "",
        "primary": primary,
        "suggestions": suggestions,
        "count": len(items),
        "total": len(scored),
        "items": items,
        "review_status": data.get("review_status"),
        "disclaimer": data.get("disclaimer"),
    }


def _dedupe_entries(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for entry in items:
        entry_id = entry.get("id")
        if entry_id and entry_id not in seen:
            seen.add(entry_id)
            result.append(entry)
    return result


def checklist(query: str = "", hazard: str | None = None, limit: int = 20) -> dict:
    result = search(query=query, hazard=hazard, limit=limit)
    items: list[dict] = []
    index: dict[str, dict] = {}
    for entry in result["items"]:
        for text in entry.get("evidence", []):
            text = str(text).strip()
            if not text:
                continue
            if text not in index:
                index[text] = {"item": text, "sources": []}
                items.append(index[text])
            index[text]["sources"].append(entry.get("id"))
    result["checklist"] = items
    return result


def hazard_for_case(case: dict) -> tuple[str | None, str]:
    props = ((case.get("data_snapshot") or {}).get("properties") or {}) if isinstance(case.get("data_snapshot"), dict) else {}
    direct = props.get("dominant_hazard") or props.get("hazard")
    if direct in HAZARD_LABELS:
        return direct, HAZARD_LABELS[direct]
    alias = HAZARD_ALIASES.get(direct)
    if alias:
        return alias, HAZARD_LABELS[alias]
    text = props.get("dominant_class") or props.get("hazard_name") or ""
    code = CLASS_TO_HAZARD.get(str(text).strip())
    if code:
        return code, HAZARD_LABELS.get(code, str(text))
    return None, "未识别灾种"


def case_checklist(case: dict) -> dict:
    hazard, hazard_label = hazard_for_case(case)
    specific = search(hazard=hazard, limit=50)["items"] if hazard else []
    general = [entry for entry in entries() if entry.get("id") in GENERAL_IDS]
    selected = _dedupe_entries(specific + general)
    primary_id = HAZARD_PRIMARY.get(hazard or "")
    selected.sort(
        key=lambda entry: (
            0
            if entry.get("id") == primary_id
            else (2 if entry.get("id") in GENERAL_IDS else 1),
            str(entry.get("id")),
        )
    )
    primary_entry = next((entry for entry in selected if entry.get("id") == primary_id), None)
    primary = None
    if primary_entry:
        primary = {
            "id": primary_entry.get("id"),
            "law": primary_entry.get("law"),
            "article": primary_entry.get("article"),
            "heading": primary_entry.get("heading"),
            "match_reason": f"按案件灾种“{hazard_label}”推荐",
        }
    data = _load()
    items: list[dict] = []
    index: dict[str, dict] = {}
    for entry in selected:
        for text in entry.get("evidence", []):
            text = str(text).strip()
            if not text:
                continue
            if text not in index:
                index[text] = {"item": text, "sources": []}
                items.append(index[text])
            index[text]["sources"].append(entry.get("id"))
    evidence = case.get("evidence") or []
    existing = [
        {
            "id": item.get("id"),
            "filename": item.get("filename"),
            "mime": item.get("mime"),
            "captured_at": item.get("captured_at"),
        }
        for item in evidence
    ]
    note = (
        f"按案件快照识别为“{hazard_label}”，已列出对应法条与通用取证清单。"
        if hazard and specific
        else (
            f"按案件快照识别为“{hazard_label}”，草稿库暂无该灾种直接对应条文，以下为通用取证与移送清单。"
            if hazard
            else "未能从案件快照识别灾种，以下为通用取证与移送清单；请人工选择灾种后重新检索。"
        )
    )
    return {
        "case_id": case.get("id"),
        "case_no": case.get("case_no"),
        "hazard": hazard,
        "hazard_label": hazard_label,
        "primary": primary,
        "entries": selected,
        "checklist": items,
        "existing_evidence": existing,
        "note": note,
        "review_status": data.get("review_status"),
        "disclaimer": data.get("disclaimer"),
    }