#!/usr/bin/env python3
"""追问侧「开放时间接两层」的端到端验收：真服务 + 真模型 + 真高德。

判据见 docs/ASK_LIVE_HOURS.md §四 —— 这里守的是单测盖不到的那四条：

    3  本地库没有的景点，答出时段（轨迹里是 search_attractions，不是本地库那个工具）
    4  本地库优先：库里有的仍走本地库，**不产生一次多余的高德调用**
    5  两层都没有：仍如实说查不到，**不许拿高德"最接近那个"顶替**
    9  城市不收录：本地库那句要指路到第二跳，模型确实接着发了联网调用

单测证明的是"每个函数按写的做"，这四条证明的是"**模型真的会走第二跳**"——
规则 11 里那句话写没写进提示词、模型认不认，只有真跑一次才算数。

方案直接 POST 一份合成方案（AskRequest 本来就把 trip_plan 放在请求体里），
不用先花几十秒生成真行程——要验的是追问链，不是规划。

用法（**必须从仓库根跑**：配置按 cwd 找 .env，从别处跑会读不到，表现像"模型不可用"）：

    .venv/bin/python scripts/opening_hours_probe.py

退出码 0 = 全过，1 = 有失败项。测试账号收尾时自清（用户名 probe_hours_ 前缀）。

**为什么挑这几个景点**（都对着 data/attractions.json 的实测内容挑的）：

    判据 3 的靶子**现挑**，不写死：从候选里找第一个「本地库那 33 条里没有、而高德给得出
                开放时间」的广州景点。高德 `opentime2` 只覆盖 ~66%，写死一个很容易正好撞上
                没覆盖的那 34%——第一版写死「中山纪念堂」就撞上了：**链路全对**，答出来却是
                "开放 未给出"，看着像功能坏了，其实只是那家没数据。
    陈家祠       本地库收的是「陈家祠堂」，问简称要能在库这边命中 → 判据 4
    广州塔旋转餐厅 故意问一个不存在的子店，高德会返回"最接近"的广州塔 → 判据 5
    大熊猫基地   成都**整座城都不在本地库索引里** → 判据 9

注意 越秀公园 **不能**拿来当判据 3 的靶子——它在本地库那 33 条里，本地库这一层就命中了，
验不到第二跳。
"""
import sys
import threading
import time
from pathlib import Path

import httpx
import uvicorn

# 从**脚本自己的位置**推仓库根：硬编码绝对路径的话换机器 / 换目录就 ImportError，
# 而报错看着像"模块不存在"。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from travel_assistant.main import app  # noqa: E402
from test_ask_agent import _request, _trip_plan  # noqa: E402

PORT = 8303
USERNAME = f"probe_hours_{int(time.time())}"
PASSWORD = "hunter2xyz"

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'PASS' if condition else 'FAIL'}  {name}" + (f"  → {detail}" if detail else ""))
    if not condition:
        failures.append(name)


# 判据 3 的靶子候选：都是广州真有名、而本地库那 33 条里没有的（现挑理由见模块开头）。
_CANDIDATES = ("中山纪念堂", "黄埔军校旧址纪念馆", "光孝寺", "六榕寺",
               "宝墨园", "石门国家森林公园", "南沙湿地公园", "广州起义烈士陵园")


def pick_target_with_hours() -> str:
    """挑一个「本地库没有、而高德给得出开放时间」的广州景点。

    现查一遍再挑，不写死名字——理由见模块开头那段。
    """
    import json

    from travel_assistant.config.container import get_container

    kb_names = [row["name"] for row in
                json.loads((ROOT / "data" / "attractions.json").read_text(encoding="utf-8"))
                if row.get("city") == "广州"]
    agent = get_container().attraction_agent
    tried: list[str] = []
    for name in _CANDIDATES:
        if any(name in kb_name or kb_name in name for kb_name in kb_names):
            continue                        # 本地库收了的就不算判据 3 的靶子
        attraction = agent.find_by_name("广州", name)
        # 要的是**具体时段**（`HH:MM-HH:MM`）：「全天开放」是另一条判据（11）的靶子，
        # 混进来会让判据 3 的"答案里答出了时段"看着像失败。
        if attraction is not None and attraction.hours and ":" in attraction.hours:
            print(f"     判据 3 的靶子现挑到：{name} → 高德给的是 {attraction.hours}")
            return name
        tried.append(f"{name}（{'高德没查到' if attraction is None else '高德没给开放时间'}）")
    raise SystemExit(f"候选里没有一个既有高德开放时间、又不在本地库的；换一批候选再跑：{tried}")


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
        # **trust_env=False 不能省**：这台机器上开了系统代理（Clash 之类），而 macOS 上
        # Python 的 getproxies() 不认系统那份 ExceptionsList（里面明明写着 127.0.0.1），
        # 于是 httpx 会把**环回**请求也发给代理，表现是"注册就 502"，看着像应用坏了。
        with httpx.Client(base_url=base, timeout=300.0, trust_env=False) as client:
            response = client.post("/api/auth/register",
                                   json={"username": USERNAME, "password": PASSWORD})
            check("注册成功（否则下面全是 401，看不出真正的原因）",
                  response.status_code == 200, str(response.status_code))

            def ask(question: str, city: str) -> list[dict]:
                """返回轨迹列表。city 改的是**方案**那一份——联网那跳取的就是它。"""
                plan = _trip_plan().model_copy(update={"city": city})
                payload = {
                    "question": question,
                    "tripPlan": plan.model_dump(by_alias=True),
                    "request": _request(city).model_dump(by_alias=True),
                }
                body = client.post("/api/trips/ask", json=payload).json()
                if "traces" not in body:
                    print(f"     ⚠️ 响应里没有 traces，原样：{str(body)[:400]}")
                return body

            def calls(body: dict, agent_name: str) -> list[dict]:
                return [t for t in body.get("traces") or [] if t.get("agentName") == agent_name]

            def answer(body: dict) -> str:
                return body.get("answer") or ""

            # ---- 判据 3：本地库没有的景点，第二跳答出时段 ----
            target = pick_target_with_hours()
            body = ask(f"{target}几点关门", city="广州")
            kb, online = calls(body, "search_attractions_by_tag"), calls(body, "search_attractions")
            print(f"     [判据3] 本地库留痕={len(kb)} 联网留痕={len(online)} 答案={answer(body)[:120]}")
            check("判据 3 · 本地库没命中后，模型确实发了第二次联网调用",
                  any("景点名" in (t.get("inputSummary") or "") for t in online),
                  str([t.get("inputSummary") for t in online])[:200])
            check("判据 3 · 联网那次把开放时间带了出来",
                  any("开放" in (t.get("outputSummary") or "") for t in online),
                  str([t.get("outputSummary") for t in online])[:200])
            check("判据 3 · 答案里答出了时段",
                  ":" in answer(body) and any(ch.isdigit() for ch in answer(body)),
                  answer(body)[:120])

            # ---- 判据 4：本地库优先，不多打一次高德 ----
            body = ask("陈家祠周一闭馆吗", city="广州")
            kb, online = calls(body, "search_attractions_by_tag"), calls(body, "search_attractions")
            print(f"     [判据4] 本地库留痕={len(kb)} 联网留痕={len(online)} 答案={answer(body)[:120]}")
            check("判据 4 · 库里有的仍走本地库",
                  bool(kb) and kb[-1].get("status") == "success",
                  str([t.get("status") for t in kb]))
            check("判据 4 · 一次多余的高德调用都没有", not online, str(len(online)))

            # ---- 判据 5：两层都没有，仍如实说查不到 ----
            body = ask("广州塔旋转餐厅几点关门", city="广州")
            kb, online = calls(body, "search_attractions_by_tag"), calls(body, "search_attractions")
            print(f"     [判据5] 本地库留痕={len(kb)} 联网留痕={len(online)} 答案={answer(body)[:120]}")
            check("判据 5 · 高德返回「最接近那个」时工具留了痕",
                  any("最接近" in (t.get("outputSummary") or "") for t in online),
                  str([t.get("outputSummary") for t in online])[:200])
            # 「如实说没查到」的判据：**答案里必须出现否定语素**（没 / 未 / 不）。
            #
            # 别列举「查不到 / 没查到 / 未查询到 / 都没给到…」——那是**开放词表**，
            # 模型每换一种说法就漏一次。这个坑连踩三回：先是「都没给到」，
            # 再是「未查询到」，这回是「都没有查到」。
            # 而否定语素是个**封闭**集合：一个把广州塔的开放时间直接当成旋转餐厅营业时间的
            # 答案，里面不会有「没 / 未 / 不」。
            check("判据 5 · 没有拿「最接近那个」顶替，答的是查不到",
                  any(word in answer(body) for word in ("没", "未", "不")),
                  answer(body)[:120])

            # ---- 判据 9：城市不收录，本地库那句要指路到第二跳 ----
            body = ask("大熊猫基地几点关门", city="成都")
            kb, online = calls(body, "search_attractions_by_tag"), calls(body, "search_attractions")
            print(f"     [判据9] 本地库留痕={len(kb)} 联网留痕={len(online)} 答案={answer(body)[:120]}")
            check("判据 9 · 城市不收录时，本地库那句给出了下一跳的指路",
                  any("可以再用 search_attractions 联网查一次" in (t.get("outputSummary") or "")
                      for t in kb),
                  str([t.get("outputSummary") for t in kb])[:200])
            check("判据 9 · 模型确实接着发了联网调用（而不是停在「本地库答不了」）",
                  bool(online), str(len(online)))

            # ---- 判据 10：问别的城市时，第二跳要带着城市走 ----
            # 判据 9 那一次方案城市和提问城市是同一座（成都），照不出这个坑：
            # 第二跳默认取**方案**城市，方案在上海、问成都时它会拿上海去查高德。
            body = ask("成都大熊猫基地几点关门", city="上海")
            kb, online = calls(body, "search_attractions_by_tag"), calls(body, "search_attractions")
            print(f"     [判据10] 本地库留痕={len(kb)} 联网留痕={len(online)} 答案={answer(body)[:120]}")
            check("判据 10 · 方案在上海、问成都，第二跳按**成都**查而不是方案城市",
                  any((t.get("inputSummary") or "").startswith("城市=成都") for t in online),
                  str([t.get("inputSummary") for t in online])[:200])
            check("判据 10 · 答案里是成都那个景点，不是上海的替代品",
                  "大熊猫" in answer(body), answer(body)[:120])

            # ---- 判据 11：库里命中了、但那一格空着 ----
            # 天河湿地公园：本地库那 33 条里**有它**，但 `hours` 是空的
            # （库里 20~30% 的行都这样）；高德那边给的是「全天开放」。
            # 所以这一次同时照出两件事：**「检索命中」≠「这一格有值」**，
            # 以及**「全天开放」是答案、不是空值**（§3.3 那条分叉）。
            body = ask("天河湿地公园几点关门", city="广州")
            kb, online = calls(body, "search_attractions_by_tag"), calls(body, "search_attractions")
            print(f"     [判据11] 本地库留痕={len(kb)} 联网留痕={len(online)} 答案={answer(body)[:120]}")
            check("判据 11 · 库里命中了但那一格是空的，模型仍走了第二跳（没停在「库里没有」）",
                  bool(kb) and kb[-1].get("status") == "success"
                  and any("天河湿地公园" in (t.get("inputSummary") or "") for t in online),
                  f"kb={[t.get('status') for t in kb]} online={[t.get('inputSummary') for t in online]}"[:200])
            check("判据 11 · 「全天开放」被当成答案答了出来，不是「未给出」",
                  "全天开放" in answer(body), answer(body)[:120])
    finally:
        cleanup()

    print()
    if failures:
        print(f"❌ {len(failures)} 条没过：{'、'.join(failures)}")
        return 1
    print("✅ 全过")
    return 0


def cleanup() -> None:
    """测试账号用完就删——探针跑一次留一个用户，跑几十次库里就全是垃圾。"""
    try:
        from travel_assistant import db
        with db.connection() as conn:
            conn.execute("delete from users where username like 'probe_hours_%'")
    except Exception as exception:  # noqa: BLE001 - 收尾失败不该把判据结果盖掉
        print(f"（收尾没清干净，不影响上面的判据：{exception}）")


if __name__ == "__main__":
    raise SystemExit(main())
