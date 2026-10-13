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

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from ..clients.llm import LlmClient, OpenAiCompatibleLlmClient
from ..exceptions import TripPlanningException
from ..models.ask_request import AskRequest
from ..models.ask_response import AskResponse
from .ask_context import AskContext, reset_ask_context, set_ask_context
from .ask_tools import build_ask_tools
from .attraction_agent import AttractionAgent
from .hotel_agent import HotelAgent
from .weather_agent import WeatherAgent

_SYSTEM_PROMPT = (
    "你是旅行方案的追问助手。用户已经拿到一份行程方案，你的任务是回答他关于这份方案的疑问，"
    "或者判断出他其实是想改方案。你不改任何东西，最多产出一句给改方案链路的描述。\n"
    "规则：\n"
    "1. 所有事实必须来自工具返回值，禁止用你自己的知识补充景点、票价、时长、地址、坐标或天气。\n"
    "2. 回答里出现的每个数字，都要能在本轮某次工具返回值里找到原文；找不到原文的就不要说。\n"
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
    "   {\"intent\":\"answer\",\"answer\":\"…\"}\n"
    "   {\"intent\":\"need_change\",\"answer\":\"…\",\"adjustmentPrompt\":\"…\"}\n"
    "   需要指定住宿位置时再加 \"anchorHint\":\"西湖\" 与 \"anchorDay\":1；"
    "需要指定某天必去某景点时再加 \"pinHints\":[\"白云山\",\"广州塔\"] 与 \"pinDay\":1。\n"
    "10. answer 用中文，控制在三句以内，不要复述整份方案；"
    "用户还没确认改不改之前，不许用完成时态说已经帮他改好了。\n"
    "11. 景点问题分三种问法，别用混。问**方案里已有的那个景点**（要门票吗、几点关门）用 "
    "get_attraction_detail。另外两种都用 search_attractions_by_tag："
    "问**有什么符合条件的**（下雨天去哪、有什么免费的、哪里适合带孩子），"
    "以及问**一个具体但方案里没有的景点**（「广州塔门票多少钱」「陈家祠周一闭馆吗」）。\n"
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
    "    **本地库收录哪些城市以工具说明里那一行为准，不在这里写死城市名**"
    "（写死过「只收录杭州」，补城市时漏改，非杭州的景点问题全被劝退）。"
    "工具说明以外的城市它会直说查不了，照实转告。"
    "它的 tags 只能从工具说明里那份词表里挑，挑不出来就别筛，不许硬凑一个充数。\n"
    "12. **本地库的答案要自己核对类别。** 检索永远会返回几条，返回了不等于对得上——"
    "用户问爬山却返回溶洞、问湖却返回博物馆，那就要说库里没有，"
    "不许因为「它是搜出来的」就把沾边的说成是用户要的。这条优先于「尽量给出答案」。\n"
)

_INTENTS = ("answer", "need_change")


class _Decision:
    """模型最后一轮输出的解析结果。"""

    def __init__(self, intent: str = "answer", answer: str = "",
                 adjustment_prompt: str | None = None,
                 anchor_hint: str | None = None, anchor_day: int | None = None,
                 pin_hints: list[str] | None = None, pin_day: int | None = None):
        self.intent = intent
        self.answer = answer
        self.adjustment_prompt = adjustment_prompt
        self.anchor_hint = anchor_hint
        self.anchor_day = anchor_day
        self.pin_hints = pin_hints or []
        self.pin_day = pin_day


class AskAgent:

    def __init__(self, llm_client: LlmClient, attraction_agent: AttractionAgent,
                 weather_agent: WeatherAgent, hotel_agent: HotelAgent):
        self._llm_client = llm_client
        self._tools: list[BaseTool] = build_ask_tools(attraction_agent, weather_agent, hotel_agent)
        self._agent: CompiledStateGraph | None = None

    def ask(self, request: AskRequest) -> AskResponse:
        if not self._llm_client.is_available():
            raise TripPlanningException("追问能力需要可用模型")
        if request.trip_plan is None or not (request.trip_plan.days or []):
            raise TripPlanningException("回传的方案里没有可追问的内容，请先生成一次旅行计划")

        context = AskContext(
            question=request.question,
            trip_plan=request.trip_plan,
            request=request.request,
        )
        token = set_ask_context(context)
        try:
            state = self._get_agent().invoke({"messages": [HumanMessage(content=request.question)]})
        finally:
            reset_ask_context(token)

        decision = self._parse_final(self._last_answer(state), context)
        context.record("AskAgent", "判定意图并生成回答",
                       f"问题={request.question}", decision.answer or "未产出回答")
        if decision.intent == "need_change":
            context.record("AskAgent", "归一化改方案诉求", "等用户确认后才发 /adjust",
                           decision.adjustment_prompt or "", "pending")
        return AskResponse(
            answer=decision.answer,
            intent=decision.intent,
            adjustment_prompt=decision.adjustment_prompt,
            anchor_hint=decision.anchor_hint,
            anchor_day=decision.anchor_day,
            pin_hints=decision.pin_hints,
            pin_day=decision.pin_day,
            traces=context.traces,
        )

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
                             pin_hints=self._pin_hints(parsed), pin_day=self._pin_day(parsed))
        return _Decision(intent="answer", answer=answer or raw)

    def _record_fallback(self, context: AskContext, raw: str, why: str) -> None:
        context.record("AskAgent", "解析模型最后一轮输出", raw[:120], why, "fallback")

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
    def _pin_day(parsed: dict) -> int | None:
        value = parsed.get("pinDay")
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            return None
        try:
            day = int(value)
        except (TypeError, ValueError):
            return None
        return day if day >= 0 else None
