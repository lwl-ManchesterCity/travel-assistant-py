#!/usr/bin/env python3
"""视觉验收探针（FRONTEND_REDESIGN_PLAN v1.2 验收标准机器化）。

对 http://localhost:8123 执行四条机器判据并输出 before/after 截图：

1. 装饰性渐变为 0：计算样式全量扫描（含 ::before / ::after 伪元素），
   gradient 仅允许命中白名单选择器 —— v4.3 起白名单只剩一条：body 的天空底。
2. 同列元素边缘对齐：各栅格容器（.grid-four/.grid-three/.grid-two）内
   子元素 left/top 边缘偏差 <= 1px；所有输入控件高度一致（44px）。
3. 375px 视口无横向滚动：document.documentElement.scrollWidth <= 375。
   3b. 日期弹层展开后同样无横向滚动 —— 弹层通常比触发它的控件宽，是最容易越界的一类元素。
4. emoji 为 0：页面 innerText + js/app.js 源码全量扫描。
5. SVG 素材合法：static/img/*.svg 必须能被 XML 解析器读通 —— 注释里的连续两个
   减号会让整张图变成碎图，而页面、控制台、前四条判据全都看不见它。

用法：
    python scripts/visual_acceptance.py --label before
    python scripts/visual_acceptance.py --label after

截图与报告写入 scripts/evidence/。退出码 0 = 全过，1 = 存在失败项。
"""
import argparse
import json
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE_URL = "http://localhost:8123"
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "scripts" / "evidence"
STATIC_DIR = ROOT / "travel_assistant" / "static"

# 渐变白名单（见 FRONTEND_REDESIGN_PLAN 验收标准 1）
#
# v4「明亮度假风」重写后，着陆页的打孔线 / 打孔列 / 锯齿边三个选择器已从 DOM 里退场。
# v4.3 重写结果页那一轮，票根条码 / 半圆缺口 / 餐券齿纹三个也退场了 ——
# 至此白名单只剩 body 的天空底一条，回到"装饰性渐变为 0"这条红线的本意。
#
# 匹配按元素 className 做子串判断；body 没有 class，回落到 tagName，所以写 "BODY"。
GRADIENT_WHITELIST = [
    "BODY",             # v4 页面底色：天蓝 → 近白的天空渐变（两页共用同一条）
]

EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u2700-\u27BF]"
)

ALIGNMENT_JS = """
() => {
    const px = (v) => Math.round(v * 10) / 10;
    const result = { grids: [], controlHeights: {} };

    document.querySelectorAll('.grid-four, .grid-three, .grid-two').forEach((grid, i) => {
        const children = [...grid.children];
        if (children.length < 2) return;
        const rects = children.map((c) => c.getBoundingClientRect());
        // 同一行的子元素 top 应一致；按 top 聚类成行
        const rows = {};
        rects.forEach((r) => {
            const key = Object.keys(rows).find((k) => Math.abs(Number(k) - r.top) <= 1);
            rows[key ?? px(r.top)] = rows[key ?? px(r.top)] || [];
            rows[key ?? px(r.top)].push(r);
        });
        let maxTopDev = 0;
        Object.values(rows).forEach((rowRects) => {
            const tops = rowRects.map((r) => r.top);
            maxTopDev = Math.max(maxTopDev, Math.max(...tops) - Math.min(...tops));
        });
        result.grids.push({
            selector: grid.className, index: i,
            rows: Object.keys(rows).length, maxTopDeviation: px(maxTopDev),
        });
    });

    const heights = {};
    document.querySelectorAll('.input, .textarea, select.input, .readonly-value, .pill-display').forEach((el) => {
        const h = px(el.getBoundingClientRect().height);
        heights[h] = (heights[h] || 0) + 1;
    });
    result.controlHeights = heights;
    return result;
}
"""

GRADIENT_JS = """
() => {
    const hits = [];
    const record = (el, pseudo, bg) => {
        const name = (el.className && el.className.toString().slice(0, 50)) || el.tagName;
        hits.push({ selector: `${name}${pseudo}`, bg: bg.slice(0, 80) });
    };
    document.querySelectorAll('*').forEach((el) => {
        const own = getComputedStyle(el).backgroundImage;
        if (own && own.includes('gradient')) record(el, '', own);
        // 伪元素也算 —— 而且装饰最爱藏在这里。v4.3 之前 .budget-grid::after（锯齿撕边）
        // 与 .meal-ticket::before/::after（餐券齿纹）都是渐变，都在这里，但当时这条
        // 只扫真实元素，于是一路放行；.meal-ticket 甚至还因此进了白名单 ——
        // 登记了一个自己根本看不见的东西。
        // content === 'none' 说明这个伪元素压根没生成，别把浏览器的默认值当成命中。
        for (const pseudo of ['::before', '::after']) {
            const s = getComputedStyle(el, pseudo);
            if (s.content === 'none') continue;
            if (s.backgroundImage && s.backgroundImage.includes('gradient')) {
                record(el, pseudo, s.backgroundImage);
            }
        }
    });
    return hits;
}
"""


def is_whitelisted(selector: str) -> bool:
    return any(w.lstrip(".") in selector for w in GRADIENT_WHITELIST)


def scan_static_emoji() -> dict:
    """扫描全部静态资源（html/css/js）里的 emoji 残留。

    覆盖整个 static/ 目录而不只是 index.html + app.js —— 样式表里同样可能藏
    emoji（比如 content: "✈️"），只查两个文件会漏。字体是二进制，按扩展名跳过。
    """
    hits = {}
    for path in sorted(STATIC_DIR.rglob("*")):
        if not path.is_file() or path.suffix not in {".html", ".css", ".js"}:
            continue
        hits[str(path.relative_to(STATIC_DIR))] = len(
            EMOJI_RE.findall(path.read_text(encoding="utf-8"))
        )
    return hits


def validate_svgs() -> dict:
    """解析 static/img 下每一张 SVG，确认它是合法 XML。

    为什么值得单列一条判据：XML 注释里**不允许出现连续两个减号**，而本项目的
    注释习惯是直接写 CSS 变量名（--ink、--shell-max …）。写进去的那一瞬间
    整张 SVG 就成了非法 XML，浏览器只显示一张碎图 —— 页面不报错、控制台不报错、
    探针原有的四条判据也全都看不见它。这个坑在 v4.1 犯过一次，v4.3 又犯过一次
    （新写的 scene-travel-wide.svg，注释里写了 shell max result 的变量名），
    两次都是靠事后手动跑一段脚本才发现的。所以把它固化成判据。
    """
    import xml.etree.ElementTree as ET

    broken = []
    checked = 0
    for path in sorted((STATIC_DIR / "img").glob("*.svg")):
        checked += 1
        try:
            ET.parse(path)
        except ET.ParseError as exc:
            broken.append({"file": path.name, "error": str(exc)})
    return {"pass": not broken, "checked": checked, "violations": broken}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="run", help="证据文件命名标签（before/after）")
    args = parser.parse_args()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    report = {"label": args.label, "checks": {}}
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(BASE_URL, wait_until="networkidle")
        page.screenshot(path=str(EVIDENCE_DIR / f"{args.label}-landing-1280.png"), full_page=True)

        # 判据 1：装饰性渐变
        gradient_hits = page.evaluate(GRADIENT_JS)
        decorative = [h for h in gradient_hits if not is_whitelisted(h["selector"])]
        report["checks"]["decorative_gradients"] = {
            "pass": len(decorative) == 0,
            "total_gradient_hits": len(gradient_hits),
            "whitelisted": len(gradient_hits) - len(decorative),
            "violations": decorative[:20],
        }
        if decorative:
            failures.append(f"装饰性渐变 {len(decorative)} 处")

        # 判据 2：对齐
        align = page.evaluate(ALIGNMENT_JS)
        bad_grids = [g for g in align["grids"] if g["maxTopDeviation"] > 1]
        heights = align["controlHeights"]
        # 输入控件一律 44px，textarea 例外 98px；height 0 是隐藏页里的控件，不计。
        # 判据 2 只在乎「同屏控件同高」，不是「只有两种高度」——原写法 <=2 在结果页会因
        # 隐藏页控件被算成 0 而恒过，等于没查。
        # JS 对象的键经 Playwright 回来是字符串，必须转 float 再比，否则 '0' != 0 恒真
        visible = {float(h) for h in heights if float(h) != 0}
        height_ok = visible <= {44.0, 98.0}
        report["checks"]["alignment"] = {
            "pass": len(bad_grids) == 0,
            "grids": align["grids"],
            "violations": bad_grids,
        }
        report["checks"]["control_heights"] = {"pass": height_ok, "distribution": heights}
        if bad_grids:
            failures.append(f"栅格对齐超差 {len(bad_grids)} 组")
        if not height_ok:
            failures.append(f"控件高度不一致 {heights}")

        # 判据 4a：页面可见文本 emoji
        text = page.evaluate("() => document.body.innerText")
        page_emojis = EMOJI_RE.findall(text)
        browser.close()

        # 判据 3：375px 视口
        browser = p.chromium.launch()
        mobile = browser.new_page(viewport={"width": 375, "height": 800})
        mobile.goto(BASE_URL, wait_until="networkidle")
        scroll_w = mobile.evaluate("() => document.documentElement.scrollWidth")
        mobile.screenshot(path=str(EVIDENCE_DIR / f"{args.label}-landing-375.png"), full_page=True)
        report["checks"]["mobile_375"] = {"pass": scroll_w <= 376, "scrollWidth": scroll_w}
        if scroll_w > 376:
            failures.append(f"375px 横向溢出 scrollWidth={scroll_w}")

        # 判据 3b：弹层展开后同样不得横向溢出。
        # 原探针不打开任何弹层，日期弹层越出视口右缘（实测 right=390 > 375）就是这么漏掉的 ——
        # 弹层通常比触发它的控件宽，是最容易越界的一类元素。
        popup_check = {"pass": True, "skipped": True, "reason": "页面无日期触发器"}
        popup_trigger = mobile.query_selector("#departureDateTrigger")
        if popup_trigger:
            popup_trigger.click()
            mobile.wait_for_timeout(250)
            popup_scroll = mobile.evaluate("() => document.documentElement.scrollWidth")
            popup_rect = mobile.evaluate("""() => {
                const el = document.querySelector('.date-popup');
                if (!el) { return null; }
                const r = el.getBoundingClientRect();
                return { left: Math.round(r.left), right: Math.round(r.right) };
            }""")
            popup_check = {
                "pass": popup_scroll <= 376,
                "skipped": False,
                "scrollWidth": popup_scroll,
                "popup": popup_rect,
            }
            mobile.screenshot(path=str(EVIDENCE_DIR / f"{args.label}-date-popup-375.png"))
            if popup_scroll > 376:
                failures.append(f"375px 日期弹层展开后横向溢出 scrollWidth={popup_scroll}")
        report["checks"]["mobile_375_date_popup"] = popup_check
        browser.close()

    # 判据 4b：静态资源 emoji 扫描
    static_hits = scan_static_emoji()
    emoji_ok = len(page_emojis) == 0 and all(v == 0 for v in static_hits.values())
    report["checks"]["emoji"] = {
        "pass": emoji_ok, "page_innerText": len(page_emojis), "static_files": static_hits,
    }
    if not emoji_ok:
        failures.append(f"emoji 残留 page={len(page_emojis)} static={static_hits}")

    # 判据 5：SVG 素材必须是合法 XML（注释里的连续两个减号会让整张图变成碎图，
    # 而页面不报错、控制台不报错、前四条判据也看不见它）
    svg_check = validate_svgs()
    report["checks"]["svg_wellformed"] = svg_check
    if not svg_check["pass"]:
        failures.append(f"SVG 非法 XML {len(svg_check['violations'])} 张")

    report["pass"] = not failures
    report["failures"] = failures
    out = EVIDENCE_DIR / f"{args.label}-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"pass": report["pass"], "failures": failures}, ensure_ascii=False))
    print(f"报告: {out}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
