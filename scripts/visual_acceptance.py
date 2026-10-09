#!/usr/bin/env python3
"""视觉验收探针（FRONTEND_REDESIGN_PLAN v1.2 验收标准机器化）。

对 http://localhost:8123 执行四条机器判据并输出 before/after 截图：

1. 装饰性渐变为 0：计算样式全量扫描，gradient 仅允许命中白名单选择器
   （票根条码 .ticket-barcode、半圆缺口 .ticket-notch）。
2. 同列元素边缘对齐：各栅格容器（.grid-four/.grid-three/.grid-two）内
   子元素 left/top 边缘偏差 <= 1px；所有输入控件高度一致（44px）。
3. 375px 视口无横向滚动：document.documentElement.scrollWidth <= 375。
   3b. 日期弹层展开后同样无横向滚动 —— 弹层通常比触发它的控件宽，是最容易越界的一类元素。
4. emoji 为 0：页面 innerText + js/app.js 源码全量扫描。

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
# .perforation 是着陆页委托单的打孔分隔线（FRONTEND_FEEDBACK_LANDING_TICKET 修复 1），
# 与结果页票根同一画法：缺口用 radial-gradient 画，属白名单 #2 的同类用法。
# .punch-holes 是委托单左缘的打孔列（FRONTEND_FEEDBACK_CREATIVE_MOTION 辅助创意 A），
# 同样是纸色圆压白卡，同一类用法。
# 匹配按元素 className 做子串判断，故缺口的类名（.perforation-notch）也含 "perforation"。
# .meal-ticket 是餐饮券上下边的锯齿齿纹（FRONTEND_FEEDBACK_LINE_COLORS 修复 2），
# 用 45 度线性渐变画三角齿，是该单唯一放行的装饰渐变。
# .form-zigzag 是着陆页表单外壳顶边的三角撕裂齿（FRONTEND_FEEDBACK_LANDING_BOLD 修复 2），
# 与餐券同一套齿纹画法。
# 反馈单红线：白名单封顶这六个选择器，不得再增。
GRADIENT_WHITELIST = [
    ".ticket-barcode",
    ".ticket-notch",
    ".perforation",
    ".punch-holes",
    ".meal-ticket",
    ".form-zigzag",
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
    document.querySelectorAll('*').forEach((el) => {
        const bg = getComputedStyle(el).backgroundImage;
        if (bg && bg.includes('gradient')) {
            hits.push({ selector: el.className.toString().slice(0, 60) || el.tagName, bg: bg.slice(0, 80) });
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

    report["pass"] = not failures
    report["failures"] = failures
    out = EVIDENCE_DIR / f"{args.label}-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"pass": report["pass"], "failures": failures}, ensure_ascii=False))
    print(f"报告: {out}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
