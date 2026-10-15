"""把景点/天气/酒店那三个 Agent 包成 LangChain @tool，交给模型自主调用。

工具内部调的还是那三个 Agent：模型决定调哪个、什么时候调，取到的数据按结构化对象写回 PlanningContext。
"""

from langchain_core.tools import BaseTool, tool

from ..exceptions import TripPlanningException
from .attraction_agent import AttractionAgent
from .hotel_agent import HotelAgent
from .planning_context import current_planning_context
from .weather_agent import WeatherAgent


def build_planning_tools(attraction_agent: AttractionAgent,
                         weather_agent: WeatherAgent,
                         hotel_agent: HotelAgent) -> list[BaseTool]:

    @tool
    def search_attractions() -> str:
        """检索本次请求目的地的候选景点。无需参数，城市与偏好取自当前旅行请求；返回候选景点摘要。"""
        context = current_planning_context()
        if context.attraction_result is not None:
            return f"候选景点已就绪：{context.attraction_result.summary}"
        try:
            context.attraction_result = attraction_agent.search(context.request)
        except Exception as exception:  # noqa: BLE001 - ToolNode 会吞掉异常，这里先记录再交给 service 抛出
            context.errors.append(exception)
            return f"景点检索失败：{exception}"
        return context.attraction_result.summary

    @tool
    def query_weather() -> str:
        """查询本次请求目的地在出行日期区间内的天气。无需参数，城市与日期取自当前旅行请求；返回天气摘要。"""
        context = current_planning_context()
        if context.weather_result is not None:
            return f"出行天气已就绪：{context.weather_result.summary}"
        try:
            context.weather_result = weather_agent.query(context.request)
        except Exception as exception:  # noqa: BLE001
            context.errors.append(exception)
            return f"天气查询失败：{exception}"
        return context.weather_result.summary

    @tool
    def recommend_hotels() -> str:
        """推荐本次请求目的地的候选酒店。无需参数，城市、预算、天数与住宿偏好取自当前旅行请求；返回酒店摘要。"""
        context = current_planning_context()
        if context.hotel_result is not None:
            return f"候选酒店已就绪：{context.hotel_result.summary}"
        try:
            context.hotel_result = hotel_agent.recommend(context.request)
        except Exception as exception:  # noqa: BLE001
            context.errors.append(exception)
            return f"酒店推荐失败：{exception}"
        return context.hotel_result.summary

    return [search_attractions, query_weather, recommend_hotels]


def raise_planning_errors(context) -> None:
    """取数结束后，把工具里暂存的异常抛出来（保留原始提示文案）。"""
    for error in context.errors:
        if isinstance(error, TripPlanningException):
            raise error
    if context.errors:
        raise context.errors[0]
