#!/usr/bin/env python3
"""长期记忆的端到端验收：真服务 + 真 Postgres + 真模型。

判据见 docs/ASK_LONGTERM_MEMORY.md —— 覆盖五档里的四档：

    第二档 记偏好   §2.2.9 判据 1/2/3/4
    第五档 角色     §2.5.6 判据 1/2/5（同行的人要记、且标出是谁）
    第四档 场景     §2.4.7 判据 1/2/3/4/5（按城市取、换城市不带）
    第三档 看偏好   §2.3.6 判据 1/2/3/4/5（列表、删除、删干净能重记、删不掉别人的）

第一档的登录由 scripts/ 下另两个探针守（它们还在 /tmp，见 §四 遗留）。

**这是唯一能证明整条链真的接通了的**那层：单元测试跑的是 InMemory 实现，
它证明不了真库那句 on conflict 认不认唯一键、也证明不了 HTTP 那条路上
user_id 有没有传到底、更证明不了"删掉之后真的不再进提示词"。

方案直接 POST 一份合成方案（AskRequest 本来就把 trip_plan 放在请求体里），
所以不用先花 40~70 秒生成一次真行程——这几档要验的是记忆，不是规划。

用法（**必须从仓库根跑**：配置按 cwd 找 .env，从别处跑会读不到，表现像"模型不可用"）：

    .venv/bin/python scripts/preference_probe.py

退出码 0 = 全过，1 = 有失败项。测试账号收尾时自清（用户名 probe_pref_ 前缀）。

判据 5/6（表单备注那一侧）不在这里：它们要走完整的规划链，代价是每次一次真行程。
那两条由 tests/test_requirement_analysis_service.py 从"喂给模型的输入干不干净"那一面守。
"""
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

# 从**脚本自己的位置**推仓库根：以前这里硬编码着一台机器的绝对路径，
# 换机器 / 换目录就 ImportError，而报错看着像"模块不存在"。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from travel_assistant.main import app  # noqa: E402
from test_ask_agent import _request, _trip_plan  # noqa: E402

PORT = 8302
USERNAME = f"probe_pref_{int(time.time())}"
PASSWORD = "hunter2xyz"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  → {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def stored(user_id: int) -> list[tuple[str, str]]:
    """直接查库，不经过任何应用代码——探针要看到的就是库里那几行。"""
    from travel_assistant import db
    with db.connection() as conn:
        rows = conn.execute(
            "select content, source from preferences where user_id = %s order by id",
            (user_id,)).fetchall()
    return [(row[0], row[1]) for row in rows]


def main() -> int:
    config = uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(80):
        time.sleep(0.1)
        if server.started:
            break

    base = f"http://127.0.0.1:{PORT}"
    try:
        # **trust_env=False 不能省**：这台机器上开了系统代理（Clash 之类，127.0.0.1:7897），
        # 而 macOS 上 Python 的 getproxies() **不认系统那份 ExceptionsList**
        # （那份里明明写着 127.0.0.1 / localhost）。于是 httpx 会把**环回**请求也发给代理，
        # 代理回一个 502，表现是"注册就 502"——像是应用坏了，其实一行代码都没关系。
        # 本探针只跟自己起的 127.0.0.1 服务说话，永远不该走代理。
        # （curl 不复现：curl 认那个例外名单。）
        with httpx.Client(base_url=base, timeout=180.0, trust_env=False) as client:
            response = client.post("/api/auth/register",
                                   json={"username": USERNAME, "password": PASSWORD})
            check("注册成功（否则下面全是 401，看不出真正的原因）", response.status_code == 200,
                  str(response.status_code))

            me = client.get("/api/auth/me").json()
            user_id = me.get("id")
            check("拿到 user_id（偏好就是按它存的）", isinstance(user_id, int), repr(me)[:120])
            if not isinstance(user_id, int):
                return 1

            def ask(question: str, city: str = "杭州") -> dict:
                # city 改的是**方案**那一份：场景是按方案的城市取的（§2.4.3）。
                # 原始请求那一份跟着改只是为了让请求自洽。
                plan = _trip_plan().model_copy(update={"city": city})
                payload = {
                    "question": question,
                    "tripPlan": plan.model_dump(by_alias=True),
                    "request": _request(city).model_dump(by_alias=True),
                }
                return client.post("/api/trips/ask", json=payload).json()

            def remembered(body: dict) -> list[str]:
                return body.get("recordedPreferences") or []

            def memory_trace(body: dict) -> str:
                """记忆那一条留痕的 inputSummary——判据 2/3 靠它看出注入了 0 条还是 1 条。"""
                for trace in body.get("traces") or []:
                    if trace.get("agentName") == "MemoryService":
                        return trace.get("inputSummary") or ""
                return ""

            def memory_trace_all(body: dict) -> str:
                """那条留痕的**整段**（input + output）。

                第三档判据 3 要问的是"某一条具体偏好还在不在提示词里"，
                而它只出现在 outputSummary 里（inputSummary 报的是条数）。
                """
                for trace in body.get("traces") or []:
                    if trace.get("agentName") == "MemoryService":
                        return f"{trace.get('inputSummary') or ''} {trace.get('outputSummary') or ''}"
                return ""

            def episodes(user_id: int) -> list[tuple[str, str, str]]:
                from travel_assistant import db
                with db.connection() as conn:
                    rows = conn.execute(
                        "select city, content, source from episodes "
                        "where user_id = %s order by id", (user_id,)).fetchall()
                return [(row[0], row[1], row[2]) for row in rows]

            def preference_hits(user_id: int, needle: str) -> int:
                """偏好表里有几条含这个词——判据 5（不跨桶）查的就是它。"""
                from travel_assistant import db
                with db.connection() as conn:
                    return conn.execute(
                        "select count(*) from preferences where user_id = %s and content like %s",
                        (user_id, f"%{needle}%")).fetchone()[0]

            def subject_pairs(user_id: int) -> list[tuple[str, str]]:
                """(subject, content) 逐行——第五档要看的就是 subject 那一格。"""
                from travel_assistant import db
                with db.connection() as conn:
                    rows = conn.execute(
                        "select subject, content from preferences where user_id = %s order by id",
                        (user_id,)).fetchall()
                return [(row[0], row[1]) for row in rows]

            # ---------- 判据 1：说了就记住 ----------
            body = ask("我不能吃辣")
            # 链没通（模型不可用、方案不合法……）就当场停：不停的话下面每一条"没多出来"
            # 都会**空过**，看起来全绿，其实什么都没验。这一条栽过一次。
            if not (body.get("answer") or "").strip():
                print()
                print(f"追问链没通，后面几条会空过，直接停：{json.dumps(body, ensure_ascii=False)[:300]}")
                failures.append("追问链没通")
                return 1
            rows = stored(user_id)
            # 不钉死措辞：模型会把「我不能吃辣」规范化成「不吃辣」（规则 13 要求
            # "只写偏好本身、不要带主语"），那是对的。探针验的是**管道**——
            # 回的那行 == 库里那行、来源是 chat、内容说的是辣椒这件事。
            check("判据 1 库里正好多了一条，来源是 chat",
                  len(rows) == 1 and rows[0][1] == "chat", repr(rows))
            check("判据 1 回的那行就是刚存进去的那条",
                  remembered(body) == [rows[0][0]], f"回={remembered(body)} 库={rows}")
            check("判据 1 记的确实是「辣」这件事",
                  bool(rows) and "辣" in rows[0][0], repr(rows))
            check("判据 1 回答本身照常产出（记偏好没把车开翻）",
                  bool((body.get("answer") or "").strip()), repr(body.get("answer"))[:80])

            # ---------- 判据 3：闲聊不记 ----------
            body = ask("你好")
            check("判据 3 闲聊不弹「已记住」", remembered(body) == [], repr(remembered(body)))
            check("判据 3 库里没多出来", len(stored(user_id)) == len(rows), repr(stored(user_id)))

            # ---------- 判据 2：不记「这一趟」 ----------
            body = ask("我想去看升旗")
            check("判据 2 「这一趟」的不记", remembered(body) == [], repr(remembered(body)))
            after = stored(user_id)
            check("判据 2 库里没多出来",
                  all("升旗" not in content for content, _ in after), repr(after))
            check("判据 4 的前置：这条链上库里仍只有刚才那几条",
                  len(after) == len(rows), repr(after))

            # ---------- 判据 4：说两遍还是一条 ----------
            body = ask("我不能吃辣")
            check("判据 4 第二遍不再弹「已记住」", remembered(body) == [], repr(remembered(body)))
            rows_now = [content for content, _ in stored(user_id)]
            check("判据 4 库里仍然只有一条，且还是原来那条",
                  len(rows_now) == 1 and rows_now == [rows[0][0]], repr(rows_now))

            # ---------- 第五档：别人的偏好要记，且标出是谁（§2.5.3）----------
            #
            # **这一条是改过的，不是修好的**：第二档到第四档这里验的是"别人的不记"，
            # 第五档把那条规则撤了（§1.2 第 3 条已被 §2.5 取代）。按 §2.5.6 判据 5，
            # 它变红是**预期内**的，要跟着改成"记了、且标了角色"——不许当回归修。
            body = ask("我朋友对海鲜过敏")
            pairs = subject_pairs(user_id)
            # 回执 = subject + content（render_preference，§2.5.6）。**不硬写「朋友」**：
            # 模型写「我朋友」或别的称谓都算对，照库里那一行现拼出来比。
            companion1 = [f"{subject}{content}" for subject, content in pairs
                          if content == "对海鲜过敏" and subject != "本人"]
            check("判据 1 别人说的**弹了**「已记住」，回执**带上角色**",
                  remembered(body) == companion1 and bool(companion1),
                  repr(remembered(body)))
            check("判据 1 库里记下了，且 subject 不是「本人」",
                  any(content == "对海鲜过敏" and subject != "本人"
                      for subject, content in pairs), repr(pairs))
            check("判据 1 这一句照常产出回答（判得对不对先不论，车没翻）",
                  bool((body.get("answer") or "").strip()), repr(body.get("answer"))[:80])

            # ---------- 判据 2：「我老婆不能吃辣」独立成行，没被本人的那条顶掉 ----------
            before = subject_pairs(user_id)
            body = ask("我老婆不能吃辣")
            pairs = subject_pairs(user_id)
            fresh = [row for row in pairs if row not in before and row[0] != "本人"]
            check("判据 2 「我老婆不能吃辣」记下来了，且 subject 不是「本人」",
                  len(fresh) >= 1, repr(fresh))

            # ---------- 判据 2 之二：三列去重键真的生效（§2.5.2 那处隐蔽 bug）----------
            # **这一段不叫模型。**
            #
            # 原来这条 check 的写法是：先让模型把「我不能吃辣」和「我老婆不能吃辣」
            # 都抽出来，再找"两行内容一模一样、subject 不同"。可模型把那句话压成
            # 哪个词**不由我们定**——11:09 那轮压成了同一个「不能吃辣」，这轮压成了
            # 「不吃辣」/「不能吃辣」两个词，同一条 check 就翻红了。**那验的是模型
            # 当天稳不稳定，不是这个键。**（用户真机上也是两个词，见 §2.5.8。）
            #
            # 所以改成直接拿真 Service 往**真库**里插：同一句话、两个角色各插一条。
            # 两列键之下第二条会被 on conflict do nothing 静默吞掉，只留一条——
            # 这才是本档要验的东西，且跑一万遍结果都一样。
            from travel_assistant.clients.postgres_preference_store import (
                PostgresPreferenceStore)
            from travel_assistant.service.preference_service import PreferenceService

            service = PreferenceService(PostgresPreferenceStore())
            probe_content = "探针用的占位口味"      # 挑一个模型不会说出来的词
            probe_subjects = ["本人", "探针的另一位"]
            for subject in probe_subjects:
                service.record(user_id, [{"subject": subject, "content": probe_content}],
                               "chat")
            twins = [subject for subject, content in subject_pairs(user_id)
                     if content == probe_content]
            # 两列键之下这里会只剩 ['本人']——第二条被吞了，而界面上看起来"记了"。
            check("判据 2 同一句话两个角色各存一条（三列去重键真的生效）",
                  twins == probe_subjects, f"库里只查到 {twins}")
            # 这两行是探针自己插的，**当场清掉**：后面的用例要看库里的真实内容
            # （注入几条、哪几条），留着会让"同行的人 N 条"这种数字对不上账。
            from travel_assistant import db
            with db.connection() as conn:
                conn.execute("delete from preferences where user_id = %s and content = %s",
                             (user_id, probe_content))
            # 回执带上角色：它得等于库里那一行的「subject+content」，**不是**裸 content。
            # 不硬写「老婆」——模型规范成「妻子」也算对。
            rendered = [f"{subject}{content}" for subject, content in pairs
                        if subject != "本人"]
            bare = [content for subject, content in pairs if subject != "本人"]
            check("判据 2 回执**带上角色**（不是裸内容）",
                  len(remembered(body)) == 1
                  and remembered(body)[0] in rendered
                  and remembered(body)[0] not in bare,
                  repr(remembered(body)))

            # ---------- 第四档：场景（情节记忆，§2.4.7） ----------
            # 措辞刻意与下面「没地点」那条**不同**：若两条内容一样，模型万一猜错城市
            # 把后一条也存进来，去重会把第二遍吞掉——那这条用例就**为了让错的原因通过**了
            # （规则 13 那条踩过同一个坑）。
            body = ask("上次杭州那家酒店隔音差")
            check("第四档 判据 1 这一句照常产出回答（判得对不对先不论，车没翻）",
                  bool((body.get("answer") or "").strip()), repr(body.get("answer"))[:80])
            rows_ep = episodes(user_id)
            check("第四档 判据 1 库里多了一条场景，城市是杭州、来源是 chat",
                  len(rows_ep) == 1 and rows_ep[0][0] == "杭州" and rows_ep[0][2] == "chat",
                  repr(rows_ep))
            check("第四档 判据 1 记的确实是「隔音」这件事",
                  bool(rows_ep) and "隔音" in rows_ep[0][1], repr(rows_ep))
            # 判据 5（不跨桶）：它进了 episodes，就不该同时在 preferences 里。
            # 两个桶分不干净的表现是**同一句话被注入两遍**，而且还挂在"他这个人"名下。
            crossed = preference_hits(user_id, "隔音")
            check("第四档 判据 5 没有同时进 preferences（不跨桶）", crossed == 0, f"命中 {crossed} 行")

            # ---------- 判据 4：没地点的不记 ----------
            body = ask("上回住的那家民宿热水不热")
            after_ep = episodes(user_id)
            check("第四档 判据 4 没地点的不记",
                  len(after_ep) == len(rows_ep)
                  and all("民宿" not in content for _, content, _ in after_ep), repr(after_ep))

            # ---------- 判据 3：同城市要带上 ----------
            body = ask("我上次住的那家酒店有什么问题？", city="杭州")
            same_city = memory_trace(body)
            check("第四档 判据 3 同城市注入了 1 条",
                  "杭州 的场景：1 条" in same_city, repr(same_city))

            # ---------- 判据 2：换个城市不许带上 ----------
            body = ask("我上次住的那家酒店有什么问题？", city="成都")
            other_city = memory_trace(body)
            check("第四档 判据 2 换个城市注入 0 条",
                  "成都 的场景：0 条" in other_city, repr(other_city))
            check("第四档 判据 2 那一条场景一个字都没进提示词",
                  "隔音" not in other_city, repr(other_city))

            # ---------- 第三档：看偏好（列表 / 删除，§2.3.5 / §2.3.6 判据 1-5）----------
            #
            # 这一段走**HTTP 那两条端点**（与前端调的是同一组），不直接查库——
            # 界面上"看得见 / 删得掉"就是它们。
            def preferences_api() -> list[dict]:
                return client.get("/api/preferences").json().get("items") or []

            # 判据 1：看得见
            items = preferences_api()
            check("判据 1 列表里看得见前面记下的那些", len(items) >= 2, repr(items)[:220])
            check("判据 1 每行都带 id 与来源（前端按 id 删、按来源显示）",
                  bool(items) and all(isinstance(i.get("id"), int) and i.get("source")
                                      for i in items), repr(items)[:220])
            check("判据 1 来源写的是 chat",
                  any(i["source"] == "chat" for i in items), repr(items)[:220])

            # 判据 2：删得掉。**按 id 验，不按内容**——第五档起同一句话可能挂着两个角色
            # （本人一条、老婆一条），按内容验会看错：删掉其中一条，另一条还在，
            # 那个内容当然还搜得到。第一版就是这么误报的。
            doomed = [i for i in items if i["content"] == "不能吃辣"] or [items[0]]
            for item in doomed:
                verdict = client.delete(f"/api/preferences/{item['id']}").json()
                check(f"判据 2 删除回 deleted=True（id={item['id']}）",
                      verdict == {"deleted": True}, repr(verdict))
            left = preferences_api()
            check("判据 2 那几条从列表里没了（按 id 验）",
                  all(i["id"] not in {row["id"] for row in left} for i in doomed),
                  repr(sorted(row["id"] for row in left)))
            check("判据 2 列表里再也搜不到这个内容（每个角色都删干净了）",
                  all(row["content"] != doomed[0]["content"] for row in left),
                  repr(left)[:220])

            # 判据 3：**删了就真的不用了**——本档唯一能证明"删除生效"的手段（§2.3.6）。
            # 只看界面看不出来：那一行确实不见了，但它还在不在生效，要再跑一次才知道。
            # 看的是留痕的**整段**（outputSummary 里列着注入了哪几条）。
            body = ask("那几天天气怎么样")
            trace_text = memory_trace_all(body)
            check("判据 3 删掉的那条不再进提示词",
                  doomed[0]["content"] not in trace_text, repr(trace_text)[:160])

            # 判据 4：**删干净了能重记**（§2.3.5 点名"最容易漏"的一条）。
            # 删不干净（软删、或唯一键还占着位置）时界面看起来完全正常，
            # 代价是这个人**再也记不上这一条**了。
            body = ask("我不能吃辣")
            check("判据 4 再说一遍又能记上", bool(remembered(body)), repr(remembered(body)))
            check("判据 4 列表里又出现了",
                  any("辣" in i["content"] for i in preferences_api()),
                  repr(preferences_api())[:220])

            # 判据 5：**删不掉别人的**——本档唯一的安全面（§2.3.3）。
            # 另造一个账号直接在库里插一条，再拿它的 id 来删：id 是连续 bigserial，
            # 漏掉 where 里那句 user_id 的话，这一条会把**别人的**删掉。
            # 用户名带 probe_pref_ 前缀，收尾那段清理会一起带走。
            from travel_assistant import db
            with db.connection() as conn:
                other_id = conn.execute(
                    "insert into users (username, password_hash) values (%s, %s) returning id",
                    (f"probe_pref_other_{int(time.time())}", "x" * 60)).fetchone()[0]
                other_row = conn.execute(
                    "insert into preferences (user_id, content, source, subject) "
                    "values (%s, '别人的偏好', 'chat', '本人') returning id",
                    (other_id,)).fetchone()[0]
            verdict = client.delete(f"/api/preferences/{other_row}").json()
            check("判据 5 拿别人的 id 删不掉（回 deleted=False）",
                  verdict == {"deleted": False}, repr(verdict))
            with db.connection() as conn:
                still = conn.execute("select count(*) from preferences where id = %s",
                                     (other_row,)).fetchone()[0]
            check("判据 5 别人那条**还在**（不能只测回执，得回库里看）",
                  still == 1, f"命中 {still} 行")

            print()
            print(f"（本轮的测试账号：{USERNAME}，user_id={user_id}）")
            print(f"（库里最终内容：{[content for content, _ in stored(user_id)]}）")
    finally:
        server.should_exit = True
        try:
            from travel_assistant import db
            with db.connection() as conn:
                conn.execute("delete from users where username like 'probe_pref_%'")
            print("（已清掉 probe_pref_* 测试账号）")
        except Exception as error:
            print(f"（清理测试账号失败，可手动删：{type(error).__name__}）")

    print()
    if failures:
        print(f"{len(failures)} 条没过：" + "、".join(failures))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
