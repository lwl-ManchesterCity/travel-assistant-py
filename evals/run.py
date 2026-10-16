#!/usr/bin/env python3
"""追问（ask）链路的评测集：真服务 + 真 Postgres + 真模型，跑一批题，报一个分。

和 `scripts/` 下那两个探针的分工，一句话（详见 `docs/AGENT_GAPS_20261014.md` §3.1）：

    探针   = 这次改动的**验收单**。题是照着这次改动现编的（「我加了 city 参数，
             那方案在上海、用户问成都呢？」），改完就归档。
    评测集 = agent 的**体检表**。题常驻，不随改动走；每个提示词改动前后各跑一次，比分。

所以这个脚本唯一值得看的东西是最后那行 **`38/41 过`** —— 以及**按类别的拆解**。
「比上次好还是差」是它存在的理由，不是「全过没全过」。

用法（**必须从仓库根跑**：配置按 cwd 找 .env，从别处跑会读不到，表现像「模型不可用」）：

    .venv/bin/python evals/run.py                  # 全跑
    .venv/bin/python evals/run.py --limit 8        # 只跑前 8 题（调判分逻辑时省钱）
    .venv/bin/python evals/run.py --only 记忆      # 只跑类别名里含「记忆」的
    .venv/bin/python evals/run.py --verbose        # 每题的答案原文都打出来

退出码 0 = 全过，1 = 有失败项。测试账号收尾时自清（用户名 probe_evals_ 前缀）。

**这个脚本不发 API key、不写任何凭据**，只跟自己在进程内起的 127.0.0.1 服务说话。
"""

import argparse
import sys
import threading
import time
from collections import Counter
from pathlib import Path

import httpx
import uvicorn

# 从**脚本自己的位置**推仓库根：硬编码绝对路径的话换机器 / 换目录就 ImportError，
# 而报错看着像「模块不存在」。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from evals.judge import judge, load_questions  # noqa: E402
from test_ask_agent import _request, _trip_plan  # noqa: E402
from travel_assistant.main import app  # noqa: E402

PORT = 8304
USERNAME = f"probe_evals_{int(time.time())}"
PASSWORD = "hunter2xyz"


def main() -> int:
    parser = argparse.ArgumentParser(description="追问链路的评测集")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 题")
    parser.add_argument("--only", default="", help="只跑类别名里含这个词的题")
    parser.add_argument("--verbose", action="store_true", help="打印每题答案原文")
    options = parser.parse_args()

    questions = load_questions()
    for row in questions:
        for warning in row.get("_警告") or []:
            print(f"⚠️  {warning}")
    if warnings := [w for row in questions for w in row.get("_警告") or []]:
        print(f"⚠️  题库里有 {len(warnings)} 处认不得的键——那些断言**没跑**，分数是虚高的。\n")

    if options.only:
        questions = [q for q in questions if options.only in q["类别"]]
    if options.limit:
        questions = questions[:options.limit]
    if not questions:
        print("没有选中任何题。")
        return 1

    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        time.sleep(0.1)
        if server.started:
            break

    passed, failed = 0, []
    per_category: Counter = Counter()
    per_category_total: Counter = Counter()

    try:
        # **trust_env=False 不能省**：这台机器上开了系统代理（Clash 之类），而 macOS 上
        # Python 的 getproxies() 不认系统那份 ExceptionsList（里面明明写着 127.0.0.1），
        # 于是 httpx 会把**环回**请求也发给代理，表现是「注册就 502」，看着像应用坏了。
        with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=300.0,
                          trust_env=False) as client:
            response = client.post("/api/auth/register",
                                   json={"username": USERNAME, "password": PASSWORD})
            if response.status_code != 200:
                # 不做成一条 FAIL 项：注册挂了后面全是 401，读起来会以为是模型答错。
                print(f"注册失败（{response.status_code}），下面全是 401，看不出真正的原因。"
                      f"先查 .env 和 Postgres。")
                return 1

            for index, case in enumerate(questions, start=1):
                _forget()      # 每道题从干净上下文起跑，理由见这个函数的注释
                body = _ask(client, case)
                reasons = judge(case, body)
                category = case["类别"]
                per_category_total[category] += 1
                if reasons:
                    failed.append((index, case, reasons, body))
                else:
                    passed += 1
                    per_category[category] += 1

                flag = "FAIL" if reasons else "PASS"
                print(f"[{index:>2}] {flag}  {category:<14} {case['问']}")
                for reason in reasons:
                    print(f"          · {reason}")
                if options.verbose:
                    # **过的题也打**：验一处修复时最想看的恰恰是「它现在答了什么」，
                    # 只在失败时打的话，修好了反而什么都看不见。
                    print(f"          答案：{(body.get('answer') or '（空）')[:160]}")
    finally:
        cleanup()

    total = len(questions)
    print()
    print("=" * 60)
    print(f"{passed}/{total} 过" + (f"　（{options.only}）" if options.only else ""))
    print()
    print("按类别：")
    for category in dict.fromkeys(q["类别"] for q in questions):
        got, whole = per_category[category], per_category_total[category]
        mark = "" if got == whole else "   ←"
        print(f"  {category:<16} {got}/{whole}{mark}")
    print("=" * 60)

    if failed:
        print()
        print("没过的题（判分逻辑在 evals/judge.py，题目在 evals/questions.yaml）：")
        for index, case, reasons, _ in failed:
            print(f"  [{index}] {case['问']}  ({case['类别']})")
            for reason in reasons:
                print(f"        · {reason}")

    return 1 if failed else 0


def _ask(client: httpx.Client, case: dict) -> dict:
    """发一道题。**任何异常都转成一个「什么都缺」的 body**，交给 judge 去判失败——
    在跑批中间抛出去的话，前面跑过的题就白跑了。
    """
    city = case.get("城市") or "杭州"
    # city 改的是**方案**那一份：第二跳取的城市是它（见 docs/ASK_LIVE_HOURS.md §3.5）。
    # 原始请求那一份跟着改只是让请求自洽。
    plan = _trip_plan().model_copy(update={"city": city})
    payload = {
        "question": case["问"],
        "tripPlan": plan.model_dump(by_alias=True),
        "request": _request(city).model_dump(by_alias=True),
    }
    if history := case.get("历史"):
        payload["history"] = history
    try:
        response = client.post("/api/trips/ask", json=payload)
    except Exception as exception:  # noqa: BLE001 - 网络层的任何问题都该变成一道 FAIL，不是崩掉
        return _empty(f"请求就挂了：{exception}")
    if response.status_code != 200:
        return _empty(f"HTTP {response.status_code}：{response.text[:200]}")
    return response.json()


def _empty(note: str) -> dict:
    """一道「什么都是空的」响应。

    这样 `judge` 会把它判成失败，而且失败原因读得出来是哪一类缺——
    比在这里提前 return 一个"跳过"要好：**跳过会让分数虚高**。
    """
    return {"intent": None, "answer": "", "traces": [], "recordedPreferences": [],
            "anchorHint": None, "_note": note}


def _forget() -> None:
    """每道题开跑前，把这个测试账号攒下的偏好和场景清空。

    **不清的话，40 道题就不是 40 个独立样本**：长期偏好是一轮轮攒的，后面的题是在
    被前面污染过的上下文里答的。第一次跑就是这么露的马脚——第 34 题「这一趟想轻松点」
    的答案里冒出一句「你记着的…带小孩」，可那题压根没人提过带小孩。

    串味会让分数**两头都不准**：一道题答错可能带崩后面几道，而这种崩法看着像
    另一个功能坏了。评测集要的是「每题单独看它答得对不对」，不是「连着聊 40 轮会怎样」。

    （想验多轮就不一样了——那是 `历史:` 那个字段的活，它是有意把上下文给到某道题的。）
    """
    try:
        from travel_assistant import db
        with db.connection() as conn:
            for table in ("preferences", "episodes"):
                conn.execute(
                    f"delete from {table} where user_id = "
                    "(select id from users where username = %s)", (USERNAME,))
    except Exception as exception:  # noqa: BLE001 - 清不干净不该让整轮跑不起来，但必须吵
        print(f"（清上下文没清干净，后面几道题可能串味：{exception}）")


def cleanup() -> None:
    """测试账号用完就删——跑一次留一个用户，跑几十次库里就全是垃圾。"""
    try:
        from travel_assistant import db
        with db.connection() as conn:
            conn.execute("delete from users where username like 'probe_evals_%'")
    except Exception as exception:  # noqa: BLE001 - 收尾失败不该把分数盖掉
        print(f"（收尾没清干净，不影响上面的分数：{exception}）")


if __name__ == "__main__":
    raise SystemExit(main())
