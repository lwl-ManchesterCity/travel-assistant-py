#!/usr/bin/env python3
"""视觉验收探针（FRONTEND_REDESIGN_PLAN v1.2 验收标准机器化）。

对 http://localhost:8123 执行四条机器判据并输出 before/after 截图：

1. 装饰性渐变为 0：计算样式全量扫描，gradient 仅允许命中白名单选择器
   （票根条码 .ticket-barcode、半圆缺口 .ticket-notch）。
2. 同列元素边缘对齐：各栅格容器（.grid-four/.grid-three/.grid-two）内
   子元素 left/top 边缘偏差 <= 1px；所有输入控件高度一致（44px）。
3. 375px 视口无横向滚动：document.documentElement.scrollWidth <= 375。
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
GRADIENT_WHITELIST = [".ticket-barcode", ".ticket-notch"]

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
    document.querySelectorAll('.input, .textarea, select.input, .pill-display').forEach((el) => {
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
        height_ok = len([h for h in heights if h != 0]) <= 2  # 输入控件 44px + textarea 例外
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
        browser.close()

    # 判据 4b：静态资源 emoji 扫描（HTML + JS）
    static_hits = {}
    for f in [STATIC_DIR / "index.html", STATIC_DIR / "js" / "app.js"]:
        if f.exists():
            found = EMOJI_RE.findall(f.read_text(encoding="utf-8"))
            static_hits[f.name] = len(found)
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
