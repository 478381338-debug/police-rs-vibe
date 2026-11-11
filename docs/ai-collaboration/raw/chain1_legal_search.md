
## USER  2026-11-11 17:30:20  (seq 141)

我输入关键字不出具体的法条这个问题解决一下，要求根据关键字推出对应主要法条


## ASSISTANT  2026-11-11 17:30:20  (seq 142)

我会先检查法条检索的匹配和排序逻辑，查清“占用农田地”为何没有命中非法占用农用地罪。


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && wc -l app/legal.py app/legal_api.py web/workspace.js && grep -n "legal\|法条" web/workspace.js | head -40`
- description: Locate legal search code and frontend usage
```
  259 app/legal.py
   46 app/legal_api.py
 1673 web/workspace.js
 1978 total
20:    legal: null,
446:      state.legal = null;
595:      <section class="section" id="legal-section">
596:        <div class="section-head"><h3>法条与取证清单</h3><span class="hint">草稿库 · 须法制审核</span></div>
598:          <div class="legal-controls">
599:            <label>关键词<input id="legal-q" type="search" maxlength="120" placeholder="如：非法采矿、倾倒、占用农用地"></label>
600:            <label>灾种<select id="legal-hazard"><option value="">全部灾种</option></select></label>
601:            <button class="btn btn-ghost btn-sm" type="button" data-action="legal-search">检索</button>
602:            <button class="btn btn-ghost btn-sm" type="button" data-action="legal-txt" disabled>下载清单 TXT</button>
604:          <div id="legal-result" class="legal-result">
605:            <p class="empty-small">正在按案件灾种加载法条与取证方向…</p>
784:    ["legal-section", "token-section", "audit-section"].forEach((id) => {
885:        if (action === "legal-search") searchLegal();
886:        if (action === "legal-txt") downloadLegalTxt(c);
1104:    const select = $("#legal-hazard");
1117:      return `<p class="empty-small">没有匹配的法条条目；请换关键词或灾种。</
... [truncated]
```


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && sed -n '1,220p' app/legal.py`
- description: Read legal search module
```
"""法条与证据清单检索。

数据是人工整理的草稿库，不是法律数据库：
- 每条都带 review_status=draft_requires_legal_review；
- 只做关键词/灾种检索和证据方向提示；
- 不自动给出构罪、处罚或移送结论。
"""
from __future__ import annotations

import json
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
    "固废":
... [truncated]
```


## ASSISTANT  2026-11-11 17:30:31  (seq 143)


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && cat app/legal_api.py && sed -n '1088,1205p' web/workspace.js`
- description: Read legal API and frontend legal rendering
```
"""法条与证据清单检索 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app import legal
from app.case_api import current_user

router = APIRouter(prefix="/api/legal", tags=["legal"])


@router.get("/meta")
def meta(user: dict = Depends(current_user)):
    return legal.meta()


@router.get("/hazards")
def hazards(user: dict = Depends(current_user)):
    return {"items": [{"code": code, "label": label} for code, label in legal.HAZARD_LABELS.items()]}


@router.get("/search")
def search(
    q: str = Query("", max_length=120),
    hazard: str = Query("", max_length=40),
    limit: int = Query(20, ge=1, le=50),
    user: dict = Depends(current_user),
):
    return legal.search(q, hazard or None, limit)


@router.get("/checklist")
def checklist(
    q: str = Query("", max_length=120),
    hazard: str = Query("", max_length=40),
    limit: int = Query(20, ge=1, le=50),
    user: dict = Depends(current_user),
):
    return legal.checklist(q, hazard or None, limit)


@router.get("/entry/{entry_id}")
def entry(entry_id: str, user: dict = Depends(current_user)):
    item = legal.get_entry(entry_id)
    if item is None:
        raise HTTPEx
... [truncated]
```


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && .venv/Scripts/python.exe - <<'PY'
import json
from pathlib import Path
kb=json.loads(Path('data/legal/knowledge_base.json').read_text(encoding='utf-8'))
print('entries', len(kb['entries']))
for e in kb['entries']:
    h=str(e.get('heading',''))
    if '农用地' in h or '342' in str(e.get('article','')) or '占用' in h or '采矿' in h:
        print('---')
        print(e.get('id'), '|', e.get('law'), e.get('article'), '|', h)
        print('hazards', e.get('hazards'))
        print('keywords', e.get('keywords'))
        print('summary', str(e.get('summary'))[:120])
PY`
- description: Inspect legal knowledge base entries for farmland and mining
```
entries 17
---
criminal-law-343 | 中华人民共和国刑法 第三百四十三条 | 非法采矿罪、破坏性采矿罪
hazards ['mining', 'mining_damage', 'bare_soil']
keywords ['非法采矿', '盗采', '采矿许可证', '矿产品', '破坏性采矿', '越界开采', '砂石', '黏土']
summary 未取得采矿许可证擅自采矿，擅自进入国家规划矿区、对国民经济具有重要价值的矿区和他人矿区范围采矿，或者擅自开采国家规定实行保护性开采的特定矿种，情节严重的；以及采取破坏性的开采方法开采矿产资源，造成矿产资源严重破坏的，可能构成非法采矿罪或破坏
---
criminal-law-342 | 中华人民共和国刑法 第三百四十二条 | 非法占用农用地罪
hazards ['construction', 'bare_soil', 'mining', 'road_intrusion', 'dumping']
keywords ['非法占用农用地', '耕地', '林地', '基本农田', '改变用途', '硬化', '取土', '堆场']
summary 违反土地管理法规，非法占用耕地、林地等农用地，改变被占用土地用途，数量较大，造成耕地、林地等农用地大量毁坏的，可能构成非法占用农用地罪。
---
judicial-mining-2016 | 最高人民法院、最高人民检察院关于办理非法采矿、破坏性采矿刑事案件适用法律若干问题的解释 法释〔2016〕25号 | 非法采矿、破坏性采矿刑事案件的法律适用
hazards ['mining', 'mining_damage']
keywords ['非法采矿', '司法解释', '情节严重', '矿产品价值', '破坏性采矿', '法释〔2016〕25号']
summary 该解释对非法采矿罪“情节严重”“情节特别严重”的认定、矿产品价值认定、破坏性采矿造成矿产资源破坏的价值认定、单位犯罪和行刑衔接等作出规定。具体案件应结合矿种、数量、价值、许可情况和鉴定意见逐项判断。
```


## ASSISTANT  2026-11-11 17:30:37  (seq 144)


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && .venv/Scripts/python.exe - <<'PY'
import json
from pathlib import Path
kb=json.loads(Path('data/legal/knowledge_base.json').read_text(encoding='utf-8'))
for e in kb['entries']:
    print(e['id'], '|', e.get('law'), e.get('article'), '|', e.get('heading'))
    print('  hazards:', ','.join(e.get('hazards',[])))
    print('  keywords:', ','.join(e.get('keywords',[])))
PY`
- description: List all legal knowledge base entries
```
criminal-law-338 | 中华人民共和国刑法 第三百三十八条 | 污染环境罪
  hazards: dumping,water_anomaly,mining,mining_damage
  keywords: 污染环境,排放,倾倒,有毒物质,暗管,渗坑,渗井,溶洞,危险废物,废水
criminal-law-343 | 中华人民共和国刑法 第三百四十三条 | 非法采矿罪、破坏性采矿罪
  hazards: mining,mining_damage,bare_soil
  keywords: 非法采矿,盗采,采矿许可证,矿产品,破坏性采矿,越界开采,砂石,黏土
criminal-law-342 | 中华人民共和国刑法 第三百四十二条 | 非法占用农用地罪
  hazards: construction,bare_soil,mining,road_intrusion,dumping
  keywords: 非法占用农用地,耕地,林地,基本农田,改变用途,硬化,取土,堆场
criminal-law-341 | 中华人民共和国刑法 第三百四十一条 | 危害珍贵、濒危野生动物罪；非法狩猎罪
  hazards: fire_scar,wetland,veg_degradation
  keywords: 野生动物,珍贵濒危,非法狩猎,禁猎区,禁猎期,猎夹,猎套,捕鸟网,电网
criminal-law-344 | 中华人民共和国刑法 第三百四十四条 | 危害国家重点保护植物罪
  hazards: veg_degradation,fire_scar,construction,mining
  keywords: 国家重点保护植物,珍贵树木,采伐,毁坏,收购,运输,加工,出售
criminal-law-345 | 中华人民共和国刑法 第三百四十五条 | 盗伐林木罪；滥伐林木罪
  hazards: veg_degradation,fire_scar,construction
  keywords: 盗伐,滥伐,林木,蓄积量,采伐许可证,原木,运输,收购
criminal-law-339 | 中华人民共和国刑法 第三百三十九条 | 非法处置进口的固体废物罪；擅自进口固体废物罪
  hazards: dumping,water_anomaly
  keywords: 固体废物,进口,洋垃圾,倾倒,堆放,处置,报关,原料
criminal-law-340 | 中华人民共和国刑法 第三百四十条 | 非法捕捞水产品罪
  hazards: water_anomaly,wetland
  keywords: 非法捕捞,禁渔区,禁渔期,电鱼,绝户网,地笼,渔获物
forest-law-39 | 中华人民共和国森林法 第三十九条 |
... [truncated]
```


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && sed -n '860,900p' web/workspace.js && grep -n "legal-q\|keydown\|addEventListener" web/workspace.js | head -30`
- description: Check legal search event bindings
```
  function renderAudit(a) {
    let detail = a.detail || "";
    if (detail.length > 120) detail = detail.slice(0, 120) + "…";
    return `<div class="audit-item">
      <span>${fmtTime(a.at)}</span>
      <b>${esc(a.actor_name || "—")}</b>
      <span>${esc(ACTION_LABELS[a.action] || a.action)}${detail ? ` · ${esc(detail)}` : ""}</span>
    </div>`;
  }

  function bindCaseEvents(c) {
    $$("[data-action]", $("#case-main")).forEach((btn) => {
      btn.addEventListener("click", () => {
        const action = btn.dataset.action;
        if (action === "refresh") selectCase(c.id);
        if (action === "archive") archiveCase(c);
        if (action === "task-status") updateTask(btn.dataset.task, btn.dataset.status);
        if (action === "verify-chain") verifyChain(c.id);
        if (action === "revoke-arrival") revokeArrival(c.id, btn.dataset.token);
        if (action === "arrival-circle") loadArrivalCircle(c);
        if (action === "arrival-geojson") downloadArrivalGeoJSON(c);
        if (action === "drone-plan") loadDronePlan(c);
        if (action === "drone-kml") downloadDroneFile(c, "kml");
        if (action === "drone-csv") downloadDroneFile(c, "csv");
        if (action
... [truncated]
```


## ASSISTANT  2026-11-11 17:30:49  (seq 145)


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && grep -n "legal-entry\|legal-banner\|legal-index\|legal-checklist\|legal-primary" web/workspace.css web/light-theme.css | head -40`
- description: Locate legal section styles
```
web/workspace.css:445:.legal-banner {
web/workspace.css:451:.legal-banner > div {
web/workspace.css:460:.legal-banner span {
web/workspace.css:464:.legal-banner b {
web/workspace.css:468:.legal-checklist {
web/workspace.css:475:.legal-checklist summary {
web/workspace.css:481:.legal-checklist ul {
web/workspace.css:495:.legal-entry {
web/workspace.css:501:.legal-entry-head {
web/workspace.css:506:.legal-index {
web/workspace.css:517:.legal-entry-head b {
web/workspace.css:522:.legal-entry-head span {
web/workspace.css:549:  .legal-banner {
web/light-theme.css:194:#app-view .legal-banner > div,#app-view .legal-checklist,#app-view .legal-entry{
web/light-theme.css:197:#app-view .legal-checklist summary,#app-view .legal-details summary{color:#206bc4}
web/light-theme.css:198:#app-view .legal-index{background:#e8f1fb;color:#206bc4}
```


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && grep -n "LEGAL_HAZARDS" web/workspace.js | head && sed -n '1,60p' web/workspace.js`
- description: Inspect workspace JS constants
```
55:  const LEGAL_HAZARDS = {
1106:    Object.entries(LEGAL_HAZARDS).forEach(([code, label]) => {
1139:      || (data.hazard ? (LEGAL_HAZARDS[data.hazard] || data.hazard) : "全部灾种");
/* 案件工作台：真实案件、任务、证据 SHA-256、复核、归档、报告。 */
(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const state = {
    user: null,
    cases: [],
    current: null,
    activeId: null,
    hotspots: null,
    grids: null,
    users: [],
    chart: null,
    imagery: null,
    imageryScenes: null,
    arrival: null,
    drone: null,
    legal: null,
    toastTimer: null,
  };

  const STATUS_ORDER = ["待派单", "核查中", "待复核", "已复核", "已归档"];
  const ACTION_LABELS = {
    create_case: "建案",
    create_task: "新增任务",
    update_task: "更新任务",
    upload_evidence: "上传证据",
    review_case: "提交复核",
    archive_case: "归档",
    export_offline_pack: "导出离线包",
    login: "登录",
    logout: "退出",
    change_password: "修改密码",
    create_user: "新增用户",
    issue_arrival_token: "生成到场码",
    revoke_arrival_token: "作废到场码",
    checkin: "现场签到",
    verification: "录入核查结论",
    verify_evidence_chain: "校验证据链",
    downloa
... [truncated]
```


## ASSISTANT  2026-11-11 17:31:17  (seq 146)


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && sed -n '96,180p' app/legal.py && sed -n '180,259p' app/legal.py`
- description: Read scoring, search and case checklist implementation
```
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


def _score(entry: dict, query: str) -> int:
    if not query:
        return 1
    q = query.strip().lower()
    if not q:
        return 1
    terms = [term for term in q.replace("，", " ").replace(",", " ").split() if term]
    if not terms:
        terms = [q]
    score = 0
    heading = str(entry.get("heading", "")).lower()
    law = str(entry.get("law", "")).lower()
    article = str(entry.get("article", "")).lower()
    summary = str(entry.get("summary", "")).lower()
    keywords = " ".join(str(item) for item in entry.get("keywords", [])).lower()
    for term in terms:
        if term in heading:
            score += 8
        if term in keywords:
            score += 6
        if term in law:
            score += 
... [truncated]
```


## ASSISTANT  2026-11-11 17:32:01  (seq 147)


### TOOL `Edit` [error]
- file: `E:\警务遥感\app\legal.py`
- old:
```
from __future__ import annotations

import json
import threading
from pathlib import Path
```
- new:
```
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
```


### TOOL `Edit` [error]
- file: `E:\警务遥感\app\legal.py`
- old:
```
GENERAL_IDS = [
    "criminal-procedure-law-50",
    "criminal-procedure-law-128-141",
    "administrative-penalty-law-27",
]
```
- new:
```
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
# 规则用于把“占用农田地”这类口语说法归到具体罪名，不替代法律适用判断。
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
        "
... [truncated]
```


## ASSISTANT  2026-11-11 17:32:19  (seq 148)


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\app\\legal.py"}
```


## ASSISTANT  2026-11-11 17:32:23  (seq 149)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\app\legal.py`
- old:
```
import json
import threading
from pathlib import Path
```
- new:
```
import json
import re
import threading
from pathlib import Path
```


### TOOL `Edit` [completed]
- file: `E:\警务遥感\app\legal.py`
- old:
```
GENERAL_IDS = [
    "criminal-procedure-law-50",
    "criminal-procedure-law-128-141",
    "administrative-penalty-law-27",
]
```
- new:
```
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
        "重点
... [truncated]
```


## ASSISTANT  2026-11-11 17:32:34  (seq 150)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\app\legal.py`
- old:
```
def _score(entry: dict, query: str) -> int:
    if not query:
        return 1
    q = query.strip().lower()
    if not q:
        return 1
    terms = [term for term in q.replace("，", " ").replace(",", " ").split() if term]
    if not terms:
        terms = [q]
    score = 0
    heading = str(entry.get("heading", "")).lower()
    law = str(entry.get("law", "")).lower()
    article = str(entry.get("article", "")).lower()
    summary = str(entry.get("summary", "")).lower()
    keywords = " ".join(str(item) for item in entry.get("keywords", [])).lower()
    for term in terms:
        if term in heading:
            score += 8
        if term in keywords:
            score += 6
        if term in law:
            score += 4
        if term in article:
            score += 3
        if term in summary:
            score += 2
    return score


def search(query: str = "", hazard: str | None = None, limit: int = 20) -> dict:
    limit = max(1, min(int(limit or 20), 50))
    scored = []
    for entry in entries():
        if hazard and hazard not in entry.get("hazards", []):
            continue
        score = _score(entry, query)
        if query and score <= 1:
            continue
   
... [truncated]
```
- new:
```
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
    keywords = [_norm(item) for i
... [truncated]
```


## ASSISTANT  2026-11-11 17:33:00  (seq 152)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\app\legal.py`
- old:
```
def case_checklist(case: dict) -> dict:
    hazard, hazard_label = hazard_for_case(case)
    specific = search(hazard=hazard, limit=50)["items"] if hazard else []
    general = [entry for entry in entries() if entry.get("id") in GENERAL_IDS]
    selected = _dedupe_entries(specific + general)
    selected.sort(key=lambda entry: (1 if entry.get("id") in GENERAL_IDS else 0, str(entry.get("id"))))
    data = _load()
```
- new:
```
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
```


### TOOL `Edit` [completed]
- file: `E:\警务遥感\app\legal.py`
- old:
```
return {
        "case_id": case.get("id"),
        "case_no": case.get("case_no"),
        "hazard": hazard,
        "hazard_label": hazard_label,
        "entries": selected,
```
- new:
```
return {
        "case_id": case.get("id"),
        "case_no": case.get("case_no"),
        "hazard": hazard,
        "hazard_label": hazard_label,
        "primary": primary,
        "entries": selected,
```


## ASSISTANT  2026-11-11 17:33:08  (seq 153)


### TOOL `Bash` [completed]
- command: `cd "E:/警务遥感" && .venv/Scripts/python.exe - <<'PY'
from app import legal
checks = [
    ("占用农田地", "criminal-law-342"),
    ("非法占用农用地", "criminal-law-342"),
    ("占用耕地建房", "criminal-law-342"),
    ("非法采矿", "criminal-law-343"),
    ("盗采砂石", "criminal-law-343"),
    ("倾倒危险废物", "criminal-law-338"),
    ("砍树", "criminal-law-345"),
    ("电鱼", "criminal-law-340"),
    ("非法狩猎", "criminal-law-341"),
]
for q, expected in checks:
    r = legal.search(q, None, 10)
    p = r["primary"]
    ids = [i["id"] for i in r["items"]]
    ok = bool(p) and p["id"] == expected
    print(("OK " if ok else "FAIL"), q, "->", (p or {}).get("id"), (p or {}).get("heading"), "| items:", ids[:4], "| inferred:", r["inferred_hazard"])
    if p: print("    reason:", p.get("match_reason"))
r = legal.search("占用农田地", None, 10)
print("count/total:", r["count"], r["total"], "suggestions:", r["suggestions"])
r2 = legal.search("随便说个不存在的", None, 10)
print("gibberish:", r2["count"], r2["suggestions"])
PY`
- description: Test keyword-to-primary-law inference
```
OK  占用农田地 -> criminal-law-342 非法占用农用地罪 | items: ['criminal-law-342'] | inferred: bare_soil
    reason: 按“农用地占用”规则命中：占用农田、农田地、农田
OK  非法占用农用地 -> criminal-law-342 非法占用农用地罪 | items: ['criminal-law-342'] | inferred: bare_soil
    reason: 按“农用地占用”规则命中：非法占用农用地、占用农用地
OK  占用耕地建房 -> criminal-law-342 非法占用农用地罪 | items: ['criminal-law-342'] | inferred: bare_soil
    reason: 按“农用地占用”规则命中：耕地
OK  非法采矿 -> criminal-law-343 非法采矿罪、破坏性采矿罪 | items: ['criminal-law-343', 'judicial-mining-2016', 'mineral-resource-law'] | inferred: mining
    reason: 按“矿产资源开采”规则命中：非法采矿、采矿
OK  盗采砂石 -> criminal-law-343 非法采矿罪、破坏性采矿罪 | items: ['criminal-law-343', 'judicial-mining-2016', 'forest-law-39', 'mineral-resource-law'] | inferred: mining
    reason: 按“矿产资源开采”规则命中：盗采、采砂、砂石
OK  倾倒危险废物 -> criminal-law-338 污染环境罪 | items: ['criminal-law-338', 'judicial-environment-2023'] | inferred: dumping
    reason: 按“污染环境”规则命中：倾倒、危险废物
OK  砍树 -> criminal-law-345 盗伐林木罪；滥伐林木罪 | items: ['criminal-law-345', 'criminal-law-344'] | inferred: veg_degradation
    reason: 按“林木采伐”规则命中：砍树
OK  电鱼 -> criminal-law-340 非法捕捞水产品罪 | items: ['criminal-law-340'] | inferred: water_anomaly
    reason: 按“水产品捕捞”规则命中：电鱼
OK  非法狩猎 -> criminal-law-341
... [truncated]
```


## ASSISTANT  2026-11-11 17:33:15  (seq 154)


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\web\\light-theme.css", "limit": 28, "offset": 186}
```


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\web\\workspace.css", "limit": 40, "offset": 493}
```


## ASSISTANT  2026-11-11 17:33:31  (seq 155)


### TOOL `Edit` [error]
- file: `E:\警务遥感\web\workspace.js`
- old:
```
function renderLegalEntries(entries) {
    if (!entries || !entries.length) {
      return `<p class="empty-small">没有匹配的法条条目；请换关键词或灾种。</p>`;
    }
    return entries.map((entry, index) => {
      const evidence = (entry.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const notes = (entry.notes || []).map((item) => `<li>${esc(item)}</li>`).join("");
      return `<article class="legal-entry">
        <div class="legal-entry-head">
          <span class="legal-index">${index + 1}</span>
          <div><b>${esc(entry.law || "")} ${esc(entry.article || "")}</b><span>${esc(entry.heading || "")}</span></div>
        </div>
        <p class="legal-summary-text">${esc(entry.summary || "")}</p>
        ${evidence ? `<details class="legal-details"><summary>取证方向（${(entry.evidence || []).length} 项）</summary><ul>${evidence}</ul></details>` : ""}
        ${notes ? `<details class="legal-details"><summary>适用提示</summary><ul>${notes}</ul></details>` : ""}
      </article>`;
    }).join("");
  }

  function renderLegal(data, mode) {
    const entries = data.entries || data.items || [];
    const checklist = data.checklist || [];
    const existing = data.existing_evidence || [
... [truncated]
```
- new:
```
function renderLegalEntries(entries, primary, suggestions) {
    if (!entries || !entries.length) {
      const tips = (suggestions || [])
        .map((item) => `${esc(item.article || "")} ${esc(item.heading || "")}`.trim())
        .filter(Boolean)
        .join("、");
      return `<p class="empty-small">没有匹配的法条条目；${tips ? `相近条目：${tips}。` : "请换关键词或灾种。"}</p>`;
    }
    const primaryId = primary && primary.id;
    return entries.map((entry, index) => {
      const evidence = (entry.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const notes = (entry.notes || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const isPrimary = primaryId && entry.id === primaryId;
      return `<article class="legal-entry${isPrimary ? " primary" : ""}">
        <div class="legal-entry-head">
          <span class="legal-index">${index + 1}</span>
          <div><b>${esc(entry.law || "")} ${esc(entry.article || "")}</b><span>${esc(entry.heading || "")}</span>${isPrimary ? `<em class="legal-primary-badge">主要适用</em>` : ""}</div>
        </div>
        ${entry.match_reason ? `<p class="legal-match">${esc(entry.match_reason)}</p>` : ""}
        <p class="legal-summary-text">${esc(entry.summary || "")}</p>
        ${evidence ? `<details class="legal-details"><summary>取证方向（${(entry.evidence || []).length} 项）</summary><ul>${evidence}</ul></details>` : ""}
        ${notes ? `<details class="legal-details"><summary>适用提示</summary><ul>${notes}</ul></details>` : ""}
      </article>`;
    }).join("");
  }

  function renderLegal(data, mode) {
    const entries = data.entries || data.items || [];
    const checklist = data.checklist || [];
    const existing = data.existing_evidence || [];
    const primary = data.primary || null;
    const hazardLabel = data.inferred_ha
... [truncated]
```


## ASSISTANT  2026-11-11 17:33:47  (seq 156)


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\web\\workspace.js", "limit": 130, "offset": 1080}
```


## ASSISTANT  2026-11-11 17:33:51  (seq 157)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\web\workspace.js`
- old:
```
function renderLegalEntries(entries) {
    if (!entries || !entries.length) {
      return `<p class="empty-small">没有匹配的法条条目；请换关键词或灾种。</p>`;
    }
    return entries.map((entry, index) => {
      const evidence = (entry.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const notes = (entry.notes || []).map((item) => `<li>${esc(item)}</li>`).join("");
      return `<article class="legal-entry">
        <div class="legal-entry-head">
          <span class="legal-index">${index + 1}</span>
          <div><b>${esc(entry.law || "")} ${esc(entry.article || "")}</b><span>${esc(entry.heading || "")}</span></div>
        </div>
        <p class="legal-summary-text">${esc(entry.summary || "")}</p>
        ${evidence ? `<details class="legal-details"><summary>取证方向（${(entry.evidence || []).length} 项）</summary><ul>${evidence}</ul></details>` : ""}
        ${notes ? `<details class="legal-details"><summary>适用提示</summary><ul>${notes}</ul></details>` : ""}
      </article>`;
    }).join("");
  }

  function renderLegal(data, mode) {
    const entries = data.entries || data.items || [];
    const checklist = data.checklist || [];
    const existing = data.existing_evidence || [
... [truncated]
```
- new:
```
function renderLegalEntries(entries, primary, suggestions) {
    if (!entries || !entries.length) {
      const tips = (suggestions || [])
        .map((item) => `${esc(item.article || "")} ${esc(item.heading || "")}`.trim())
        .filter(Boolean)
        .join("、");
      return `<p class="empty-small">没有匹配的法条条目；${tips ? `相近条目：${tips}。` : "请换关键词或灾种。"}</p>`;
    }
    const primaryId = primary && primary.id;
    return entries.map((entry, index) => {
      const evidence = (entry.evidence || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const notes = (entry.notes || []).map((item) => `<li>${esc(item)}</li>`).join("");
      const isPrimary = primaryId && entry.id === primaryId;
      return `<article class="legal-entry${isPrimary ? " primary" : ""}">
        <div class="legal-entry-head">
          <span class="legal-index">${index + 1}</span>
          <div><b>${esc(entry.law || "")} ${esc(entry.article || "")}</b><span>${esc(entry.heading || "")}</span>${isPrimary ? `<em class="legal-primary-badge">主要适用</em>` : ""}</div>
        </div>
        ${entry.match_reason ? `<p class="legal-match">${esc(entry.match_reason)}</p>` : ""}
        <p class="legal-summary-text">${esc(entry.summary || "")}</p>
        ${evidence ? `<details class="legal-details"><summary>取证方向（${(entry.evidence || []).length} 项）</summary><ul>${evidence}</ul></details>` : ""}
        ${notes ? `<details class="legal-details"><summary>适用提示</summary><ul>${notes}</ul></details>` : ""}
      </article>`;
    }).join("");
  }

  function renderLegal(data, mode) {
    const entries = data.entries || data.items || [];
    const checklist = data.checklist || [];
    const existing = data.existing_evidence || [];
    const primary = data.primary || null;
    const hazardLabel = data.inferred_ha
... [truncated]
```


## ASSISTANT  2026-11-11 17:34:05  (seq 158)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\web\workspace.js`
- old:
```
async function searchLegal() {
    const query = $("#legal-q").value.trim();
    const hazard = $("#legal-hazard").value;
    const button = $('[data-action="legal-search"]');
    button.disabled = true;
    button.textContent = "检索中…";
    try {
      const data = await api(`/api/legal/search?q=${encodeURIComponent(query)}&hazard=${encodeURIComponent(hazard)}&limit=30`);
      $("#legal-result").innerHTML = renderLegal(data, "search");
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      button.textContent = "检索";
    }
  }
```
- new:
```
let legalSeq = 0;

  async function searchLegal(c) {
    const input = $("#legal-q");
    const select = $("#legal-hazard");
    const query = input ? input.value.trim() : "";
    const hazard = select ? select.value : "";
    if (!query && !hazard) {
      if (c) loadLegalChecklist(c);
      return;
    }
    const seq = ++legalSeq;
    const button = $('[data-action="legal-search"]');
    if (button) {
      button.disabled = true;
      button.textContent = "检索中…";
    }
    try {
      const data = await api(`/api/legal/search?q=${encodeURIComponent(query)}&hazard=${encodeURIComponent(hazard)}&limit=30`);
      if (seq !== legalSeq) return;
      $("#legal-result").innerHTML = renderLegal(data, "search");
    } catch (err) {
      if (seq === legalSeq) toast(err.message, true);
    } finally {
      if (seq === legalSeq && button) {
        button.disabled = false;
        button.textContent = "检索";
      }
    }
  }
```


### TOOL `Edit` [completed]
- file: `E:\警务遥感\web\workspace.js`
- old:
```
if (action === "legal-search") searchLegal();
```
- new:
```
if (action === "legal-search") searchLegal(c);
```


## ASSISTANT  2026-11-11 17:34:16  (seq 159)


### TOOL `mcp__bili__compress` [completed]
```json
{"content": "m00361–m00394 上一轮收尾与远程验证\n本地回归：health=200；口令登录 303；/、/workspace、/operations、/static/light-theme.css、/static/gate-exit.js 均 200；案件登录 200；案件列表返回原始数组，字段是 `id`（不是 case_id），CASE-DD0CCBFD86 的二维码 200 image/png 1785 字节、333×333 RGB；logout 后 / 为 401。本地临时 8093 服务已停止，远程 police-web active、health 200。\n文档：README.md 4.6 增加 POLICE_GATE_MAX_FAILS=5、POLICE_GATE_LOCK_MINUTES=15，说明 5 次/15 分钟锁定、429+Retry-After、进程内存计数、/__gate/status 与三处“退出访问”。docs/部署与安全.md 第 2 节新增两行环境变量，第 3 节限流列表增加口令失败计数条目。docs/实施记录.md 第 8 节（8.1–8.6）已写入，7.6/7.7 已回填。\nTodoWrite 五项全部 completed；上一轮优化任务（二维码、口令锁定、浅色拆分、1366 验收、文档部署）结束。\n\nm00396–m00547 法条关键词检索需求与后端实现\n用户请求（m00505，原文）：“我输入关键字不出具体的法条这个问题解决一下，要求根据关键字推出对应主要法条”。附截图：工作台“法条与取证清单”，关键词输入“占用农田地”，灾种“全部灾种”，识别灾种“盗采/非法采矿”，匹配法条 13 条、取证方向 83 项；列表第 1 条刑法第三百三十八条污染环境罪，第 2 条第三百四十二条非法占用农用地罪。
```


## ASSISTANT  2026-11-11 17:34:43  (seq 160)


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\web\\workspace.js", "offset": 860, "limit": 80}
```


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\web\\workspace.css", "offset": 470, "limit": 60}
```


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\web\\light-theme.css", "offset": 175, "limit": 45}
```


## ASSISTANT  2026-11-11 17:34:47  (seq 161)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\web\workspace.js`
- old:
```
loadArrivalTokens(c.id);
    fillLegalHazards();
    loadLegalChecklist(c);
```
- new:
```
loadArrivalTokens(c.id);
    fillLegalHazards();
    const legalInput = $("#legal-q");
    if (legalInput && !legalInput.dataset.bound) {
      legalInput.dataset.bound = "1";
      legalInput.addEventListener("keydown", (ev) => {
        if (ev.key === "Enter") {
          ev.preventDefault();
          searchLegal(c);
        }
      });
      let legalTimer = 0;
      legalInput.addEventListener("input", () => {
        window.clearTimeout(legalTimer);
        if (!legalInput.value.trim()) return;
        legalTimer = window.setTimeout(() => searchLegal(c), 350);
      });
      const legalHazard = $("#legal-hazard");
      if (legalHazard) {
        legalHazard.addEventListener("change", () => {
          if (legalInput.value.trim()) searchLegal(c);
        });
      }
    }
    loadLegalChecklist(c);
```


### TOOL `Edit` [completed]
- file: `E:\警务遥感\web\workspace.css`
- old:
```
.legal-entry {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #0a141d;
  padding: 10px 12px;
}
```
- new:
```
.legal-entry {
  border: 1px solid var(--border);
  border-radius: 10px;
  background: #0a141d;
  padding: 10px 12px;
}
.legal-entry.primary {
  border-color: rgba(34, 211, 238, 0.55);
  background: #0b1a24;
  box-shadow: inset 0 0 0 1px rgba(34, 211, 238, 0.18);
}
.legal-primary-badge {
  display: inline-block;
  margin-left: 6px;
  padding: 1px 6px;
  border: 1px solid rgba(34, 211, 238, 0.45);
  border-radius: 999px;
  background: rgba(34, 211, 238, 0.12);
  color: var(--accent2);
  font-size: 10px;
  font-style: normal;
  vertical-align: 2px;
}
.legal-match {
  margin: 6px 0 0;
  padding-left: 9px;
  border-left: 2px solid rgba(34, 211, 238, 0.45);
  font-size: 11px;
  line-height: 1.6;
  color: var(--accent2);
}
.legal-primary {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  border: 1px solid rgba(34, 211, 238, 0.35);
  border-radius: 10px;
  background: rgba(34, 211, 238, 0.07);
  padding: 10px 12px;
}
.legal-primary-tag {
  flex: 0 0 auto;
  padding: 2px 7px;
  border-radius: 999px;
  background: rgba(34, 211, 238, 0.15);
  color: var(--accent2);
  font-size: 10px;
  font-weight: 700;
}
.legal-primary b { display: block; font-size: 13px; }
.legal-primary span { display: block; margin-top: 2px; color: var(--text); font-size: 12px; }
.legal-primary em {
  display: block;
  margin-top: 4px;
  color: var(--muted);
  font-size: 11px;
  font-style: normal;
}
```


### TOOL `Edit` [completed]
- file: `E:\警务遥感\web\light-theme.css`
- old:
```
#app-view .legal-index{background:#e8f1fb;color:#206bc4}
```
- new:
```
#app-view .legal-index{background:#e8f1fb;color:#206bc4}
#app-view .legal-entry.primary{
  border-color:#206bc4;background:#f3f8ff;
  box-shadow:inset 0 0 0 1px rgba(32,107,196,.16);
}
#app-view .legal-primary{background:#f3f8ff;border-color:#d6e4f5}
#app-view .legal-primary-tag{background:#e8f1fb;color:#206bc4}
#app-view .legal-primary b{color:#182433}
#app-view .legal-primary span{color:#344054}
#app-view .legal-primary em{color:#66758a}
#app-view .legal-primary-badge{border-color:#206bc4;background:#e8f1fb;color:#206bc4}
#app-view .legal-match{border-left-color:#206bc4;color:#206bc4}
```


## ASSISTANT  2026-11-11 17:35:01  (seq 163)


### TOOL `TodoWrite` [completed]
```json
{"todos": [{"content": "后端法条打分与主要法条推断（app/legal.py）", "status": "completed", "priority": "high"}, {"content": "前端主要法条展示与检索交互（web/workspace.js）", "status": "completed", "priority": "high"}, {"content": "主要法条样式（深色 + 浅色主题）", "status": "completed", "priority": "high"}, {"content": "本地语法检查与 1366×768 Playwright 验证“占用农田地”→342", "status": "in_progress", "priority": "high"}, {"content": "部署远程、接口验证并更新实施记录", "status": "pending", "priority": "high"}]}
```


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && node --check web/workspace.js && .venv/Scripts/python.exe -m compileall -q app && echo CHECKS_OK`
- description: Check JS syntax and compile Python app
```
CHECKS_OK
```


## ASSISTANT  2026-11-11 17:35:08  (seq 164)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && grep -n "getenv\|environ" app/main.py | head -40 && grep -rn "DEMO" app/config.py app/main.py 2>/dev/null | head -20`
- description: Inspect environment variable names for demo mode
```
43:APP_VERSION = os.getenv("POLICE_VERSION", "1.1.0")
98:_docs_disabled = os.getenv("POLICE_DISABLE_DOCS", "0").strip().lower() in {"1", "true", "yes", "on"}
116:    for item in os.getenv("POLICE_CORS_ORIGINS", "").split(",")
160:_WEB_USER = os.getenv("POLICE_WEB_USER", "").strip()
161:_WEB_PASSWORD = os.getenv("POLICE_WEB_PASSWORD", "")
163:_GATE_TTL = max(600, int(os.getenv("POLICE_GATE_HOURS", "12")) * 3600)
164:_GATE_MAX_FAILS = max(1, int(os.getenv("POLICE_GATE_MAX_FAILS", "5")))
165:_GATE_LOCK_SECONDS = max(60, int(float(os.getenv("POLICE_GATE_LOCK_MINUTES", "15")) * 60))
595:        "demo_mode": os.getenv("POLICE_DEMO_MODE", "1").strip().lower() not in {"0", "false", "no", "off"},
app/main.py:595:        "demo_mode": os.getenv("POLICE_DEMO_MODE", "1").strip().lower() not in {"0", "false", "no", "off"},
```


## ASSISTANT  2026-11-11 17:35:12  (seq 165)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && POLICE_WEB_USER=demo POLICE_WEB_PASSWORD=Test@12345 .venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8093 --log-level warning`
- description: Start local test server on port 8093
```
Command running in background with ID: exec_2ae925bd-6c06-41a5-b1a4-177f2d1b934b. Output is being written to: C:\Users\lenovo\AppData\Local\Temp\zcode-bash-1ncE2d\session_c7acdf13bcc033a4b1895a63813d3821\call_36c1153f2aed49db9bd072ca-stdout.log. You will be notified when it completes. To check interim output, use Read on that file path.
```


## ASSISTANT  2026-11-11 17:35:16  (seq 166)


### TOOL `Bash` [completed]
- command: `sleep 3; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8093/api/health; curl -s "http://127.0.0.1:8093/api/legal/search?q=%E5%8D%A0%E7%94%A8%E5%86%9C%E7%94%B0%E5%9C%B0" | head -c 600`
- description: Check health and legal search API response
```
200
{"detail":"需要访问口令"}
```


## ASSISTANT  2026-11-11 17:35:23  (seq 167)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && J=/tmp/legal_cookies.txt && rm -f "$J" && curl -s -c "$J" -o /dev/null -w "gate:%{http_code}\n" -X POST -d "username=demo&password=Test@12345&next=/" http://127.0.0.1:8093/__gate && curl -s -b "$J" -c "$J" -H "Content-Type: application/json" -d '{"username":"admin","password":"Admin@12345"}' -o /tmp/login.json -w "login:%{http_code}\n" http://127.0.0.1:8093/api/auth/login && cat /tmp/login.json && echo && curl -s -b "$J" "http://127.0.0.1:8093/api/legal/search?q=%E5%8D%A0%E7%94%A8%E5%86%9C%E7%94%B0%E5%9C%B0" -o /tmp/legal.json -w "legal:%{http_code}\n" && .venv/Scripts/python.exe -c "import json;d=json.load(open('/tmp/legal.json',encoding='utf-8'));print('inferred:',d.get('inferred_hazard'),d.get('inferred_hazard_label'));print('primary:',d.get('primary'));print('items:',[(e['id'],e.get('article'),e.get('heading'),e.get('score')) for e in (d.get('items') or d.get('entries') or [])[:4]])"`
- description: Log in through gate and case API, verify legal search primary result
```
Exit code 1
gate:303
login:200
{"ok":true,"user":{"id":1,"username":"admin","display_name":"系统管理员","role":"admin","role_label":"系统管理员","active":true},"expires_at":"2026-11-11T21:35:31+00:00"}
legal:200
Traceback (most recent call last):
  File "<string>", line 1, in <module>
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/legal.json'
```


## ASSISTANT  2026-11-11 17:35:31  (seq 168)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && cat /tmp/legal.json | .venv/Scripts/python.exe -c "import sys,json;d=json.load(sys.stdin);print('inferred:',d.get('inferred_hazard'),d.get('inferred_hazard_label'));print('primary:',d.get('primary'));print('items:',[(e.get('id'),e.get('article'),e.get('heading'),e.get('score'),e.get('match_reason')) for e in (d.get('items') or d.get('entries') or [])[:4]])"`
- description: Parse legal search response from stdin
```
inferred: bare_soil 裸土/农用地
primary: {'id': 'criminal-law-342', 'law': '中华人民共和国刑法', 'article': '第三百四十二条', 'heading': '非法占用农用地罪', 'score': 103, 'match_reason': '按“农用地占用”规则命中：占用农田、农田地、农田', 'matched_terms': ['占用农田', '农田地', '农田', '农用地', '耕地', '非法占用农用地']}
items: [('criminal-law-342', '第三百四十二条', '非法占用农用地罪', 103, '按“农用地占用”规则命中：占用农田、农田地、农田')]
```


## ASSISTANT  2026-11-11 17:35:36  (seq 169)


### TOOL `mcp__playwright__browser_navigate` [completed]
```json
{"url": "http://127.0.0.1:8093/__gate"}
```


## ASSISTANT  2026-11-11 17:35:43  (seq 170)


### TOOL `mcp__playwright__browser_evaluate` [completed]
```json
{"function": "async () => {\n  const res = await fetch('/__gate', {\n    method: 'POST',\n    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },\n    body: 'username=demo&password=Test%4012345&next=%2Fworkspace',\n    redirect: 'follow',\n  });\n  return { status: res.status, url: res.url };\n}"}
```


## ASSISTANT  2026-11-11 17:35:52  (seq 171)


### TOOL `mcp__playwright__browser_resize` [completed]
```json
{"width": 1366, "height": 768}
```


### TOOL `mcp__playwright__browser_navigate` [completed]
```json
{"url": "http://127.0.0.1:8093/workspace"}
```


## ASSISTANT  2026-11-11 17:35:56  (seq 172)


### TOOL `mcp__playwright__browser_evaluate` [completed]
```json
{"function": "async () => {\n  const res = await fetch('/api/auth/login', {\n    method: 'POST',\n    headers: { 'Content-Type': 'application/json' },\n    body: JSON.stringify({ username: 'admin', password: 'Admin@12345' }),\n  });\n  return { status: res.status, body: (await res.text()).slice(0, 200) };\n}"}
```


## ASSISTANT  2026-11-11 17:36:00  (seq 173)


### TOOL `mcp__playwright__browser_navigate` [completed]
```json
{"url": "http://127.0.0.1:8093/workspace"}
```


## ASSISTANT  2026-11-11 17:36:04  (seq 175)


### TOOL `mcp__playwright__browser_evaluate` [completed]
```json
{"function": "async () => {\n  const wait = (ms) => new Promise((r) => setTimeout(r, ms));\n  for (let i = 0; i < 40 && !document.querySelector('#legal-q'); i += 1) await wait(250);\n  const input = document.querySelector('#legal-q');\n  if (!input) return { error: 'no legal input', body: document.body.innerText.slice(0, 400) };\n  input.scrollIntoView({ block: 'center' });\n  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;\n  setter.call(input, '占用农田地');\n  input.dispatchEvent(new Event('input', { bubbles: true }));\n  await wait(1800);\n  const res = document.querySelector('#legal-result');\n  const first = res && res.querySelector('.legal-entry');\n  const banner = res && res.querySelector('.legal-banner');\n  const primaryBlock = res && res.quer
```


## ASSISTANT  2026-11-11 17:36:13  (seq 176)


### TOOL `mcp__playwright__browser_evaluate` [completed]
```json
{"function": "() => {\n  const card = document.querySelector('#legal-result .legal-entry.primary');\n  const primary = document.querySelector('#legal-result .legal-primary');\n  const badge = document.querySelector('#legal-result .legal-primary-badge');\n  const cs = card ? getComputedStyle(card) : null;\n  return {\n    cardBorder: cs ? cs.borderTopColor : null,\n    cardBg: cs ? cs.backgroundColor : null,\n    primaryBg: primary ? getComputedStyle(primary).backgroundColor : null,\n    badgeColor: badge ? getComputedStyle(badge).color : null,\n    lightTheme: !!document.querySelector('link[href*=\"light-theme.css\"]'),\n  };\n}"}
```


### TOOL `mcp__playwright__browser_take_screenshot` [completed]
```json
{"filename": "legal-1366.png", "fullPage": true, "scale": "css"}
```


## ASSISTANT  2026-11-11 17:36:19  (seq 177)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && ls -1t legal-1366.png .playwright-mcp/legal-1366.png 2>/dev/null | head -5`
- description: Locate saved screenshot file
```
(Bash completed with no output)
```


## ASSISTANT  2026-11-11 17:36:23  (seq 178)


### TOOL `Bash` [completed]
- command: `ls -1t "/d/ZCODE插件/playwright-mcp/" | head -5`
- description: List recent Playwright output files
```
legal-1366.png
accept-operations-1366-v2.png
accept-operations-1366.png
accept-workspace-1366.png
accept-index-1366-v2.png
```


## ASSISTANT  2026-11-11 17:36:26  (seq 179)


### TOOL `Read` [completed]
```json
{"file_path": "D:\\ZCODE插件\\playwright-mcp\\legal-1366.png"}
```


## ASSISTANT  2026-11-11 17:36:29  (seq 180)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && scp -i ~/.ssh/police_deploy_ed25519 -o StrictHostKeyChecking=no app/legal.py admin@47.95.141.225:/opt/police/app/legal.py && scp -i ~/.ssh/police_deploy_ed25519 web/workspace.js web/workspace.css web/light-theme.css admin@47.95.141.225:/opt/police/web/ && echo SCP_OK`
- description: Copy legal module and frontend assets to remote server
```
SCP_OK
```


## ASSISTANT  2026-11-11 17:36:40  (seq 181)


### TOOL `Bash` [completed]
- command: `ssh -i ~/.ssh/police_deploy_ed25519 -o StrictHostKeyChecking=no admin@47.95.141.225 'bash -s' <<'EOF'
set -e
sudo systemctl restart police-web
sleep 2
curl -s -o /dev/null -w 'health:%{http_code}\n' http://127.0.0.1:8080/api/health
U=$(sudo grep -E '^POLICE_WEB_USER=' /etc/police-web.env | cut -d= -f2-)
P=$(sudo grep -E '^POLICE_WEB_PASSWORD=' /etc/police-web.env | cut -d= -f2-)
J=/tmp/remote_legal_cookies.txt
rm -f "$J"
curl -s -c "$J" -o /dev/null -w 'gate:%{http_code}\n' -X POST --data-urlencode "username=$U" --data-urlencode "password=$P" --data-urlencode "next=/" http://127.0.0.1:8080/__gate
curl -s -b "$J" -c "$J" -H 'Content-Type: application/json' -d '{"username":"admin","password":"Admin@12345"}' -o /dev/null -w 'login:%{http_code}\n' http://127.0.0.1:8080/api/auth/login
curl -s -b "$J" "http://127.0.0.1:8080/api/legal/search?q=%E5%8D%A0%E7%94%A8%E5%86%9C%E7%94%B0%E5%9C%B0" -o /tmp/remote_legal.json -w 'legal:%{http_code}\n'
python3 - <<'PY'
import json
d=json.load(open('/tmp/remote_legal.json',encoding='utf-8'))
p=d.get('primary') or {}
print('inferred:', d.get('inferred_hazard'), p.get('id'), p.get('article'), p.get('heading'))
print('items:', [(e.get('id'), e.get('article'), e.get('score')) for e in (d.get('items') or [])[:3]])
PY
rm -f "$J" /tmp/remote_legal.json
EOF`
- description: Restart remote service and verify legal search primary result
```
health:200
gate:303
login:200
legal:200
inferred: bare_soil criminal-law-342 第三百四十二条 非法占用农用地罪
items: [('criminal-law-342', '第三百四十二条', 103)]
```


## ASSISTANT  2026-11-11 17:36:54  (seq 182)


### TOOL `Bash` [completed]
- command: `ssh -i ~/.ssh/police_deploy_ed25519 -o StrictHostKeyChecking=no admin@47.95.141.225 "echo -n 'js_badge:'; curl -s http://127.0.0.1:8080/static/workspace.js | grep -c 'legal-primary-badge'; echo -n 'css_primary:'; curl -s http://127.0.0.1:8080/static/light-theme.css | grep -c 'legal-entry.primary'; echo -n 'css_match:'; curl -s http://127.0.0.1:8080/static/light-theme.css | grep -c 'legal-match'"`
- description: Verify deployed static assets contain new legal styles
```
Exit code 1
js_badge:0
css_primary:0
css_match:0
```


## ASSISTANT  2026-11-11 17:37:01  (seq 183)


### TOOL `Bash` [completed]
- command: `ssh -i ~/.ssh/police_deploy_ed25519 -o StrictHostKeyChecking=no admin@47.95.141.225 "curl -s -o /tmp/w.js -w 'js:%{http_code} %{size_download}\n' http://127.0.0.1:8080/static/workspace.js; head -c 120 /tmp/w.js; echo; ls -l /opt/police/web/workspace.js /opt/police/web/light-theme.css; grep -c 'legal-primary-badge' /opt/police/web/workspace.js; grep -c 'legal-entry.primary' /opt/police/web/light-theme.css"`
- description: Check remote static file response and on-disk contents
```
js:401 3234
<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,init
-rw-r--r-- 1 admin admin 13424 Nov 11 17:36 /opt/police/web/light-theme.css
-rw-r--r-- 1 admin admin 83872 Nov 11 17:36 /opt/police/web/workspace.js
1
1
```


## ASSISTANT  2026-11-11 17:37:08  (seq 184)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && grep -n "^## " docs/实施记录.md | tail -8 && wc -l docs/实施记录.md`
- description: List recent section headers in implementation log
```
21:## 1. 车行到达圈（2026-11-09）
129:## 2. 无人机核查航线（2026-11-09）
247:## 3. 法条与证据清单检索助手（2026-11-09）
331:## 4. 工程化加固（2026-11-09）
377:## 5. 最终端到端回归（2026-11-09）
411:## 6. 五种扩宽方向：多场景研判（2026-11-09）
476:## 7. 案件办理页 Tabler 改版与访问口令表单（2026-11-11）
533:## 8. 优化收尾：二维码修复、口令锁定与 1366 验收（2026-11-11）
586 docs/实施记录.md
```


## ASSISTANT  2026-11-11 17:37:13  (seq 186)


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\docs\\实施记录.md", "offset": 533, "limit": 60}
```


## ASSISTANT  2026-11-11 17:37:17  (seq 187)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && grep -n "法条" README.md | head -20`
- description: Find legal section in README
```
31:- **法条与取证清单助手（P1-D，新增）**：内置 17 条草稿法条（刑法 338/339/340/341/342/343/344/345、森林法、矿产资源法、自然保护区条例、行政处罚法、刑诉法、环保法及两部司法解释），按案件灾种匹配法条和取证方向，可检索、可下载 TXT。`review_status=draft_requires_legal_review`，引用前必须经法制审核并以官方文本为准。
34:- **案件办理页 Tabler 浅色改版（P2，新增）**：`/workspace` 改为浅色后台布局，增加“一张图 / 多场景研判 / 案件办理”主导航、案件面包屑和五步办理进度条；左列放快照/因子/任务/证据/复核/报告，右列放双分数说明/到达圈/无人机，法条/到场码/审计底部通栏。保留全部原有办案能力，只改展示层。实现记录见 [docs/实施记录.md](docs/实施记录.md)。
60:| 法条助手 | 本地 JSON 草稿库（17 条）+ 灾种映射 + 字符检索；所有结果标注 `draft_requires_legal_review` |
87:│  ├─ legal.py              法条草稿库检索、灾种映射、案件取证清单
88:│  ├─ legal_api.py          法条 meta/hazards/search/checklist/entry
110:│  ├─ legal\knowledge_base.json  法条与取证方向草稿库（17 条，需法制审核）
117:│  ├─ workspace.js          工作台交互、双分数四象限、上传与报告、证据链/到场码/到达圈/无人机/法条
160:├─ docs/实施记录.md          到达圈/无人机/法条/工程化的设计、验证数字与边界
484:| GET | `/api/cases/{id}/legal-checklist` | 按案件灾种生成法条与取证清单 |
485:| GET | `/api/legal/meta`、`/hazards`、`/search`、`/checklist`、`/entry/{id}` | 法条草稿库元数据、灾种、检索、清单、单条 |
520:12. **法条助手边界**：知识库是 `draft_requires_legal_review` 草稿，条文为摘要，不是官方全文；灾种映射和关键词检索可能漏项，取证清单不判断现有证据是否达到证明标准。引用、定性、移送、处罚前必须由法制部门审核，并以官方发布文本为准。
581:- ✅ 法条/取证清单助手：17 条草稿库、17 类灾种映射、案件清单、TXT 导出（须法制审核）
584:- ⏳ 后续：STAC 定时 + 真实变化检测 + 自动
... [truncated]
```


## ASSISTANT  2026-11-11 17:37:22  (seq 188)


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\README.md", "limit": 8, "offset": 29}
```


### TOOL `Read` [completed]
```json
{"file_path": "E:\\警务遥感\\README.md", "limit": 6, "offset": 57}
```


## ASSISTANT  2026-11-11 17:37:27  (seq 189)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\README.md`
- old:
```
- **法条与取证清单助手（P1-D，新增）**：内置 17 条草稿法条（刑法 338/339/340/341/342/343/344/345、森林法、矿产资源法、自然保护区条例、行政处罚法、刑诉法、环保法及两部司法解释），按案件灾种匹配法条和取证方向，可检索、可下载 TXT。`review_status=draft_requires_legal_review`，引用前必须经法制审核并以官方文本为准。
```
- new:
```
- **法条与取证清单助手（P1-D，新增）**：内置 17 条草稿法条（刑法 338/339/340/341/342/343/344/345、森林法、矿产资源法、自然保护区条例、行政处罚法、刑诉法、环保法及两部司法解释），按案件灾种匹配法条和取证方向，可检索、可下载 TXT。检索支持同义词扩展、规则命中和双字相似度兜底，并给出“主要法条”推断（如“占用农田地”→刑法第三百四十二条）。`review_status=draft_requires_legal_review`，引用前必须经法制审核并以官方文本为准。
```


### TOOL `Edit` [completed]
- file: `E:\警务遥感\README.md`
- old:
```
| 法条助手 | 本地 JSON 草稿库（17 条）+ 灾种映射 + 字符检索；所有结果标注 `draft_requires_legal_review` |
```
- new:
```
| 法条助手 | 本地 JSON 草稿库（17 条）+ 灾种映射 + 同义词/规则/双字相似度检索 + 主要法条推断；所有结果标注 `draft_requires_legal_review` |
```


### TOOL `Edit` [completed]
- file: `E:\警务遥感\docs\实施记录.md`
- old:
```
- 二维码依赖 `qrcode[pil]`；仅执行 `pip install -r requirements.txt` 的新环境不会再复现 500。
```
- new:
```
- 二维码依赖 `qrcode[pil]`；仅执行 `pip install -r requirements.txt` 的新环境不会再复现 500。

## 9. 法条检索：从关键词推断主要法条（2026-11-11）

### 9.1 问题与根因

在工作台“法条与取证清单”输入“占用农田地”后，列表仍停留在案件灾种清单；即使触发检索，首条也是刑法第三百三十八条污染环境罪，而不是第三百四十二条非法占用农用地罪。排查到三个独立原因：

- 前端只给“检索”按钮绑定了 `click`，输入框没有 Enter 或 `input` 监听，打字后不会自动检索。
- 后端 `_score` 只做整串精确子串匹配；知识库写的是“非法占用农用地/耕地”，没有“农田地”，因此得分为 0。
- 案件清单 `case_checklist` 按条目 id 排序，非法采矿灾种下 338 排在 343 前，主次颠倒。

### 9.2 后端改动（app/legal.py）

- `HAZARD_PRIMARY`：灾种到主条目的映射。采矿/矿山破坏→343，倾倒/水体异常→338，违建/裸土/道路侵入/湿地→342，植被退化/火烧→345。
- `PRIMARY_RULES`：十条“语义规则→条目+灾种”映射，覆盖农用地占用、矿产资源开采、污染环境、固体废物、水产品捕捞、野生动物、重点保护植物、林木采伐、毁林开垦、自然保护区。
- `SYNONYMS`：把“农田/农田地/农地”归一到“农用地、耕地”，并补充占用、采砂、排污、砍树等口语词。
- `_similarity`：双字 Dice 相似度；`_score` 先做同义词扩展和规则加权，再按 heading 8 / keywords 6 / law 3 / article 3 / summary 2 的字段权重计分，相似度 ≥0.34 时加 `int(sim×24)` 兜底召回。
- `search()` 新增 `inferred_hazard`、`inferred_hazard_label`、`primary`（仅 top 分 ≥8 时给出）和零命中时的 `suggestions`；每条结果带 `score`、`match_reason`。
- `case_checklist()` 按 `HAZARD_PRIMARY` 把主条目置顶，并返回 `primary`，`match_reason` 写明“按案件灾种推荐”。

### 9.3 前端改动（web/workspace.js、样式）

- 检索 banner 改为“主要法条 / 命中法条 / 匹配总数”，新增主要法条提示块；命中的卡片加 `.primary` 边框和“主要适用”徽标，并展示 `match_reason`。
- 零命中时展示相近条目的 `article + heading`，不再只给一句“没有匹配”。
- `#legal-q` 支持 Enter 和 350ms 防抖输入；灾种下拉在有词时自动重检索；`legalSeq` 丢弃过期响应，避免快速输入时旧结果覆盖新结果。
- 深色样式加在 `web/workspace.css`，浅色覆盖加在 `web/light-theme.css`，主色沿用 Tabler `#206bc4`。

### 9.4 验证

| 输入 | 推断灾种 | 主要法条 |
| --- | --- | --- |
| 占用农田地 | bare_soil | 刑法第三百四十二条 非法占用农用地罪 |
| 非法占用农用地 | bare_soil | 刑法第三百四十二条 |
| 占用耕地建房 | bare_soil | 刑法第三百四十二条 |
| 非法采矿 | mining | 刑法第三百四十三条 非法采矿罪 |
| 盗采砂石 | mining | 刑法第三百四十三条 |
| 倾倒危险废物 | dumping | 刑法第三百三十八条 污染环境罪 |
| 砍树 | veg_degradation | 刑法第三百四十五条 盗伐林木罪 |
| 电鱼 | water_anomaly | 刑法第三百四十条 非法捕捞水产品罪 |
| 非法狩猎 | — | 刑法第三百四十一条 非法狩猎罪 |

- 本地 1366×768：输入“占用农田地”后 b
... [truncated]
```


## ASSISTANT  2026-11-11 17:37:47  (seq 190)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && grep -n "async function loadLegalChecklist" -A 18 web/workspace.js`
- description: Inspect checklist loader for race with search
```
1209:  async function loadLegalChecklist(c) {
1210-    const button = $('[data-action="legal-txt"]');
1211-    try {
1212-      const data = await api(`/api/cases/${encodeURIComponent(c.id)}/legal-checklist`);
1213-      state.legal = data;
1214-      $("#legal-result").innerHTML = renderLegal(data, "case");
1215-      if (button) button.disabled = false;
1216-    } catch (err) {
1217-      $("#legal-result").innerHTML = `<p class="empty-small bad-text">${esc(err.message)}</p>`;
1218-    }
1219-  }
1220-
1221-  let legalSeq = 0;
1222-
1223-  async function searchLegal(c) {
1224-    const input = $("#legal-q");
1225-    const select = $("#legal-hazard");
1226-    const query = input ? input.value.trim() : "";
1227-    const hazard = select ? select.value : "";
```


## ASSISTANT  2026-11-11 17:37:54  (seq 191)


### TOOL `Edit` [completed]
- file: `E:\警务遥感\web\workspace.js`
- old:
```
async function loadLegalChecklist(c) {
    const button = $('[data-action="legal-txt"]');
    try {
      const data = await api(`/api/cases/${encodeURIComponent(c.id)}/legal-checklist`);
      state.legal = data;
      $("#legal-result").innerHTML = renderLegal(data, "case");
      if (button) button.disabled = false;
    } catch (err) {
      $("#legal-result").innerHTML = `<p class="empty-small bad-text">${esc(err.message)}</p>`;
    }
  }

  let legalSeq = 0;

  async function searchLegal(c) {
```
- new:
```
let legalSeq = 0;

  async function loadLegalChecklist(c) {
    const button = $('[data-action="legal-txt"]');
    const seq = legalSeq;
    try {
      const data = await api(`/api/cases/${encodeURIComponent(c.id)}/legal-checklist`);
      if (seq !== legalSeq) return;
      const input = $("#legal-q");
      if (input && input.value.trim()) return;
      state.legal = data;
      $("#legal-result").innerHTML = renderLegal(data, "case");
      if (button) button.disabled = false;
    } catch (err) {
      if (seq !== legalSeq) return;
      const input = $("#legal-q");
      if (input && input.value.trim()) return;
      $("#legal-result").innerHTML = `<p class="empty-small bad-text">${esc(err.message)}</p>`;
    }
  }

  async function searchLegal(c) {
```


## ASSISTANT  2026-11-11 17:38:04  (seq 192)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && node --check web/workspace.js && echo JS_OK`
- description: Recheck workspace.js syntax
```
JS_OK
```


## ASSISTANT  2026-11-11 17:38:10  (seq 193)


### TOOL `mcp__playwright__browser_navigate` [completed]
```json
{"url": "http://127.0.0.1:8093/workspace"}
```


## ASSISTANT  2026-11-11 17:38:14  (seq 194)


### TOOL `mcp__playwright__browser_evaluate` [completed]
```json
{"function": "async () => {\n  const wait = (ms) => new Promise((r) => setTimeout(r, ms));\n  for (let i = 0; i < 40 && !document.querySelector('#legal-q'); i += 1) await wait(250);\n  const input = document.querySelector('#legal-q');\n  if (!input) return { error: 'no legal input' };\n  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set;\n  setter.call(input, '占用农田地');\n  input.dispatchEvent(new Event('input', { bubbles: true }));\n  await wait(1500);\n  const res = document.querySelector('#legal-result');\n  const first = res.querySelector('.legal-entry');\n  return {\n    banner: res.querySelector('.legal-banner').innerText.replace(/\\s+/g, ' '),\n    firstIsPrimary: !!first && first.classList.contains('primary'),\n    firstArticle: first ? first.que
```


## ASSISTANT  2026-11-11 17:38:22  (seq 195)


### TOOL `mcp__playwright__browser_console_messages` [completed]
```json
{"level": "error"}
```


## ASSISTANT  2026-11-11 17:38:26  (seq 197)


### TOOL `Bash` [completed]
- command: `cd /e/警务遥感 && scp -i ~/.ssh/police_deploy_ed25519 web/workspace.js admin@47.95.141.225:/opt/police/web/workspace.js && ssh -i ~/.ssh/police_deploy_ed25519 admin@47.95.141.225 "grep -c 'legalSeq' /opt/police/web/workspace.js; sudo systemctl is-active police-web"`
- description: Redeploy workspace.js and confirm service active
```
8
active
```


## ASSISTANT  2026-11-11 17:38:35  (seq 198)


### TOOL `TaskStop` [completed]
```json
{"task_id": "exec_2ae925bd-6c06-41a5-b1a4-177f2d1b934b"}
```


## ASSISTANT  2026-11-11 17:38:40  (seq 199)


### TOOL `TodoWrite` [completed]
```json
{"todos": [{"content": "后端法条打分与主要法条推断（app/legal.py）", "priority": "high", "status": "completed"}, {"content": "前端主要法条展示与检索交互（web/workspace.js）", "priority": "high", "status": "completed"}, {"content": "主要法条样式（深色 + 浅色主题）", "priority": "high", "status": "completed"}, {"content": "本地语法检查与 1366×768 Playwright 验证“占用农田地”→342", "priority": "high", "status": "completed"}, {"content": "部署远程、接口验证并更新实施记录", "priority": "high", "status": "completed"}]}
```


## ASSISTANT  2026-11-11 17:38:45  (seq 201)

已完成：工作台“法条与取证清单”现在会根据关键词自动检索，并把最相关的一条作为“主要法条”置顶。输入“占用农田地”会直接给出**刑法第三百四十二条 非法占用农用地罪**，不再停留在案件灾种清单，也不会再把第三百三十八条排在前面。

问题由三处叠加造成，分别修掉了：

- **前端不触发检索**：原来只有“检索”按钮绑定了点击。现在输入框支持 Enter 和 350ms 防抖输入，灾种下拉在有词时自动重查；并用序号丢弃过期响应，避免快速输入时旧结果覆盖新结果。
- **关键词匹配不到**：后端原来只做整串精确匹配，知识库里是“非法占用农用地/耕地”，没有“农田地”。现在 `app/legal.py` 增加同义词归一、十条语义规则、字段权重和双字相似度兜底（阈值 0.34），并返回 `primary`、`inferred_hazard` 和零命中时的相近条目建议。
- **案件清单主次颠倒**：`case_checklist` 改为按灾种的主法条映射置顶，采矿灾种下 343 会排在 338 前面。

界面同步调整：检索区改为“主要法条 / 命中法条 / 匹配总数”，命中卡片加“主要适用”徽标和命中原因；深色与 Tabler 浅色样式都已补齐。

验证结果：

- 本地 1366×768 实际输入“占用农田地”，banner 显示“第三百四十二条 非法占用农用地罪，命中 1 条”，首卡带主要法条样式，页面无横向滚动，控制台无报错。截图：[legal-1366.png](D:\ZCODE插件\playwright-mcp\legal-1366.png)
- 其他查询：非法采矿/盗采砂石→343，倾倒危险废物→338，砍树→345，电鱼→340，非法狩猎→341。
- 已同步 `app/legal.py`、`web/workspace.js`、`web/workspace.css`、`web/light-theme.css` 到远程 `/opt/police` 并重启服务；远程接口实测返回 `primary.id=criminal-law-342`、score 103、仅 1 条结果，`police-web` 处于 active。
- 文档已补：`README.md` 的法条助手说明，以及 `docs/实施记录.md` 第 9 节（根因、代码变更、验证表、已知边界）。
- 本地临时 8093 服务已停止。

需要说明的边界：规则和同义词表是人工维护的，零命中时只给相近条目，不保证召回全部法条；“主要法条”是检索推荐，不是法律适用结论，定性、处罚和移送仍需法制部门复核。
