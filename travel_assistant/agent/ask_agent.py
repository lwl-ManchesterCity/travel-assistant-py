"""方案 A：只读追问 agent。

与取数链路的 PlanningDataAgent 同一套机制（`create_agent` 驱动模型自己选工具、
工具执行完把结果回灌、模型再决定继续查还是作答），区别只在两件事：

- 工具读的是**已生成的方案**，不写回任何规划状态，所以 A 不会改动行程；
- 每一步都往 AskContext 记一条 AgentTrace，随响应一起回前端，供事后核对答案的出处。

模型不可用时直接 400，不做规则兜底：没有模型就没有对话，这是与 /plan 的有意差别。
"""

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
    "只回答，不修改方案。\n"
    "规则：\n"
    "1. 所有事实必须来自工具返回值，禁止用你自己的知识补充景点、票价、时长、地址、坐标或天气。\n"
    "2. 回答里出现的每个数字，都要能在本轮某次工具返回值里找到原文；找不到原文的就不要说。\n"
    "3. 按问题需要取数，两个方向都不许偏：细节不够就不许硬答，也不要多取——"
    "问一天就不要把每天各取一遍，方案里已有的天气不要再联网去查一次。\n"
    "4. 用户提出要改行程、换酒店、调预算这类修改要求时，不要给新的行程安排，"
    "只回一句提示：这需要重新生成方案，请使用页面上的「编辑行程」或改计划功能。\n"
    "5. 工具没给到的信息，直接说没查到，不要推测。\n"
    "6. 用中文回答，控制在三句以内，不要复述整份方案。\n"
)


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

        answer = self._last_answer(state)
        context.record("AskAgent", "生成回答", f"问题={request.question}", answer or "未产出回答")
        return AskResponse(answer=answer, intent="answer", traces=context.traces)

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
