# 迭代 1｜法条检索：从“输入没反应”到“主要法条”推断

> 会话：ZCode `session_c7acdf13bcc033a4b1895a63813d3821`（烟台整体）
> 时间：2026-11-11 17:30；消息 seq 141–201
> 模型：`new-provider / deepseek-v4.1-flash`（variant `max`）
> 原始导出：[`raw/chain1_legal_search.md`](raw/chain1_legal_search.md)（364 blocks）

## 1. 用户 Prompt（原文）

> 我输入关键字不出具体的法条这个问题解决一下，要求根据关键字推出对应主要法条

这是一个“功能坏了 + 要求升级”的复合指令：既要修复“输入没反应”，又要把“检索结果”升级为“主要法条推断”。

## 2. AI 的诊断：先读代码，不先改

AI 先并行读取了 `app/legal.py`、`app/legal_api.py`、`web/workspace.js`、`data/legal/knowledge_base.json`，并实际查询知识库条目。定位到三处叠加原因：

1. **前端不触发检索**：`web/workspace.js` 原来只有“检索”按钮绑定了点击事件，输入框回车和输入过程都不会发起请求。
2. **后端只做整串精确匹配**：`app/legal.py` 原来把用户输入与条目文本做整串匹配；知识库里写的是“非法占用农用地/耕地”，没有“农田地”这个词，所以命中不了。
3. **案件清单主次颠倒**：`case_checklist` 的排序把刑法第三百三十八条排在第三百四十三条前面，采矿类案件打开时看不到最相关的主法条。

## 3. 代码修改

| 文件 | 改动 |
|---|---|
| `app/legal.py` | 新增 `import re`；新增 `_norm`（去空白/标点、转小写）、`_bigrams`（双字集合）、`_similarity`（双字相似度）；新增 `SYNONYMS` 同义词表、10 条语义规则、字段权重、`_FUZZY_MIN = 0.34`；`search(query="", hazard=None, limit=20)` 返回 `primary`、`inferred_hazard` 和零命中时的相近条目建议；新增 `HAZARD_PRIMARY` 映射；`case_checklist` 按灾种主法条置顶。 |
| `web/workspace.js` | 输入框支持 Enter 和 350 ms 防抖（`legalTimer = window.setTimeout(() => searchLegal(c), 350)`）；用请求序号丢弃过期响应，避免快速输入时旧结果覆盖新结果；检索区改为“主要法条 / 命中法条 / 匹配总数”；命中卡片增加“主要适用”徽标和命中原因。 |
| `web/workspace.css`、`web/light-theme.css` | 补主法条、徽标、命中原因的深色与 Tabler 浅色样式。 |
| `README.md`、`docs/实施记录.md` | 补法条助手说明；第 9 节记录根因、代码变更、验证表、已知边界。 |

关键实现不是“加一个大模型”，而是把规则检索做扎实：

```python
def _norm(text: object) -> str:
    return _TOKEN_SPLIT.sub("", str(text or "").lower())

def _bigrams(text: str) -> set[str]:
    text = _norm(text)
    if len(text) < 2:
        return {text} if text else set()
    return {text[index:index + 2] for index in range(len(text) - 1)}
```

`search()` 先做灾种过滤，再按“同义词/语义规则/字段权重/双字相似度”打分；分数最高的条目作为 `primary`，并给出 `inferred_hazard`，让前端知道“这条主要法条对应哪个灾种”。

## 4. 运行验证

| 输入 | 结果 |
|---|---|
| `占用农田地` | 刑法第三百四十二条 非法占用农用地罪，score 103，仅 1 条结果 |
| `非法采矿` / `盗采砂石` | 刑法第三百四十三条 |
| `倾倒危险废物` | 刑法第三百三十八条 |
| `砍树` | 刑法第三百四十五条 |
| `电鱼` | 刑法第三百四十条 |
| `非法狩猎` | 刑法第三百四十一条 |

- 本地 1366×768：输入“占用农田地”，banner 显示“第三百四十二条 非法占用农用地罪，命中 1 条”，首卡带主要法条样式；页面无横向滚动，控制台 0 报错。
- 远程：同步 `app/legal.py`、`web/workspace.js`、`web/workspace.css`、`web/light-theme.css` 到 `/opt/police` 并重启 `police-web`；远程接口实测 `primary.id=criminal-law-342`、score 103、仅 1 条结果，服务 active。
- 截图：[legal-1366.png](<local-playwright-dir>\legal-1366.png)。

## 5. 这次协作做对了什么

- **先定位再改**：AI 没有直接重写检索，而是先用 `wc -l`、`grep`、读取知识库和实际查询确认三处根因。
- **改动有边界**：只动法条模块、工作台前端和两套样式，没有改案件、证据、地图等无关逻辑。
- **验证可复跑**：每个查询都有预期法条；远程部署后再次用接口验证，不是只看本地页面。
- **如实写限制**：“主要法条”是检索推荐，不是法律适用结论；同义词/规则表人工维护，零命中只给相近条目，不保证召回全部法条。