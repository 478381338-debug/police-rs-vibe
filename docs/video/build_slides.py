# -*- coding: utf-8 -*-
"""Generate 1920x1080 slides and narration JSON for the Vibe Coding demo video."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import json, math

ROOT = Path(__file__).resolve().parent
SHOTS = ROOT / "shots"
SLIDES = ROOT / "slides"
SLIDES.mkdir(exist_ok=True)

W, H = 1920, 1080
GOLD = (232, 195, 106)
GREEN = (79, 209, 161)
MUTED = (157, 184, 174)
WHITE = (244, 247, 245)
DARK = (7, 26, 20)
DARK2 = (12, 43, 35)

F_TITLE = "C:/Windows/Fonts/msyhbd.ttc"
F_BODY = "C:/Windows/Fonts/msyh.ttc"
F_MONO = "C:/Windows/Fonts/consola.ttf"


def font(path, size):
    return ImageFont.truetype(path, size)


def gradient_bg():
    im = Image.new("RGB", (W, H), DARK)
    d = ImageDraw.Draw(im)
    for y in range(H):
        t = y / H
        r = int(DARK[0] * (1 - t) + DARK2[0] * t)
        g = int(DARK[1] * (1 - t) + DARK2[1] * t)
        b = int(DARK[2] * (1 - t) + DARK2[2] * t)
        d.line([(0, y), (W, y)], fill=(r, g, b))
    # subtle grid
    grid = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(grid)
    for x in range(0, W, 80):
        gd.line([(x, 0), (x, H)], fill=(80, 160, 130, 18), width=1)
    for y in range(0, H, 80):
        gd.line([(0, y), (W, y)], fill=(80, 160, 130, 18), width=1)
    im = Image.alpha_composite(im.convert("RGBA"), grid).convert("RGB")
    return im


def wrap(draw, text, fnt, max_w):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur); cur = ""; continue
        test = cur + ch
        if draw.textlength(test, font=fnt) <= max_w:
            cur = test
        else:
            lines.append(cur); cur = ch
    if cur:
        lines.append(cur)
    return lines


def draw_text(draw, xy, text, fnt, fill=WHITE, max_w=1600, spacing=1.45, anchor_top=True):
    x, y = xy
    lines = wrap(draw, text, fnt, max_w)
    line_h = int(fnt.size * spacing)
    for line in lines:
        draw.text((x, y), line, font=fnt, fill=fill)
        y += line_h
    return y


def pill(draw, xy, text, fnt, fg=GOLD, bg=(24, 54, 44), pad=14):
    x, y = xy
    bbox = draw.textbbox((0, 0), text, font=fnt)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.rounded_rectangle([x, y, x + tw + pad * 2, y + th + pad * 1.4], radius=10, fill=bg, outline=(70, 120, 100), width=1)
    draw.text((x + pad, y + pad * 0.5), text, font=fnt, fill=fg)
    return x + tw + pad * 2 + 14


def header(draw, label, idx):
    f = font(F_BODY, 24)
    draw.rectangle([60, 48, 68, 84], fill=GREEN)
    draw.text((84, 50), label, font=f, fill=MUTED)
    draw.text((W - 150, 50), f"{idx:02d} / 14", font=f, fill=(120, 150, 138))


def footer(draw, text="2026-11-18 · 警务遥感 · 演示数据 / 仿真边界见文档"):
    f = font(F_BODY, 20)
    draw.text((60, H - 52), text, font=f, fill=(110, 140, 128))


def base(label, idx):
    im = gradient_bg()
    d = ImageDraw.Draw(im)
    header(d, label, idx)
    footer(d)
    return im, d


def screenshot_slide(name, title, badges, caption, idx, label="演示环境 · 本地 8091"):
    im = Image.open(SHOTS / name).convert("RGB").resize((W, H), Image.LANCZOS)
    # top banner with a soft gradient so the UI stays visible below it
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.rectangle([0, 0, W, 270], fill=(4, 18, 13, 240))
    for y in range(270, 390):
        a = int(240 * (1 - (y - 270) / 120))
        od.line([(0, y), (W, y)], fill=(4, 18, 13, a))
    od.rectangle([0, 0, 10, 390], fill=(79, 209, 161, 255))
    od.text((30, 36), label, font=font(F_BODY, 22), fill=(200, 225, 212))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)
    d.text((44, 92), title, font=font(F_TITLE, 52), fill=WHITE)
    x = 46
    for b in badges:
        x = pill(d, (x, 190), b, font(F_BODY, 25))
    draw_text(d, (46, 258), caption, font(F_BODY, 25), fill=MUTED, max_w=1800, spacing=1.3)
    d.rectangle([0, H - 56, W, H], fill=(4, 18, 13))
    footer(d)
    im.save(SLIDES / f"slide{idx:02d}.png")


def cover(idx):
    im = Image.open(SHOTS / "home.png").convert("RGB").resize((W, H), Image.LANCZOS)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    od = ImageDraw.Draw(ov)
    od.rectangle([0, 0, W, H], fill=(3, 15, 11, 175))
    for x in range(0, 1100):
        a = int(80 * (1 - x / 1100))
        od.line([(x, 0), (x, H)], fill=(3, 15, 11, a))
    im = Image.alpha_composite(im.convert("RGBA"), ov).convert("RGB")
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 12, H], fill=GOLD)
    d.text((84, 110), "2026 第九届传智杯 · AI 创新应用挑战赛（Vibe Coding，个人赛）", font=font(F_BODY, 28), fill=GOLD)
    d.text((80, 250), "警务遥感", font=font(F_TITLE, 128), fill=WHITE)
    d.text((86, 410), "生态安全与灾害风险一张图", font=font(F_TITLE, 56), fill=(232, 195, 106))
    d.text((88, 520), "卫星发现 → 警务核查 → 证据闭环", font=font(F_BODY, 38), fill=(226, 236, 231))
    d.text((88, 760), "作品材料 · 演示视频 · 2026-11-18", font=font(F_BODY, 30), fill=GREEN)
    d.text((88, 830), "所有遥感结果、案件、证据均为演示 / 仿真数据，边界见技术文档。", font=font(F_BODY, 24), fill=(157, 184, 174))
    d.text((W - 150, 50), "01 / 14", font=font(F_BODY, 24), fill=(120, 150, 138))
    im.save(SLIDES / f"slide{idx:02d}.png")


def text_slide(title, kicker, bullets, idx, quote=None, label="作品说明"):
    im, d = base(label, idx)
    d.text((70, 150), kicker, font=font(F_BODY, 28), fill=GREEN)
    draw_text(d, (70, 200), title, font(F_TITLE, 64), fill=WHITE, max_w=1700, spacing=1.3)
    y = 360
    bf = font(F_BODY, 34)
    for b in bullets:
        d.rectangle([70, y + 12, 78, y + 44], fill=GOLD)
        y = draw_text(d, (102, y), b, bf, fill=(226, 236, 231), max_w=1660, spacing=1.35) + 18
    if quote:
        qy = max(y + 30, 760)
        d.rounded_rectangle([70, qy, 1780, qy + 130], radius=12, fill=(15, 42, 33))
        d.rectangle([70, qy, 78, qy + 130], fill=GREEN)
        draw_text(d, (104, qy + 24), quote, font(F_BODY, 30), fill=GOLD, max_w=1620, spacing=1.35)
    im.save(SLIDES / f"slide{idx:02d}.png")


def prompt_slide(title, prompt, actions, results, idx, label="AI 协作主链"):
    im, d = base(label, idx)
    draw_text(d, (70, 120), title, font(F_TITLE, 52), fill=WHITE, max_w=1780, spacing=1.25)
    # left prompt card
    d.rounded_rectangle([70, 250, 760, 690], radius=14, fill=(16, 44, 35), outline=(60, 110, 90), width=1)
    d.text((96, 272), "用户 Prompt（原文）", font=font(F_BODY, 24), fill=GREEN)
    draw_text(d, (96, 320), prompt, font(F_BODY, 29), fill=(238, 242, 239), max_w=620, spacing=1.45)
    # right actions card
    d.rounded_rectangle([800, 250, 1850, 690], radius=14, fill=(14, 36, 29), outline=(60, 110, 90), width=1)
    d.text((826, 272), "AI 动作", font=font(F_BODY, 24), fill=GREEN)
    y = 320
    for a in actions:
        d.rectangle([826, y + 10, 832, y + 38], fill=GOLD)
        y = draw_text(d, (850, y), a, font(F_BODY, 27), fill=(226, 236, 231), max_w=960, spacing=1.3) + 14
    # results band
    d.rounded_rectangle([70, 720, 1850, 960], radius=14, fill=(9, 30, 23), outline=(50, 95, 78), width=1)
    d.text((96, 742), "验证结果", font=font(F_BODY, 24), fill=GREEN)
    x = 96
    for r in results:
        x = pill(d, (x, 800), r, font(F_BODY, 26))
    im.save(SLIDES / f"slide{idx:02d}.png")


def phone_slide(idx):
    im, d = base("移动端 · 弱网取证", idx)
    mob = Image.open(SHOTS / "mobile.png").convert("RGB")
    mh = 900
    mw = int(mob.width * mh / mob.height)
    mob = mob.resize((mw, mh), Image.LANCZOS)
    x0, y0 = 180, 90
    d.rounded_rectangle([x0 - 18, y0 - 18, x0 + mw + 18, y0 + mh + 18], radius=40, fill=(18, 30, 26), outline=(90, 130, 112), width=3)
    im.paste(mob, (x0, y0))
    tx = x0 + mw + 90
    d.text((tx, 170), "移动端：弱网现场也能取证", font=font(F_TITLE, 58), fill=WHITE)
    bullets = [
        "到场码进入现场，任务与图斑绑定",
        "拍照 / 文字证据先写 IndexedDB",
        "断网不丢，恢复网络后按队列同步",
        "同步失败保留队列，可重试、可追溯",
    ]
    y = 300
    for b in bullets:
        d.rectangle([tx, y + 12, tx + 8, y + 44], fill=GOLD)
        y = draw_text(d, (tx + 30, y), b, font(F_BODY, 33), fill=(226, 236, 231), max_w=850, spacing=1.35) + 20
    d.rounded_rectangle([tx, 720, 1810, 900], radius=12, fill=(15, 42, 33))
    draw_text(d, (tx + 28, 744), "边界：移动端不假设永远有网；离线队列是演示实现，生产需接警务专网与终端管理。", font(F_BODY, 26), fill=MUTED, max_w=820, spacing=1.35)
    im.save(SLIDES / f"slide{idx:02d}.png")


def git_slide(idx):
    im, d = base("可复现 · Git 历史", idx)
    d.text((70, 140), "可复现：8 个提交，能回到原始会话", font=font(F_TITLE, 58), fill=WHITE)
    commits = [
        ("1", "运行骨架", "11-09"), ("2", "一张图", "11-10"),
        ("3", "案件工作台", "11-11"), ("4", "法条检索", "11-11"),
        ("5", "移动端", "11-12"), ("6", "6G 天枢", "11-12"),
        ("7", "烟台预警", "11-15"), ("8", "文档", "11-17"),
    ]
    x, y = 70, 280
    for i, (n, name, date) in enumerate(commits):
        col = i % 4
        row = i // 4
        cx = 70 + col * 450
        cy = 280 + row * 210
        d.rounded_rectangle([cx, cy, cx + 410, cy + 160], radius=12, fill=(15, 42, 33), outline=(60, 110, 90), width=1)
        d.ellipse([cx + 24, cy + 34, cx + 80, cy + 90], fill=GREEN)
        d.text((cx + 42, cy + 44), n, font=font(F_TITLE, 30), fill=(5, 25, 18))
        d.text((cx + 100, cy + 34), name, font=font(F_TITLE, 32), fill=WHITE)
        d.text((cx + 100, cy + 92), date, font=font(F_BODY, 24), fill=MUTED)
    d.rounded_rectangle([70, 740, 1850, 940], radius=12, fill=(8, 26, 20), outline=(50, 95, 78), width=1)
    d.text((96, 760), "提交信息带 session_id / sequence；README 明确说明：仓库历史按真实时间线与功能归属重建。", font=font(F_BODY, 27), fill=GOLD)
    d.text((96, 830), "python -m uvicorn app.main:app --host 127.0.0.1 --port 8080", font=font(F_MONO, 30), fill=(200, 230, 215))
    d.text((96, 884), "curl http://127.0.0.1:8080/api/health", font=font(F_MONO, 30), fill=(200, 230, 215))
    im.save(SLIDES / f"slide{idx:02d}.png")


def score_slide(idx):
    im, d = base("评分对齐 · 边界", idx)
    d.text((70, 120), "评分对齐与边界", font=font(F_TITLE, 58), fill=WHITE)
    # left scores
    d.rounded_rectangle([70, 230, 900, 900], radius=14, fill=(15, 42, 33), outline=(60, 110, 90), width=1)
    d.text((96, 252), "自评（证据见文档）", font=font(F_BODY, 24), fill=GREEN)
    scores = [
        ("过程可复现与演进链", "20–22 / 25"),
        ("解题有效性与实用价值", "20–22 / 25"),
        ("方法创新性与意图控制", "15–17 / 20"),
        ("工程质量与架构", "12–13 / 15"),
        ("用户体验与产品精细度", "11–13 / 15"),
    ]
    y = 310
    for name, val in scores:
        d.text((96, y), name, font=font(F_BODY, 30), fill=(226, 236, 231))
        d.text((660, y), val, font=font(F_TITLE, 30), fill=GOLD)
        y += 105
    # right boundaries
    d.rounded_rectangle([940, 230, 1850, 900], radius=14, fill=(14, 36, 29), outline=(60, 110, 90), width=1)
    d.text((966, 252), "边界写清楚，不把演示写成承诺", font=font(F_BODY, 24), fill=GREEN)
    bounds = [
        "规则检测 ≠ 训练好的模型",
        "6G 指标 = 数字孪生仿真",
        "法条 = 摘要草稿，需法制审核",
        "未做精度评估、等保、渗透、压测",
        "烟台许可 / 河道 / 水位 / 现场材料待核",
    ]
    y = 310
    for b in bounds:
        d.rectangle([966, y + 10, 972, y + 38], fill=GOLD)
        y = draw_text(d, (992, y), b, font(F_BODY, 29), fill=(226, 236, 231), max_w=800, spacing=1.3) + 16
    im.save(SLIDES / f"slide{idx:02d}.png")


def closing(idx):
    im, d = base("谢谢观看", idx)
    d.text((70, 250), "谢谢观看", font=font(F_TITLE, 92), fill=WHITE)
    d.text((74, 400), "警务遥感 · 生态安全与灾害风险一张图", font=font(F_BODY, 40), fill=GOLD)
    draw_text(d, (74, 500), "公开仓库、10 分钟复现指南、三条完整 Prompt 链与技术文档，见提交材料。", font(F_BODY, 32), fill=(226, 236, 231), max_w=1600, spacing=1.4)
    d.text((74, 700), "演示账号：commander / officer / reviewer / admin", font=font(F_BODY, 28), fill=MUTED)
    d.text((74, 760), "所有账号仅用于本地演示；数据为演示 / 仿真数据。", font=font(F_BODY, 26), fill=MUTED)
    d.text((74, 880), "2026-11-18", font=font(F_TITLE, 36), fill=GREEN)
    im.save(SLIDES / f"slide{idx:02d}.png")


NARRATION = [
    "大家好，这是警务遥感，生态安全与灾害风险一张图。作品参加2026年第九届传智杯AI创新应用挑战赛Vibe Coding个人赛。",
    "这个项目从一个很具体的断点开始：遥感并不是看不见，而是看见了接不住。卫星影像能给出疑似采矿、火烧迹地、裸土扰动的图斑，但图斑往往停在GIS图层里；现场核查靠电话派单，照片散落在手机里；复核时说不清这张照片对应哪个图斑、什么时候拍的、有没有被改过。",
    "作品用公开遥感数据做成一个可以本地一键启动的全栈应用。一张图负责发现和排序：31个核查点、180个0.2度风险格网、14天风险推演；图斑可以一键转成案件，而不是停在演示图层。",
    "烟台试点是单点深挖：用真实的Sentinel-2双时相影像，按观测日期计算时长，给出43个规则候选、24公顷候选面积。页面明确写的是规则评分、未校准，不是概率；许可、河道范围、水位、现场材料都列为待核项。",
    "6G天枢把网络状态和影像推理绑在一起：常态、卫星切换、切片拥塞、基站中断四个场景，分别返回不同的编排计划。6G的时延、带宽、丢包都是数字孪生仿真值，页面和报告都标注了simulated，不写成实测。",
    "案件工作台把线索变成可追溯的流程：指挥员建案派单，民警到场取证，复核员审核，管理员审计。证据用SHA-256和案件级HMAC链串起来，任何一条被改动，证据链校验都会失败。",
    "移动端是现场入口：到场码、签到、拍照和文字证据先写IndexedDB，弱网或断网也不丢；恢复网络后再同步，失败保留队列。",
    "接下来是AI协作过程。这个作品只把用AI修改程序的对话作为证据，不把用AI想思路的对话算进来。作者负责选题、业务判断和验收标准；AI负责定位代码、给出修改、跑验证、修回归。有效Prompt通常包含可验证目标、边界和验收方式，而不是帮我优化一下。",
    "第一条主链是法条检索。用户的原话是：我输入关键字不出具体的法条，要求根据关键字推出对应主要法条。AI没有直接调阈值，而是先定位三处原因：前端只有按钮触发，回车不响应；后端只做整串精确匹配；案件清单把刑法338排在343前面。修改后，占用农田地返回刑法342，非法采矿返回343，倾倒危险废物返回338，六组查询全部通过。",
    "第二条主链是6G天枢。用户先说不要改我已经做好的，再往上加；再说把天枢放到右上角，从GitHub找开源项目，用Three.js做动态效果；最后要求科技风强、给样板选择、变成实用的东西。AI复用约85%的现有能力，只新增6G模块，主程序只做三处追加；四套样板对比后选globe.gl，并保留SVG降级。",
    "第三条主链是烟台预警。第一版右侧浮窗没有生效，用户说并没有在旁边做专项分析，刷新也没用。AI先怀疑缓存，判断错了；真实原因是8080端口跑着旧后端，页面是新的，但烟台接口没有加载。修复后升级为独立三栏工作区，修正了EPSG 32651坐标误用，P07图斑0.54公顷、54个像元，全窗口43个候选、24公顷。",
    "仓库历史按真实开发时间线和功能归属重建，并在README里明确说明：项目最初没有从一开始启用Git。八个提交分别对应运行骨架、一张图、案件工作台、法条检索、移动端、6G、烟台和文档；提交信息带session_id和sequence，可以回到原始会话核对。",
    "评分维度上，过程可复现和实用价值是长板；意图控制有三条真实链路；工程和体验还有提升空间。边界也写清楚：规则检测不是训练模型，6G是仿真，法条是草稿，没有做精度评估。",
    "10分钟复现指南、三条完整Prompt链和技术文档都在仓库里。谢谢观看。",
]


def main():
    cover(1)
    screenshot_slide("home.png", "一张图：先查哪里、谁去、什么时候回",
                     ["31 个核查点", "180 个 0.2° 风险格网", "14 天风险推演"],
                     "图斑可一键转案件；风险排序、气象与影像在同一条核查链上。", 3)
    screenshot_slide("yantai.png", "烟台试点：真实影像，规则候选，边界透明",
                     ["Sentinel-2 双时相", "43 个候选 / 24.0 ha", "规则评分 0.71（未校准）"],
                     "许可、河道范围、水位、现场材料列为待核项；不把候选写成违法定性。", 4, "演示环境 · /yantai")
    screenshot_slide("sixg.png", "6G 天枢：网络状态驱动影像推理",
                     ["常态 eMBB", "卫星切换 URLLC", "切片拥塞", "基站中断 NTN"],
                     "时延 / 带宽 / 丢包为数字孪生仿真值，页面与报告标注 simulated。", 5, "演示环境 · /sixg")
    screenshot_slide("workspace-case.png", "案件闭环：四角色、五步流转、证据链",
                     ["指挥员 / 民警 / 复核员 / 管理员", "SHA-256 + 案件级 HMAC", "6 份证据校验通过"],
                     "任何一条证据被改动，证据链校验失败；复核与归档留审计记录。", 6, "演示环境 · /workspace")
    phone_slide(7)
    git_slide(12)
    score_slide(13)
    closing(14)

    # text slides
    text_slide("遥感不是看不见，是看见了接不住", "真实断点",
               ["图斑停在 GIS 图层，进不了案件",
                "现场核查靠电话和聊天工具派单",
                "照片散落在个人手机，对应不到图斑",
                "复核时说不清：谁、何时、改没改"],
               2, quote="需要一条工程链路：图斑 → 案件 → 任务 → 证据 → 复核 → 归档。")

    text_slide("AI 协作：只收“改程序”，不收“想思路”", "方法",
               ["先定位：读代码、查日志、找进程，不先猜",
                "再改：只加不改，保护已经验收的功能",
                "跑验证：接口、页面、控制台、移动端逐项验收",
                "留证据：Prompt 原文、Edit 前后对照、验证命令"],
               8, quote="“不要改我已经做好的，再我做好的基础上往上加。”")

    prompt_slide("主链一 · 法条检索：从“不出结果”到可解释命中",
                 "我输入关键字不出具体的法条这个问题解决一下，要求根据关键字推出对应主要法条。",
                 ["三处根因：前端仅按钮触发；后端整串精确匹配；清单 338 排在 343 前",
                  "加 _norm / _bigrams / 同义词 / 10 条语义规则 / 双字相似度 0.34",
                  "前端 Enter + 350ms 防抖 + 序号丢弃过期响应",
                  "主法条置顶，显示命中原因；1366 宽无横向滚动"],
                 ["占用农田地 → 刑法 342 · score 103", "非法采矿 → 343", "倾倒危险废物 → 338", "六组查询通过"],
                 9)

    prompt_slide("主链二 · 6G 天枢：只加不改，四场景编排",
                 "不要改我已经做好的，再我做好的基础上往上加；把天枢放到右上角，从 GitHub 找开源项目，用 Three.js 做动态效果；科技风强，给我样板选择，最后变成实用的东西。",
                 ["复用约 85% 现有能力；新增 sixg 模块，main.py 仅 3 处追加",
                  "Three.js r128 五节点四链路 + SVG 降级；四套样板选 globe.gl",
                  "网络感知编排：eMBB / URLLC / NTN 按场景切换",
                  "中断场景省 75% 带宽；6G 指标标注为仿真"],
                 ["常态 4/4 瓦片 · 0% 节省", "卫星切换 3/4 · 25%", "切片拥塞 2/4 · 50%", "基站中断 1/4 · 75%"],
                 10)

    prompt_slide("主链三 · 烟台预警：先定位，再修，再验证",
                 "读取烟台非法采矿与河道采砂真实预警实施方案，在中国地区右边再开一个窗口，对现有矿区等资源清晰标注位置，并实现预警。",
                 ["第一次未生效：不是缓存，是 8080 旧进程未加载烟台接口",
                  "修复后升级为独立三栏工作区；入口橙色“烟台预警”自动展开",
                  "修正 EPSG:32651 米制坐标误用；P07 0.54 ha / 54 像元",
                  "全窗口 43 候选 / 24.0 ha；规则评分 0.71（未校准）"],
                 ["用户：并没有在旁边做专项分析", "用户：刷新没用", "AI：查进程与端口", "公网 /yantai 200"],
                 11)

    (SLIDES / "narration.json").write_text(json.dumps(NARRATION, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"slides: {len(list(SLIDES.glob('slide*.png')))}")
    print(f"narration chars: {sum(len(x) for x in NARRATION)}")
    print(f"estimated seconds @2.85 chars/s: {sum(len(x) for x in NARRATION)/2.85:.1f}")


if __name__ == "__main__":
    main()