"""用 LangChain create_agent 组织的取数链路：有可用模型时由模型自己决定调用哪个数据工具。

Java 侧对应 PlannerInputBuilder 里 attractionAgent -> weatherAgent -> hotelAgent 的固定顺序调用。
Python 侧按需求改成 agent + tools：模型可用时走 create_agent 自选工具，模型不可用时
（`travel.llm.enabled=false` 或 api-key/base-url/model 缺任一）退回 Java 的固定顺序，
两条路径调用的都是那三个 Agent，取数结果由 PlanningContext 交付给确定性的 PlannerAgent，
模型不参与选点、算价或编造坐标。
"""

from langchain.agents import create_agent
from langchain_core.messages import HumanMessage
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from ..clients.llm import LlmClient, OpenAiCompatibleLlmClient
from ..exceptions import TripPlanningException
from ..models.trip_plan_request import TripPlanRequest
from .planning_context import PlanningContext, reset_planning_context, set_planning_context

_SYSTEM_PROMPT = (
    "你是旅行数据取数智能体，负责为一次规划请求拿到三类事实数据：候选景点、出行天气、候选酒店。\n"
    "要求：\n"
    "1. 数据只能通过工具获取，每个工具各调用一次，不要重复调用同一个工具。\n"
    "2. 禁止凭自己的知识编造景点、酒店、价格、坐标或天气。\n"
    "3. 城市、日期、预算和住宿偏好都由系统提供，不需要你向用户追问。\n"
    "4. 三类数据都就绪后，只用一行中文回复：数据已就绪。\n"
)


class PlanningDataAgent:

    def __init__(self, llm_client: LlmClient, tools: list[BaseTool]):
        self._llm_client = llm_client
        self._tools = tools
        self._agent: CompiledStateGraph | None = None

    def collect(self, context: PlanningContext) -> None:
        if not self._llm_client.is_available():
            self._collect_by_fixed_order(context)
            return
        token = set_planning_context(context)
        try:
            self._get_agent().invoke({"messages": [HumanMessage(content=self._build_user_prompt(context.request))]})
        finally:
            reset_planning_context(token)

    def _collect_by_fixed_order(self, context: PlanningContext) -> None:
        """没有可用模型时按 Java PlannerInputBuilder 的顺序取数：景点 -> 天气 -> 酒店。

        `self._tools` 的顺序就是那个顺序；某个源抛错即停，后面的源不再发请求，
        异常由 data_tools.raise_planning_errors 冒泡，与 Java 让异常直接逃出 build() 等价。
        """
        token = set_planning_context(context)
        try:
            for planning_tool in self._tools:
                planning_tool.invoke({})
                if context.errors:
                    break
        finally:
            reset_planning_context(token)

    def _get_agent(self) -> CompiledStateGraph:
        if self._agent is None:
            if not isinstance(self._llm_client, OpenAiCompatibleLlmClient):
                raise TripPlanningException("当前 LLM 实现不提供 LangChain chat model，无法构建取数智能体")
            self._agent = create_agent(
                self._llm_client.get_chat_model(),
                tools=self._tools,
                system_prompt=_SYSTEM_PROMPT,
            )
        return self._agent

    def _build_user_prompt(self, request: TripPlanRequest) -> str:
        return (
            f"请为目的地 {request.city}（{request.start_date} 至 {request.end_date}，"
            f"预算 {request.budget} 元，偏好 {request.preferences}，住宿偏好 {request.accommodation}）"
            "获取候选景点、出行天气和候选酒店。"
        )
