"""每次规划请求的取数上下文：工具把结构化结果写回这里，PlannerAgent 再从上下文消费。

LangChain 的工具返回值只回到模型；景点/酒店/天气必须按结构化字段进入 planner，
所以用 contextvar 把同一次请求的结果留在进程内，而不是从模型文本里解析。
"""

from contextvars import ContextVar
from dataclasses import dataclass

from ..models.attraction_search_result import AttractionSearchResult
from ..models.hotel_search_result import HotelSearchResult
from ..models.trip_plan_request import TripPlanRequest
from ..models.weather_query_result import WeatherQueryResult


@dataclass
class PlanningContext:
    request: TripPlanRequest
    attraction_result: AttractionSearchResult | None = None
    weather_result: WeatherQueryResult | None = None
    hotel_result: HotelSearchResult | None = None
    # LangChain 的 ToolNode 会把工具异常转成错误消息给模型，异常本身到不了 service 层；
    # 这里把异常暂存下来，取数结束后按 Java 的方式抛出，保证 400 里的提示文案一致。
    errors: list[Exception] = None

    def __post_init__(self) -> None:
        if self.errors is None:
            self.errors = []


_context_var: ContextVar[PlanningContext | None] = ContextVar("travel_planning_context", default=None)


def set_planning_context(context: PlanningContext):
    return _context_var.set(context)


def reset_planning_context(token) -> None:
    _context_var.reset(token)


def current_planning_context() -> PlanningContext:
    context = _context_var.get()
    if context is None:
        raise RuntimeError("取数工具只能在一次规划请求的上下文内调用")
    return context
