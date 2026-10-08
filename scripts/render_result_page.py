#!/usr/bin/env python3
"""结果页视觉验收：把真实方案 fixture 注入页面渲染出结果页，截图 + 跑机器判据。

为什么单独一个脚本：visual_acceptance.py 只对根路径截图，覆盖的是着陆页。
结果页要拿到真实方案数据才渲染得出来，P3（票根头卡、等宽数字、侧导航去渐变）改的全是这块，
没有本脚本就是盲改。

判据与 visual_acceptance.py 共用（同一条 EMOJI_RE、同一个渐变白名单、同一套对齐 JS），
避免两处判据漂移。结果页额外断 375px 无横向滚动。

确定性处理：拦截 *.amap.com 请求。本机 AMAP 直连会 502/SSL 失败，底图是否加载成功随机，
拦掉后地图区固定走「加载失败」占位分支，before/after 截图才可比。地图卡片自身的描边与标题条
不受影响，P3 要改的正是那部分。

用法：
    PYTHONPATH=. .venv/bin/python scripts/render_result_page.py --label before
    PYTHONPATH=. .venv/bin/python scripts/render_result_page.py --label after
退出码 0 = 判据全过。截图与报告写入 scripts/evidence/。
"""
import argparse
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from visual_acceptance import (  # noqa: E402  同目录复用判据，避免两处写法漂移
    ALIGNMENT_JS,
    EMOJI_RE,
    GRADIENT_JS,
    is_whitelisted,
    scan_static_emoji,
)

BASE_URL = "http://localhost:8123"
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "scripts" / "evidence"
DEFAULT_FIXTURE = ROOT / "scripts" / "fixtures" / "result-plan.json"

# renderResult 是 app.js 里的顶层函数声明，经典脚本下会挂到 window 上，可直接调用
RENDER_JS = """
(fixture) => {
    if (typeof renderResult !== 'function') {
        throw new Error('renderResult 不是全局函数，无法注入 fixture');
    }
    renderResult(fixture.plan, fixture.payload);
    document.getElementById('landing-page').classList.add('hidden');
    document.getElementById('result-page').classList.remove('hidden');
    window.scrollTo(0, 0);
    return true;
}
"""

# 截图前冻结动画：转圈 spinner / 过渡会让同一份代码截出不同像素
FREEZE_JS = """
() => {
    const style = document.createElement('style');
    style.textContent = '*, *::before, *::after { transition: none !important; animation: none !important; }';
    document.head.appendChild(style);
}
"""

# 展开全部折叠区，让按天卡与 Agent 轨迹都进截图
EXPAND_JS = """
() => {
    document.querySelectorAll('[data-day-body]').forEach((el) => el.classList.remove('hidden'));
    document.querySelectorAll('[data-day-toggle] strong').forEach((el) => {
        el.textContent = el.textContent.replace(/^[›⌄]/, '⌄');
    });
    if (typeof toggleAgentTraceSection === 'function') {
        toggleAgentTraceSection(true);
    }
}
"""


def block_amap(page) -> None:
    page.route(lambda url: "amap.com" in url, lambda route: route.abort())


def render_result(page, fixture: dict) -> None:
    page.goto(BASE_URL, wait_until="networkidle")
    page.evaluate(FREEZE_JS)
    page.evaluate(RENDER_JS, fixture)
    # renderMap 是 async，等它落到占位分支再截图
    try:
        page.wait_for_selector(".map-placeholder", timeout=8000)
    except Exception:
        pass
    page.wait_for_timeout(400)


def collect_checks(page) -> dict:
    """复用 visual_acceptance 的判据，作用在结果页 DOM 上。"""
    checks = {}

    gradient_hits = page.evaluate(GRADIENT_JS)
    decorative = [h for h in gradient_hits if not is_whitelisted(h["selector"])]
    checks["decorative_gradients"] = {
        "pass": len(decorative) == 0,
        "total_gradient_hits": len(gradient_hits),
        "whitelisted": len(gradient_hits) - len(decorative),
        "violations": decorative[:20],
    }

    align = page.evaluate(ALIGNMENT_JS)
    bad_grids = [g for g in align["grids"] if g["maxTopDeviation"] > 1]
    heights = align["controlHeights"]
    checks["alignment"] = {
        "pass": len(bad_grids) == 0, "grids": align["grids"], "violations": bad_grids,
    }
    checks["control_heights"] = {
        "pass": len([h for h in heights if h != 0]) <= 2, "distribution": heights,
    }

    page_emojis = EMOJI_RE.findall(page.evaluate("() => document.body.innerText"))
    static_hits = scan_static_emoji()
    checks["emoji"] = {
        "pass": len(page_emojis) == 0 and all(v == 0 for v in static_hits.values()),
        "page_innerText": len(page_emojis),
        "static_files": static_hits,
    }
    return checks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="result", help="证据文件命名标签（before/after）")
    parser.add_argument("--fixture", default=str(DEFAULT_FIXTURE))
    args = parser.parse_args()

    fixture_path = Path(args.fixture)
    if not fixture_path.exists():
        print(f"fixture 不存在：{fixture_path}\n先跑 scripts/capture_result_fixture.py", file=sys.stderr)
        return 2
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    report = {"label": args.label, "page": "result", "fixture": fixture_path.name, "checks": {}}
    failures = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        page = browser.new_page(viewport={"width": 1280, "height": 900})
        block_amap(page)
        render_result(page, fixture)

        page.screenshot(path=str(EVIDENCE_DIR / f"{args.label}-result-1280.png"), full_page=True)

        report["checks"] = collect_checks(page)

        page.evaluate(EXPAND_JS)
        page.wait_for_timeout(300)
        page.screenshot(path=str(EVIDENCE_DIR / f"{args.label}-result-expanded-1280.png"), full_page=True)
        browser.close()

        browser = p.chromium.launch()
        mobile = browser.new_page(viewport={"width": 375, "height": 800})
        block_amap(mobile)
        render_result(mobile, fixture)
        scroll_w = mobile.evaluate("() => document.documentElement.scrollWidth")
        mobile.screenshot(path=str(EVIDENCE_DIR / f"{args.label}-result-375.png"), full_page=True)
        report["checks"]["mobile_375"] = {"pass": scroll_w <= 376, "scrollWidth": scroll_w}
        browser.close()

    for name, check in report["checks"].items():
        if not check["pass"]:
            if name == "decorative_gradients":
                failures.append(f"装饰性渐变 {check['total_gradient_hits'] - check['whitelisted']} 处")
            elif name == "emoji":
                failures.append(
                    f"emoji 残留 page={check['page_innerText']} static={check['static_files']}"
                )
            elif name == "mobile_375":
                failures.append(f"375px 横向溢出 scrollWidth={check['scrollWidth']}")
            elif name == "control_heights":
                failures.append(f"控件高度不一致 {check['distribution']}")
            else:
                failures.append(f"{name} 未通过")

    report["pass"] = not failures
    report["failures"] = failures
    out = EVIDENCE_DIR / f"{args.label}-result-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"pass": report["pass"], "failures": failures}, ensure_ascii=False))
    print(f"报告: {out}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
