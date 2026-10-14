"""方案 A + B：只读追问 agent，B 多一步意图判定与人工确认。

与取数链路的 PlanningDataAgent 同一套机制（`create_agent` 驱动模型自己选工具、
工具执行完把结果回灌、模型再决定继续查还是作答），区别只在三件事：

- 工具读的是**已生成的方案**，不写回任何规划状态，所以这里不会改动行程；
- 每一步都往 AskContext 记一条 AgentTrace，随响应一起回前端，供事后核对答案的出处；
- 判成 `need_change` 时只产出一句给 /adjust 的描述，**必须用户点确认才会发出去**，
  因为一次改方案等于一次完整重规划，误判的代价是 40~70 秒加一轮高德配额。

模型不可用时直接 400，不做规则兜底：没有模型就没有对话，这是与 /plan 的有意差别。
"""

import json
from typing import Callable

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from ..clients.llm import LlmClient, OpenAiCompatibleLlmClient
from ..clients.preference_store import SOURCE_CHAT
from ..exceptions import TripPlanningException
from ..models.ask_request import AskRequest, AskTurn
from ..models.ask_response import AskResponse
from ..service.preference_service import (
    MAX_PER_USER,
    PreferenceService,
    build_injection_block,
)
from .ask_context import AskContext, reset_ask_context, set_ask_context
from .ask_tools import build_ask_tools
from .attraction_agent import AttractionAgent
from .hotel_agent import HotelAgent
from .weather_agent import WeatherAgent

_SYSTEM_PROMPT = (
    "你是旅行方案的追问助手。用户已经拿到一份行程方案，你的任务是回答他关于这份方案的疑问，"
    "或者判断出他其实是想改方案。你不改任何东西，最多产出一句给改方案链路的描述。\n"
    "规则：\n"
    "1. 事实分两档，别混在一句话里说。\n"
    "   1a **具体数字与可执行事实**——门票、开放时间、闭馆日、地址、坐标、天气、"
    "酒店价位、通勤时长：**必须来自本轮工具返回值**，禁止用你自己的知识补充或修改。\n"
    "   1b **宽泛的常识与建议**——菜系口味、季节特点、注意事项、行程节奏的看法："
    "**可以用你自己的知识**，但必须守住三条：\n"
    "      （1）不许出现任何具体数字（可以说「杭州菜偏清淡、偏甜」，"
    "不可以说「人均 80 元」「玩两小时就够」）；\n"
    "      （2）不许把具体店名、具体地点当成推荐说出来（可以说「杭帮菜」，"
    "不可以说「去楼外楼」）——店名一旦说出口，用户会当成你查证过的事实；\n"
    "      （3）要说清这是「一般经验」，与工具查到的方案数据分开表述。\n"
    "   **为什么非留这三条**：你的「知道」和「编」是同一套机制，你自己分不出"
    "「这句是我记住的」和「这句是我拼的」。数字和店名最容易编，也最容易被用户当真照做。\n"
    "2. 回答里出现的每个数字，都要能在本轮某次工具返回值里找到原文；找不到原文的就不要说。"
    "前面几轮的对话只用来理解用户在指谁、指哪一天、接着哪句问，**不是事实来源**——"
    "历史里出现过的数字同样要在本轮工具返回值里找到原文才能用，找不到就重新查一次。\n"
    "3. 按问题需要取数，两个方向都不许偏：细节不够就不许硬答，也不要多取——"
    "问一天就不要把每天各取一遍，方案里已有的天气不要再联网去查一次。\n"
    "4. 工具没给到的信息，直接说没查到，不要推测。\n"
    "5. 工具返回值里标了「估算」的价格，转述时必须保留「估算」二字，不许说成实时票价或实际收费。\n"
    "6. 先判意图。只是问这份方案写了什么、为什么这么排、怎么办 → answer；"
    "提出要改预算、改交通方式、改住宿档位、增加或删除偏好、调节奏松紧、"
    "指定住宿位置（整趟或某一天）、要求整趟住同一家、指定某天必去某个景点 → need_change。"
    "拿不准就按 answer 处理。\n"
    "7. 判成 need_change 时，adjustmentPrompt 只许写下游做得到的那些：预算金额、交通方式、"
    "住宿档位、偏好增删、节奏松紧、整趟住同一家；要指定住宿位置就把地名放进 anchorHint、"
    "只指定某一天就同时写 anchorDay（第一天是 0）；要指定某天必去一个或多个景点，就把景点名都放进 "
    "pinHints（数组）、那天写进 pinDay（第一天是 0）。改天数、指定某一家具体酒店、删除或替换某个景点"
    "这类做不到的诉求，不要转过去，在 answer 里当场说明当前版本改不了。\n"
    "8. pinHints 与 anchorHint 只填用户说出口的地名原词，不要求它先出现在方案或候选清单里："
    "下游会按名字重新检索一次，检索不到才会在备注里写明白。"
    "「方案里查不到这个景点」不是拒绝的理由，这类诉求照样判 need_change；"
    "你自己不要凭知识断言这个地点存不存在。\n"
    "9. 最后一轮只输出一个 JSON 对象，二选一，不要任何多余文字：\n"
    "   {\"intent\":\"answer\",\"answer\":\"…\",\"preferences\":[]}\n"
    "   {\"intent\":\"need_change\",\"answer\":\"…\",\"adjustmentPrompt\":\"…\",\"preferences\":[]}\n"
    "   需要指定住宿位置时再加 \"anchorHint\":\"西湖\" 与 \"anchorDay\":1；"
    "需要指定某天必去某景点时再加 \"pinHints\":[\"白云山\",\"广州塔\"] 与 \"pinDay\":1。\n"
    "10. answer 用中文，控制在三句以内，不要复述整份方案；"
    "用户还没确认改不改之前，不许用完成时态说已经帮他改好了。\n"
    "11. 景点问题分三种问法，别用混。问**方案里已有的那个景点**（它排在第几天、什么类别、"
    "逛多久、在哪）用 get_attraction_detail——**它只读方案，方案里没有开放时间、也没有闭馆日**。"
    "另外两种都用 search_attractions_by_tag："
    "问**有什么符合条件的**（下雨天去哪、有什么免费的、哪里适合带孩子），"
    "以及问**某个景点的客观信息、或方案里没有的景点**（「广州塔门票多少钱」「陈家祠周一闭馆吗」"
    "「越秀公园几点关门」）——**要门票吗、几点关门、周一闭馆吗、要预约吗这几样，"
    "景点在不在方案里都一样，方案里压根没这几格，只能查库**。\n"
    "    第三种最容易答错：它问的是一个具体景点，但**方案里没有不代表库里没有**——"
    "search_attractions_by_tag 查的是整个本地库，不限于本方案，这类问题照样用它，"
    "**把景点名填进 attraction_name**（问「陈家祠周一闭馆吗」就填 \"陈家祠\"，"
    "不要连问句一起填），不用传 tags。"
    "整句送进检索会被问句里多出来的字稀释，相似度掉到线下，明明库里有也查不到。"
    "**「方案里没有这个景点」不是景点问题的答案**：没查过本地库就说查不到是答错了。\n"
    "    **「杭州下雨天去哪」里的「下雨天」是筛选条件（= 要室内的），不是问本方案的天气。**"
    "本方案这几天是不是晴天，跟这个问题没关系——用户问的是「库里有哪些雨天能去的」，"
    "方案里是晴天也照样要去本地库查一遍。"
    "把它当成天气问题、回一句「这几天不下雨所以没有雨天安排」，是答错了。\n"
    "    **问别的城市的景点也照查**：把城市名填进 city（问「广州塔几点关门」就填 \"广州\"）。"
    "**只有问「某个具体景点 / 某类景点」时才填 city**；问「我这趟的」——天气、酒店、"
    "候选景点——一律不填，那三个工具取的就是本次行程的城市，填了反而带偏。"
    "**本地库收录哪些城市以工具说明里那一行为准，不在这里写死城市名**"
    "（写死过「只收录杭州」，补城市时漏改，非杭州的景点问题全被劝退）。"
    "工具说明以外的城市它会直说查不了，照实转告。"
    "它的 tags 只能从工具说明里那份词表里挑，挑不出来就别筛，不许硬凑一个充数。\n"
    "12. **本地库的答案要自己核对类别。** 检索永远会返回几条，返回了不等于对得上——"
    "用户问爬山却返回溶洞、问湖却返回博物馆，那就要说库里没有，"
    "不许因为「它是搜出来的」就把沾边的说成是用户要的。这条优先于「尽量给出答案」。\n"
    "13. `preferences` 填用户在这一句里露出的**关于他自己**的长期偏好，"
    "它会存下来、影响他以后每一趟。**判断标准是「这个人的」还是「这一趟的」**：\n"
    "    要记（这个人的）：「对海鲜过敏」「我不能吃辣」「不吃香菜」「预算敏感」"
    "「恐高」「带小孩出行」「喜欢安静」「腿脚不便」。\n"
    "    不记（这一趟的）：「想去看升旗」「这次轻松点」「明天想去西湖」「这次多留一天」。\n"
    "    **拿不准就按「这一趟的」不记。** 这一档宁可漏记，不可错记——"
    "错记的那条会跟着他每一趟，而他根本不知道它在那儿。\n"
    "    只记**用户本人**的。同行的人自己什么口味、什么状况，不算他的："
    "「我老婆不能吃辣」「我朋友对海鲜过敏」「我妈腿脚不好」都整条不记。\n"
    "    注意别人的话**不许「去掉主语再记」**：下面要求你不带主语，而「老婆不能吃辣」"
    "剥掉主语就是「不能吃辣」，跟用户本人说的**长得一模一样**，你分不出来，"
    "他以后每一趟也就都被这么带着了——所以只能整条丢。\n"
    "    分界看是**谁**：同行的人是谁，是他的情况（「带小孩出行」要记）；"
    "同行的人**自己**的口味和状况，不是。\n"
    "    闲聊不记：「你好」「谢谢」「今天天气不错」。\n"
    "    一句话里可以有多条：「我不吃辣也不吃香菜」→ [\"不吃辣\",\"不吃香菜\"]。\n"
    "    每条只写**偏好本身**，短，不超过十几个字；不要带主语，不要写成一句话"
    "（写「不吃辣」，不写「用户说他不能吃辣」），更不要把整句话抄进来。\n"
    "    **没有就交空数组，不要为了填而填。** 你天然倾向于「总得输出点什么」，"
    "这一格允许空——空是正常答案，不是失败。\n"
)

_INTENTS = ("answer", "need_change")

# 本 agent 自己那几条留痕用的名字（"判定意图并生成回答" 等）。
# 它们**不是工具调用**，所以不进 SSE：流式那条链约定推的是"工具调用进度条"，
# 这几条混进去只会挤在答案前面。更要紧的是其中一条（解析失败回落）的
# inputSummary 装的是**模型原始输出**——模型要是吐了个残缺的 JSON 信封，
# 这段原文就会把 `{"intent":"answer","answer":"` 直接摆到聊天里，
# 正好踩中"从头到尾看不到 JSON 脚手架"那条判据。
# 数据不丢：它们照旧进 AskResponse.traces，只是不在聊天里显示。
_AGENT_TRACE_NAME = "AskAgent"

# 历史对话的窗口（见 docs/ASK_MULTITURN_MEMORY.md §3.4）。
# 先说清这不是"怕撑爆"：实测一轮问答 ≈ 28 token，聊一百轮才刚赶上系统提示词那个量级。
# 做窗口是为了质量（五十轮前的东西只会稀释注意力）、成本、延迟。
_HISTORY_MAX_TURNS = 6       # 一轮 = 一问一答，所以最多 12 条消息
_HISTORY_MAX_CHARS = 8000    # 兜底：规则 10 要求答案 ≤3 句，但模型不一定会守
# 单条上限。真到不了这个数（正常答案三句 ≈ 100 字），它是给**不信任的客户端**兜底的：
# 客户端塞一条一千万字的历史，光靠总字符闸门拦不住——窗口里至少会留最近那一对。
_HISTORY_MAX_TURN_CHARS = 2000


def _history_messages(history: list[AskTurn] | None) -> list[HumanMessage | AIMessage]:
    """把历史的尾巴拼成消息列表，**成对**截断。

    两道闸门：最近 `_HISTORY_MAX_TURNS` 轮，以及从最近往前累加不超过
    `_HISTORY_MAX_CHARS` 个字符（超了就整对丢掉）。

    **成对**是这里的要点：只丢 Human 或只丢它对应的 AI，模型会看到
    "AI 答了一句用户没问过的话"。所以从末尾往前配对，配不成对的（末尾孤立的
    Human——比如上一轮问出去还没答完；或开头孤立的 AI）一律不要。

    前端存的时候也是成对的（发问时追加 Human，答案到手才追加 AI），
    这里是**服务端自己的兜底**：不能信任客户端，构造一个一万轮的历史就能打爆 prompt。
    """
    turns = list(history or [])
    index = len(turns) - 1
    # 末尾孤立的 Human 先摘掉（上一轮问出去还没答完）。前端在答案到手前不会存它，
    # 但客户端不可信——留着的话，下面从末尾配对**第一步就配不上**，整段历史一起丢。
    if index >= 0 and turns[index].role == "user":
        index -= 1
    pairs: list[list[AskTurn]] = []
    while index >= 1:
        assistant, human = turns[index], turns[index - 1]
        if assistant.role != "assistant" or human.role != "user":
            break
        pairs.append([human, assistant])
        index -= 2
    pairs.reverse()

    kept: list[list[AskTurn]] = []
    total = 0
    for pair in reversed(pairs[-_HISTORY_MAX_TURNS:]):
        size = sum(len(turn.content) for turn in pair)
        # `kept` 非空才允许因为超限停手：最近这一对无论多大都要留着，
        # 否则"那它几点关门"里的"它"就没有指代对象了，多轮等于白做。
        if kept and total + size > _HISTORY_MAX_CHARS:
            break
        total += size
        kept.append(pair)
    kept.reverse()

    messages: list[HumanMessage | AIMessage] = []
    for human, assistant in kept:
        messages.append(HumanMessage(content=human.content[:_HISTORY_MAX_TURN_CHARS]))
        messages.append(AIMessage(content=assistant.content[:_HISTORY_MAX_TURN_CHARS]))
    return messages


class _Decision:
    """模型最后一轮输出的解析结果。"""

    def __init__(self, intent: str = "answer", answer: str = "",
                 adjustment_prompt: str | None = None,
                 anchor_hint: str | None = None, anchor_day: int | None = None,
                 pin_hints: list[str] | None = None, pin_day: int | None = None,
                 preferences: list[str] | None = None):
        self.intent = intent
        self.answer = answer
        self.adjustment_prompt = adjustment_prompt
        self.anchor_hint = anchor_hint
        self.anchor_day = anchor_day
        self.pin_hints = pin_hints or []
        self.pin_day = pin_day
        self.preferences = preferences or []


class AskAgent:

    def __init__(self, llm_client: LlmClient, attraction_agent: AttractionAgent,
                 weather_agent: WeatherAgent, hotel_agent: HotelAgent,
                 preference_service: PreferenceService | None = None):
        self._llm_client = llm_client
        self._tools: list[BaseTool] = build_ask_tools(attraction_agent, weather_agent, hotel_agent)
        # 不传就整条链不碰偏好：老的单测构造它时不带这个参数，行为与加这一档之前一字不差。
        self._preference_service = preference_service
        self._agent: CompiledStateGraph | None = None

    def ask(self, request: AskRequest,
            on_event: Callable[[str, dict], None] | None = None,
            user_id: int | None = None) -> AskResponse:
        """跑一次追问。

        `on_event` 是给流式那条链用的可选回调：`("trace", {...})` 每次工具调用推一条，
        收尾再推 `("answer", {...})`。**默认 None 时行为与从前一字不差**——
        一次性接口（`/api/trips/ask`）走的还是这条默认路径。

        `user_id` 是登录门禁认出来的人，长期偏好按它存取。**None 时既不注入也不记录**：
        这一档之前就没有这个参数，缺省路径必须保持原样。
        """
        if not self._llm_client.is_available():
            raise TripPlanningException("追问能力需要可用模型")
        if request.trip_plan is None or not (request.trip_plan.days or []):
            raise TripPlanningException("回传的方案里没有可追问的内容，请先生成一次旅行计划")

        context = AskContext(
            question=request.question,
            trip_plan=request.trip_plan,
            request=request.request,
            on_trace=(
                (lambda trace: self._emit_trace(on_event, trace))
                if on_event is not None else None
            ),
        )
        token = set_ask_context(context)
        try:
            messages: list = []
            # 长期偏好**顶在最前**，是一条独立的系统消息，不是塞进历史。
            #
            # 不塞历史的两个理由：历史是"可以被不被读"的（模型能选择性忽略），
            # 而且历史有窗口（_HISTORY_MAX_TURNS），偏好会被裁掉——那就等于
            # "有时候生效有时候不生效"，是最难查的一种坏。
            #
            # 也不改 create_agent(system_prompt=…)：那个参数在 create_agent 时
            # 就固化了，而 self._agent 是缓存的，按请求重建等于每轮把图重编译一遍。
            block = self._preference_block(user_id)
            if block:
                messages.append(SystemMessage(content=block))
            # 历史在前、当前这句在后。模型看到的是完整的多轮，工具调用由它自己决定
            # ——历史里**没有**工具消息（见 models/ask_request.py 的 AskTurn）。
            messages.extend(_history_messages(request.history))
            messages.append(HumanMessage(content=request.question))
            state = self._get_agent().invoke({"messages": messages})
        finally:
            reset_ask_context(token)

        decision = self._parse_final(self._last_answer(state), context)
        context.record(_AGENT_TRACE_NAME, "判定意图并生成回答",
                       f"问题={request.question}", decision.answer or "未产出回答")
        if decision.intent == "need_change":
            context.record(_AGENT_TRACE_NAME, "归一化改方案诉求", "等用户确认后才发 /adjust",
                           decision.adjustment_prompt or "", "pending")
        recorded = self._record_preferences(user_id, decision, context)
        response = AskResponse(
            answer=decision.answer,
            intent=decision.intent,
            adjustment_prompt=decision.adjustment_prompt,
            anchor_hint=decision.anchor_hint,
            anchor_day=decision.anchor_day,
            pin_hints=decision.pin_hints,
            pin_day=decision.pin_day,
            recorded_preferences=recorded or None,
            traces=context.traces,
        )
        # 答案整条推，不逐 token：模型最后一轮吐的是规则 9 约定的 JSON 信封，
        # 逐 token 推出去用户会先看到 `{"intent":"answer","answer":"` 这堆脚手架。
        # 前端的打字机效果是拿到这一整条之后本地播的。
        if on_event is not None:
            on_event("answer", response.model_dump(by_alias=True))
        return response

    def _preference_block(self, user_id: int | None) -> str:
        """这一轮要顶在最前的那段长期偏好。没接服务 / 没登录 / 一条都没有 → 空串。"""
        if self._preference_service is None or user_id is None:
            return ""
        try:
            return build_injection_block(self._preference_service.list_for_injection(user_id))
        except Exception:  # noqa: BLE001 - 偏好读不出来不该让整轮追问挂掉
            return ""

    def _record_preferences(self, user_id: int | None, decision: _Decision,
                            context: AskContext) -> list[str]:
        """把这一句里露出的长期偏好落库，返回**真正新存进去的**那几条。

        判据是模型按规则 13 判的，这里只管清洗、去重、上限。整段包在 try 里：
        记偏好是搭便车的乘客，不能让它把车开翻（§2.2.4）——追问失败和偏好没记住，
        代价差着一个量级。
        """
        if self._preference_service is None or user_id is None or not decision.preferences:
            return []
        try:
            result = self._preference_service.record(user_id, decision.preferences, SOURCE_CHAT)
        except Exception:  # noqa: BLE001
            return []
        if result.overflowed:
            context.record(_AGENT_TRACE_NAME, "记录长期偏好",
                           "、".join(decision.preferences),
                           f"已达 {MAX_PER_USER} 条上限，丢弃 {result.dropped_for_cap} 条",
                           "fallback")
        return result.added

    @staticmethod
    def _emit_trace(on_event: Callable[[str, dict], None], trace) -> None:
        """把一条留痕推给流式那条链。只推工具调用，理由见 _AGENT_TRACE_NAME 那段。"""
        if trace.agent_name == _AGENT_TRACE_NAME:
            return
        on_event("trace", trace.model_dump(by_alias=True))

    def _get_agent(self) -> CompiledStateGraph:
        if self._agent is None:
            if not isinstance(self._llm_client, OpenAiCompatibleLlmClient):
                raise TripPlanningException("当前 LLM 实现不提供 LangChain chat model，无法构建追问智能体")
            self._agent = create_agent(
                self._llm_client.get_chat_model(),
                tools=self._tools,
                system_prompt=_SYSTEM_PROMPT,
            )
        return self._agent

    def _last_answer(self, state: dict) -> str:
        for message in reversed(state.get("messages") or []):
            if isinstance(message, AIMessage) and not message.tool_calls and str(message.content).strip():
                return str(message.content).strip()
        return ""

    def _parse_final(self, raw: str, context: AskContext) -> _Decision:
        """把最后一轮文本解析成意图；任何不合规的输出都回落成 answer。

        回落而不是报错：误判成"要改"会白烧一次重规划，误判成"只是问"用户多点一次编辑就行，
        两边代价不对称。
        """
        parsed = self._load_json(raw)
        if parsed is None:
            self._record_fallback(context, raw, "不是约定的 JSON，回落成只是回答")
            return _Decision(answer=raw)
        intent = str(parsed.get("intent") or "").strip()
        answer = str(parsed.get("answer") or "").strip()
        if intent not in _INTENTS:
            self._record_fallback(context, raw, f"意图值不认识：{intent or '空'}，回落成只是回答")
            return _Decision(answer=answer or raw)
        if intent == "need_change":
            prompt = str(parsed.get("adjustmentPrompt") or "").strip()
            if not prompt:
                self._record_fallback(context, raw, "说要改但没给出改哪句，回落成只是回答")
                return _Decision(answer=answer or raw)
            return _Decision(intent="need_change", answer=answer, adjustment_prompt=prompt,
                             anchor_hint=self._anchor_hint(parsed), anchor_day=self._anchor_day(parsed),
                             pin_hints=self._pin_hints(parsed), pin_day=self._pin_day(parsed),
                             preferences=self._preferences(parsed))
        return _Decision(intent="answer", answer=answer or raw,
                         preferences=self._preferences(parsed))

    def _record_fallback(self, context: AskContext, raw: str, why: str) -> None:
        context.record(_AGENT_TRACE_NAME, "解析模型最后一轮输出", raw[:120], why, "fallback")

    @staticmethod
    def _load_json(raw: str) -> dict | None:
        text = (raw or "").strip()
        if text.startswith("```"):
            body = text.split("```")
            text = body[1] if len(body) > 1 else text
            if text.strip().startswith("json"):
                text = text.strip()[4:]
        try:
            loaded = json.loads(text.strip())
        except (ValueError, TypeError):
            return None
        return loaded if isinstance(loaded, dict) else None

    @staticmethod
    def _anchor_hint(parsed: dict) -> str | None:
        hint = str(parsed.get("anchorHint") or "").strip()
        return hint or None

    @staticmethod
    def _anchor_day(parsed: dict) -> int | None:
        value = parsed.get("anchorDay")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            day = int(value)
        except (TypeError, ValueError):
            return None
        return day if day >= 0 else None

    @staticmethod
    def _pin_hints(parsed: dict) -> list[str]:
        raw = parsed.get("pinHints")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            legacy = str(parsed.get("pinHint") or "").strip()
            raw = [legacy] if legacy else []
        cleaned: list[str] = []
        for item in raw:
            name = item if isinstance(item, str) else ""
            name = name.strip()
            if name and name not in cleaned:
                cleaned.append(name)
        return cleaned

    @staticmethod
    def _preferences(parsed: dict) -> list[str]:
        """"规则 13 那一格。清洗照抄 _pin_hints：str / list 都收、strip、去重。

        这里**只收字符串**，长度闸门与 5 条上限归 PreferenceService——它是两条链
        共用的那一处，规矩写在那边才不会分叉。
        """
        raw = parsed.get("preferences")
        if isinstance(raw, str):
            raw = [raw]
        if not isinstance(raw, list):
            return []
        cleaned: list[str] = []
        for item in raw:
            content = item.strip() if isinstance(item, str) else ""
            if content and content not in cleaned:
                cleaned.append(content)
        return cleaned

    @staticmethod
    def _pin_day(parsed: dict) -> int | None:
        value = parsed.get("pinDay")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            day = int(value)
        except (TypeError, ValueError):
            return None
        return day if day >= 0 else None
