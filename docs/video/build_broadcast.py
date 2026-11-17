# -*- coding: utf-8 -*-
"""Build the 15-slide, scoring-aligned broadcast version of the demo video."""
from pathlib import Path
from PIL import Image, ImageDraw
import json
import build_slides as b

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "slides_broadcast"
OUT.mkdir(exist_ok=True)
b.SLIDES = OUT
TOTAL = 15


def header15(draw, label, idx):
    f = b.font(b.F_BODY, 24)
    draw.rectangle([60, 48, 68, 84], fill=b.GREEN)
    draw.text((84, 50), label, font=f, fill=b.MUTED)
    draw.text((b.W - 150, 50), f"{idx:02d} / {TOTAL}", font=f, fill=(120, 150, 138))


b.header = header15


def cover15(idx):
    im = Image.open(b.SHOTS / "home.png").convert("RGB").resize((b.W, b.H), Image.LANCZOS)
    ov = Image.new("RGBA", (b.W, b.H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.rectangle([0, 0, b.W, b.H], fill=(3, 15, 11, 70))
    od.rectangle([0, 0, 1180, b.H], fill=(4, 18, 13, 244))
    for x in range(1180, 1370):
        a = int(244 * (1 - (x - 1180) / 190))
        od.line([(x, 0), (x, b.H)], fill=(4, 18, 13, a))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 12, b.H], fill=b.GOLD)
    d.text((84, 96), "2026 第九届传智杯 · AI 创新应用挑战赛（Vibe Coding，个人赛）",
           font=b.font(b.F_BODY, 27), fill=b.GOLD)
    d.text((80, 220), "警务遥感", font=b.font(b.F_TITLE, 126), fill=b.WHITE)
    d.text((86, 374), "生态安全与灾害风险一张图", font=b.font(b.F_TITLE, 54), fill=b.GOLD)
    d.text((88, 474), "卫星发现 → 警务核查 → 证据闭环", font=b.font(b.F_BODY, 36), fill=(226, 236, 231))
    b.pill(d, (88, 570), "评审视角：优势 · 创新 · 用处", b.font(b.F_BODY, 28))
    b.pill(d, (88, 640), "过程可复现 25% · 实用价值 25% · 意图控制 20%", b.font(b.F_BODY, 24))
    d.text((88, 790), "演示视频 · 2026-11-18", font=b.font(b.F_BODY, 28), fill=b.GREEN)
    d.text((88, 850), "所有遥感结果、案件、证据均为演示 / 仿真数据；边界见技术文档。",
           font=b.font(b.F_BODY, 23), fill=(157, 184, 174))
    d.text((b.W - 150, 50), f"01 / {TOTAL}", font=b.font(b.F_BODY, 24), fill=(120, 150, 138))
    im.save(OUT / f"slide{idx:02d}.png")


def pain_slide(idx):
    im = Image.open(b.SHOTS / "home.png").convert("RGB").resize((b.W, b.H), Image.LANCZOS)
    ov = Image.new("RGBA", (b.W, b.H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.rectangle([0, 0, b.W, b.H], fill=(4, 16, 12, 205))
    od.rectangle([0, 0, 14, b.H], fill=b.GREEN)
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)
    d.text((70, 70), "真实断点", font=b.font(b.F_BODY, 28), fill=b.GREEN)
    b.draw_text(d, (70, 130), "遥感不是看不见，是看见了接不住",
                b.font(b.F_TITLE, 64), fill=b.WHITE, max_w=1750, spacing=1.2)
    bullets = [
        "图斑停在 GIS 图层，进不了案件",
        "现场核查靠电话和聊天工具派单",
        "照片散落在个人手机，对应不到图斑",
        "复核时说不清：谁、何时、改没改",
    ]
    y = 330
    for x in bullets:
        d.rectangle([70, y + 12, 78, y + 44], fill=b.GOLD)
        y = b.draw_text(d, (102, y), x, b.font(b.F_BODY, 34), fill=(226, 236, 231),
                        max_w=1600, spacing=1.35) + 22
    d.rounded_rectangle([70, 760, 1790, 910], radius=12, fill=(15, 42, 33))
    d.rectangle([70, 760, 78, 910], fill=b.GREEN)
    b.draw_text(d, (104, 786),
                "需要一条工程链路：图斑 → 案件 → 任务 → 证据 → 复核 → 归档。",
                b.font(b.F_BODY, 32), fill=b.GOLD, max_w=1620, spacing=1.3)
    im.save(OUT / f"slide{idx:02d}.png")


def bottom_caption_slide(name, title, badges, caption, idx, label):
    im = Image.open(b.SHOTS / name).convert("RGB").resize((b.W, b.H), Image.LANCZOS)
    ov = Image.new("RGBA", (b.W, b.H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.rectangle([0, b.H - 255, b.W, b.H], fill=(4, 18, 13, 238))
    od.rectangle([0, b.H - 255, 10, b.H], fill=b.GREEN)
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)
    d.text((30, b.H - 238), label, font=b.font(b.F_BODY, 22), fill=(200, 225, 212))
    b.draw_text(d, (30, b.H - 205), title, b.font(b.F_TITLE, 46), fill=b.WHITE,
                max_w=1840, spacing=1.2)
    x = 32
    for badge in badges:
        x = b.pill(d, (x, b.H - 135), badge, b.font(b.F_BODY, 23))
    b.draw_text(d, (32, b.H - 78), caption, b.font(b.F_BODY, 22), fill=b.MUTED,
                max_w=1840, spacing=1.2)
    d.rectangle([0, b.H - 8, b.W, b.H], fill=b.DARK)
    im.save(OUT / f"slide{idx:02d}.png")


def score_map_slide(idx):
    im, d = b.base("评分方法 · 证据映射", idx)
    d.text((70, 118), "评分方法：五个维度，逐条给出证据", font=b.font(b.F_TITLE, 54), fill=b.WHITE)
    rows = [
        ("过程可复现与演进链", "25%", "公开仓库 20 提交 · 三条 Prompt 链 · raw 会话 · 10 分钟复现"),
        ("解题有效性与实用价值", "25%", "一张图 → 案件 → 任务 → 证据 → 复核 → 归档完整闭环"),
        ("方法创新性与意图控制", "20%", "三条主链：先定位、只加不改、修回归；1200 消息 / 1080 工具调用"),
        ("工程质量与架构", "15%", "FastAPI 模块拆分 · 111 项接口 · SHA-256 + HMAC 证据链 · 错误回退"),
        ("用户体验与产品精细度", "15%", "11 个页面 · 1366/1920 无横向溢出 · 空态 / 错误态 / 加载态"),
    ]
    y = 235
    for name, weight, evidence in rows:
        d.rounded_rectangle([70, y, 1850, y + 132], radius=12, fill=(15, 42, 33), outline=(60, 110, 90), width=1)
        d.text((96, y + 20), name, font=b.font(b.F_TITLE, 32), fill=b.WHITE)
        d.text((760, y + 20), weight, font=b.font(b.F_TITLE, 36), fill=b.GOLD)
        b.draw_text(d, (900, y + 22), evidence, b.font(b.F_BODY, 25), fill=(226, 236, 231),
                    max_w=900, spacing=1.25)
        y += 148
    d.text((70, 980), "评分方法来自赛道页面；每条证据都能在公开仓库、技术文档或视频里核对。",
           font=b.font(b.F_BODY, 22), fill=b.MUTED)
    im.save(OUT / f"slide{idx:02d}.png")


def boundary_slide(idx):
    im, d = b.base("边界 · 不把演示写成承诺", idx)
    d.text((70, 120), "边界写清楚，优势才可信", font=b.font(b.F_TITLE, 58), fill=b.WHITE)
    items = [
        ("规则检测", "不是训练好的模型；变化检测是分位数阈值 + 形态学处理"),
        ("6G 指标", "时延、带宽、丢包、切片利用率均为数字孪生仿真值"),
        ("法条助手", "摘要草稿，需法制部门审核；引用以官方文本为准"),
        ("尚未验证", "精度评估、等保、渗透、压力测试；烟台许可 / 河道 / 水位 / 现场材料待核"),
        ("数据边界", "案件、证据、账号均为演示数据；真实警务数据不进入仓库"),
    ]
    y = 260
    for head, body in items:
        d.rounded_rectangle([70, y, 1850, y + 128], radius=12, fill=(14, 36, 29), outline=(60, 110, 90), width=1)
        d.text((96, y + 22), head, font=b.font(b.F_TITLE, 31), fill=b.GOLD)
        b.draw_text(d, (390, y + 24), body, b.font(b.F_BODY, 26), fill=(226, 236, 231),
                    max_w=1400, spacing=1.25)
        y += 142
    im.save(OUT / f"slide{idx:02d}.png")


NARRATION = [
    "各位评委好，这是警务遥感，生态安全与灾害风险一张图。下面用真实网页，说明它解决了什么问题、创新在哪里、为什么值得用。",
    "基层警务的断点，不是看不见，而是看见了接不住。卫星能发现疑似采矿、火烧迹地、裸土扰动的图斑，但图斑停在GIS图层；现场核查靠电话派单，照片散落在手机；复核时说不清这张照片对应哪个图斑、什么时候拍的、有没有被改过。",
    "先看一张图。31个高发点、180个0.2度风险格网、14天风险推演，按核查优先级排序。点开图斑，历史证据、坡度、距离和处置建议在同一屏；图斑可以一键转成案件，而不是停在演示图层。这是实用价值的第一个落点。",
    "烟台试点做单点深挖。真实的Sentinel-2双时相影像，按观测日期计算时长，给出43个规则候选、24公顷候选面积；重点图斑P07只有54个像元，合0.54公顷。页面写的是规则评分0.71，未校准，不是违法概率；许可、河道范围、水位、现场材料都列为待核项。",
    "6G天枢是创新点：把网络状态接到影像推理。常态、卫星切换、切片拥塞、基站中断四个场景，分别返回不同的编排计划；中断场景只传四分之一瓦片，省75%带宽，293毫秒出线索。时延、带宽、丢包都是数字孪生仿真值，页面和报告都标注simulated，不写成实测。",
    "发现之后要能办案。案件工作台把线索变成四角色、五步流转：指挥员建案派单，民警到场取证，复核员审核，管理员审计。每份证据带SHA-256，案件级HMAC链把证据串起来；任何一条被改动，证据链校验都会失败。这是从图斑到证据闭环的关键。",
    "现场用移动端。到场码绑定案件，签到、拍照、文字证据先写IndexedDB；弱网或断网不丢，恢复网络后按队列同步，失败保留队列。现场民警最关心的地址、任务和证据不丢，这一屏解决的是可用性。",
    "接下来是Vibe Coding的核心：AI协作过程。这个作品只把用AI修改程序的对话作为证据，不把用AI想思路的对话算进来。作者负责选题、业务判断和验收标准；AI负责定位代码、给出修改、跑验证、修回归。三条主链合计1200条消息、1080次工具调用，都能回到session_id和sequence。",
    "第一条主链，法条检索。用户原话：输入关键字不出具体法条，要求根据关键字推出主要法条。AI没有直接调阈值，而是先定位三处原因：前端只有按钮触发，回车不响应；后端只做整串精确匹配；案件清单把刑法338排在343前。修改后，占用农田地命中刑法342，六组查询全部通过。",
    "第二条主链，6G天枢。用户的约束很硬：不要改我已经做好的，在原有基础上往上加。AI复用约85%的现有能力，只新增6G模块，主程序只做三处追加；前端用Three.js搭五节点四链路，断网时基站转红、NTN接管，SVG兜底；四套样板对比后选globe.gl，并把网络状态接到取数。",
    "第三条主链，烟台预警。第一版右侧浮窗没有生效，用户说并没有在旁边做专项分析，刷新也没用。AI先怀疑缓存，判断错了；真实原因是8080端口跑着旧后端，页面是新的，但烟台接口没有加载。修复后升级为独立三栏工作区，修正EPSG 32651坐标误用，P07重算为0.54公顷，43个候选、24公顷。",
    "过程可复现，是这个赛道25%的评分项。公开仓库里有20个提交，提交信息带session_id和sequence；README给出10分钟复现：不需要Node、不需要数据库服务、不需要云API Key；一条uvicorn命令起在8080，四个演示账号直接登录。评委可以按提交和会话逐条核对。",
    "对应评分方法：过程可复现25%，看公开仓库、20个提交、三条Prompt链和raw会话；实用价值25%，看一张图到案件到证据的完整闭环；意图控制20%，看三条主链里AI如何定位、如何只加不改、如何修回归；工程质量15%，看FastAPI模块、111项接口、证据链和错误回退；体验15%，看11个页面、1366和1920无横向溢出、空态错误态加载态。",
    "边界也写清楚：规则检测不是训练好的模型；6G指标是仿真；法条是摘要草稿，需要法制审核；没有做精度评估、等保、渗透和压测；烟台的许可、河道、水位、现场材料还要核。不把演示写成承诺。",
    "公开仓库、10分钟复现、三条完整Prompt链、19页技术文档，都在提交材料里。谢谢观看。",
]


def main():
    cover15(1)
    pain_slide(2)
    b.screenshot_slide("home.png", "一张图：先查哪里、谁去、什么时候回",
                       ["评分：实用价值 25%", "31 个核查点", "180 个 0.2° 格网", "14 天推演"],
                       "图斑可一键转案件；风险排序、气象与影像在同一条核查链上。", 3)
    b.screenshot_slide("yantai.png", "烟台试点：真实影像，规则候选，边界透明",
                       ["评分：实用价值 25%", "Sentinel-2 双时相", "43 候选 / 24.0 ha", "P07 0.54 ha"],
                       "页面写规则评分 0.71（未校准），不是违法概率；许可、河道、水位、现场材料列为待核。",
                       4, "演示环境 · /yantai")
    b.screenshot_slide("sixg.png", "6G 天枢：网络状态驱动影像推理",
                       ["评分：创新性 20%", "四场景编排", "中断省 75% 带宽", "293 ms 出线索"],
                       "时延 / 带宽 / 丢包为数字孪生仿真值，页面与报告标注 simulated。",
                       5, "演示环境 · /sixg")
    b.screenshot_slide("workspace-case.png", "案件闭环：四角色、五步流转、证据链",
                       ["评分：实用价值 25%", "四角色 / 五步", "SHA-256 + HMAC", "6 份证据通过"],
                       "任何一条证据被改动，证据链校验失败；复核与归档留审计记录。",
                       6, "演示环境 · /workspace")
    b.phone_slide(7)
    bottom_caption_slide("ai-replay.png", "AI 协作：只收“改程序”，不收“想思路”",
                         ["评分：过程可复现 25%", "3 条主链", "1200 条消息 / 1080 次工具调用"],
                         "每条 Prompt、AI 动作、代码行号与验证结果都来自真实 ZCode 会话；session_id + sequence 可核对。",
                         8, "AI 协同回放 · 本仓库 docs/video/ai-replay.html")
    b.prompt_slide("主链一 · 法条检索：从“不出结果”到可解释命中",
                   "我输入关键字不出具体的法条这个问题解决一下，要求根据关键字推出对应主要法条。",
                   ["三处根因：前端仅按钮触发；后端整串精确匹配；清单 338 排在 343 前",
                    "加 _norm / _bigrams / 同义词 / 10 条语义规则 / 双字相似度 0.34",
                    "前端 Enter + 350ms 防抖 + 序号丢弃过期响应",
                    "主法条置顶，显示命中原因；1366 宽无横向滚动"],
                   ["占用农田地 → 刑法 342 · score 103", "非法采矿 → 343", "倾倒危险废物 → 338", "六组查询通过"],
                   9)
    b.prompt_slide("主链二 · 6G 天枢：只加不改，四场景编排",
                   "不要改我已经做好的，再我做好的基础上往上加；把天枢放到右上角，从 GitHub 找开源项目，用 Three.js 做动态效果；科技风强，给我样板选择，最后变成实用的东西。",
                   ["复用约 85% 现有能力；新增 sixg 模块，main.py 仅 3 处追加",
                    "Three.js r128 五节点四链路 + SVG 降级；四套样板选 globe.gl",
                    "网络感知编排：eMBB / URLLC / NTN 按场景切换",
                    "中断场景省 75% 带宽；6G 指标标注为仿真"],
                   ["常态 4/4 瓦片 · 0% 节省", "卫星切换 3/4 · 25%", "切片拥塞 2/4 · 50%", "基站中断 1/4 · 75%"],
                   10)
    b.prompt_slide("主链三 · 烟台预警：先定位，再修，再验证",
                   "读取烟台非法采矿与河道采砂真实预警实施方案，在中国地区右边再开一个窗口，对现有矿区等资源清晰标注位置，并实现预警。",
                   ["第一次未生效：不是缓存，是 8080 旧进程未加载烟台接口",
                    "修复后升级为独立三栏工作区；入口橙色“烟台预警”自动展开",
                    "修正 EPSG:32651 米制坐标误用；P07 0.54 ha / 54 像元",
                    "全窗口 43 候选 / 24.0 ha；规则评分 0.71（未校准）"],
                   ["用户：并没有在旁边做专项分析", "用户：刷新没用", "AI：查进程与端口", "公网 /yantai 200"],
                   11)
    bottom_caption_slide("github.png", "可复现：公开仓库与 20 个提交",
                         ["Public 仓库", "20 commits", "10 分钟复现", "无需云 API Key"],
                         "README 给出启动、账号、演示路线与三条 Prompt 链索引；提交信息带 session_id / sequence。",
                         12, "公开仓库 · github.com/478381338-debug/police-rs-vibe")
    score_map_slide(13)
    boundary_slide(14)
    b.closing(15)

    (OUT / "narration.json").write_text(json.dumps(NARRATION, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"slides: {len(list(OUT.glob('slide*.png')))}")
    print(f"narration chars: {sum(len(x) for x in NARRATION)}")
    print(f"estimated seconds @4.2 chars/s: {sum(len(x) for x in NARRATION)/4.2:.1f}")


if __name__ == "__main__":
    main()