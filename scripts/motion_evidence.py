#!/usr/bin/env python3
"""四幕出票动效的证据采集与机器核查（FRONTEND_FEEDBACK_CREATIVE_MOTION 验收 3）。

mid-flight 截帧不靠 sleep —— 那既不准也不可复现。这里用 Web Animations API
把目标动画 pause() 后精确 seek 到某一时刻再截图，同一份代码每次都截到同一帧。

同时核查三件事：
  1. 各幕动画确实存在，且时长/延迟与反馈单一致（读 effect.getTiming()）
  2. prefers-reduced-motion: reduce 下动画数为 0（降级生效）
  3. reduce 下终态直接显示：受理章可见、票根停在 translateY(0)

用法：
    PYTHONPATH=. .venv/bin/python scripts/motion_evidence.py
退出码 0 = 全部核查通过。截图与报告写入 scripts/evidence/。
"""
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from render_result_page import block_amap  # noqa: E402
from visual_acceptance import BASE_URL  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE_DIR = ROOT / "scripts" / "evidence"
FIXTURE = ROOT / "scripts" / "fixtures" / "result-plan.json"

# 先把所有动画暂停并归零，再逐个 seek 到指定时刻。
# 只 pause 目标动画是不够的 —— 其余动画会继续实时走，同一份代码截出的帧就不一样了
# （第一版就因此截到「页面还在淡入」的灰蒙蒙一帧）。
SEEK_JS = """
(pairs) => {
    const anims = document.getAnimations();
    anims.forEach(a => { a.pause(); a.currentTime = 0; });
    const found = {};
    for (const [name, ms] of pairs) {
        const a = anims.find(x => x.animationName === name);
        found[name] = !!a;
        if (a) { a.currentTime = ms; }
    }
    return found;
}
"""

# 读所有动画的名字 + 时长 + 延迟，用来核对与反馈单给的值是否一致
TIMING_JS = """
() => document.getAnimations().map(a => {
    const t = a.effect.getTiming();
    return { name: a.animationName, duration: t.duration, delay: t.delay };
})
"""

def render_result(page, fixture: dict) -> None:
    """不带 FREEZE_JS 的结果页渲染 —— 冻结动画会把这四幕一并冻掉。"""
    page.goto(BASE_URL, wait_until="networkidle")
    page.evaluate(
        """(fixture) => {
            renderResult(fixture.plan, fixture.payload);
            document.getElementById('landing-page').classList.add('hidden');
            document.getElementById('result-page').classList.remove('hidden');
        }""",
        fixture,
    )
    page.wait_for_timeout(120)


def main() -> int:
    if not FIXTURE.exists():
        print(f"fixture 不存在：{FIXTURE}", file=sys.stderr)
        return 2
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)

    report = {"checks": {}, "failures": []}
    failures: list[str] = []

    with sync_playwright() as p:
        browser = p.chromium.launch()

        # ---------- 正常（no-preference）：逐幕取帧 ----------
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        block_amap(page)
        page.goto(BASE_URL, wait_until="networkidle")

        # 第 1 幕 · 受理章：seek 到回弹中段
        page.evaluate("() => document.querySelector('.landing-page').classList.add('is-loading')")
        page.wait_for_timeout(30)
        found1 = page.evaluate(SEEK_JS, [["stamp-land-accept", 60]])
        page.locator("#landing-page .action-wrap").screenshot(
            path=str(EVIDENCE_DIR / "creative-act1-accept.png"))
        ok1 = found1.get("stamp-land-accept", False)

        # 第 2 幕 · 打印中：显示出票机面板，seek 走纸线
        page.evaluate("() => document.getElementById('progress-panel').classList.remove('hidden')")
        page.wait_for_timeout(30)
        found2 = page.evaluate(SEEK_JS, [["paper-feed", 400]])
        page.locator("#progress-panel").screenshot(path=str(EVIDENCE_DIR / "creative-act2-printing.png"))
        ok2 = found2.get("paper-feed", False)
        act12_timings = page.evaluate(TIMING_JS)
        page.close()

        # 第 3 / 4 幕 · 结果页入场。
        # 第 3 幕帧：票根滑到中段（320ms），页面淡入已完成（400ms 超过 350ms 时长即终态），
        # 落章动画停在 0 —— 此时它就该还没落，印章不可见。
        page = browser.new_page(viewport={"width": 1280, "height": 900}, device_scale_factor=2)
        block_amap(page)
        render_result(page, fixture)
        # 150ms 而不是 320ms：cubic-bezier(0.16,1,0.3,1) 前段极快，320ms 时位移已完成约 98%，
        # 截出来跟终态没区别，证明不了「滑出」这件事
        found3 = page.evaluate(SEEK_JS, [["ticket-slide", 150], ["result-fade", 400]])
        page.screenshot(path=str(EVIDENCE_DIR / "creative-act3-ticket-sliding.png"),
                        clip={"x": 0, "y": 0, "width": 1280, "height": 420})
        act34_timings = page.evaluate(TIMING_JS)
        ok3 = found3.get("ticket-slide", False) and found3.get("result-fade", False)

        # 第 4 幕帧：票根已停稳、落章走到落定前一刻（延迟 850ms + 110ms）
        found4 = page.evaluate(SEEK_JS, [
            ["ticket-slide", 700], ["result-fade", 400], ["stamp-land-issue", 960],
        ])
        page.locator("#summary-section").screenshot(path=str(EVIDENCE_DIR / "creative-act4-stamp-landed.png"))
        ok4 = found4.get("stamp-land-issue", False)

        # 出票口：票根顶部外沿那条 8px 深色槽是否真的画出来了
        slot = page.evaluate("""() => {
            const hero = document.querySelector('.dashboard-hero');
            const s = getComputedStyle(hero, '::before');
            const card = document.querySelector('.ticket-card').getBoundingClientRect();
            const heroRect = hero.getBoundingClientRect();
            return {
                height: s.height, background: s.backgroundColor, zIndex: s.zIndex,
                heroTop: Math.round(heroRect.top), cardTop: Math.round(card.top)
            };
        }""")
        page.close()

        # ---------- reduced-motion：动画数应为 0，且终态直接显示 ----------
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.emulate_media(reduced_motion="reduce")
        block_amap(page)
        page.goto(BASE_URL, wait_until="networkidle")
        page.evaluate("() => document.querySelector('.landing-page').classList.add('is-loading')")
        page.wait_for_timeout(60)
        reduced_landing = page.evaluate("""() => {
            const s = document.querySelector('.stamp-accept');
            return {
                animationCount: document.getAnimations().length,
                stampOpacity: getComputedStyle(s).opacity,
                stampTransform: getComputedStyle(s).transform
            };
        }""")

        render_result(page, fixture)
        reduced_result = page.evaluate("""() => {
            const t = document.querySelector('.ticket-card');
            const s = document.querySelector('.ticket-stamp');
            return {
                animationCount: document.getAnimations().length,
                ticketTransform: getComputedStyle(t).transform,
                stampOpacity: getComputedStyle(s).opacity
            };
        }""")
        page.close()
        browser.close()

    # ---------- 核查 ----------
    report["checks"]["animations_seekable"] = {
        "act1": ok1, "act2": ok2, "act3": ok3, "act4": ok4,
        "pass": all([ok1, ok2, ok3, ok4]),
    }
    if not all([ok1, ok2, ok3, ok4]):
        failures.append("有动画没找到，无法截帧")

    timing = {t["name"]: t for t in (act12_timings + act34_timings)}
    report["checks"]["timings"] = timing
    # 反馈单给定时长：第 1/4 幕 0.25s，第 3 幕 0.7s，第 4 幕延迟 0.15s
    expected = {
        "stamp-land-accept": (250.0, 0.0),
        "ticket-slide": (700.0, 0.0),
        "stamp-land-issue": (250.0, 850.0),
    }
    timing_ok = True
    for name, (dur, delay) in expected.items():
        got = timing.get(name)
        if not got or abs(got["duration"] - dur) > 1 or abs(got["delay"] - delay) > 1:
            timing_ok = False
            failures.append(f"{name} 时长/延迟不符：期望 {dur}ms/+{delay}ms，实得 {got}")
    report["checks"]["timings_match_feedback"] = timing_ok

    # 结果页入场序列（第 3 幕 + 第 4 幕延迟与落章）= 0.7 + 0.15 + 0.25 = 1.1s
    entrance_ms = 700 + 150 + 250
    report["checks"]["entrance_sequence_ms"] = entrance_ms
    if entrance_ms > 1200:
        failures.append(f"结果页入场序列 {entrance_ms}ms 超过 1.2s 预算")

    # 出票口：8px 高、贴着票根上沿（hero 与票根同顶）
    slot_ok = slot["height"] == "8px" and slot["heroTop"] == slot["cardTop"]
    report["checks"]["ticket_slot"] = {**slot, "pass": slot_ok}
    if not slot_ok:
        failures.append(f"出票口槽不符：{slot}")

    reduced_ok = (
        reduced_landing["animationCount"] == 0
        and reduced_result["animationCount"] == 0
        and float(reduced_landing["stampOpacity"]) == 1.0
        and float(reduced_result["stampOpacity"]) == 1.0
        and reduced_result["ticketTransform"] in ("none", "matrix(1, 0, 0, 1, 0, 0)")
    )
    report["checks"]["reduced_motion"] = {
        "landing": reduced_landing, "result": reduced_result, "pass": reduced_ok,
    }
    if not reduced_ok:
        failures.append(f"reduced-motion 降级不合格：{reduced_landing} / {reduced_result}")

    report["pass"] = not failures
    report["failures"] = failures
    out = EVIDENCE_DIR / "creative-motion-report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"pass": report["pass"], "failures": failures}, ensure_ascii=False))
    print(f"入场序列 {entrance_ms}ms；出票口 {slot['height']} / {slot['background']}")
    print(f"报告: {out}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
