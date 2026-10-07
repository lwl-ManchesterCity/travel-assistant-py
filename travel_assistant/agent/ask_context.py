"""一次追问的请求级上下文：工具从这里读方案和原始请求，往这里写留痕。

与 agent/planning_context.py 同一套做法（contextvar 而非函数参数），因为 LangChain 的
工具签名由模型那边决定，参数只能从上下文里取；工具返回值只会回到模型文本里，
结构化留痕必须自己存一份。
"""

from contextvars import ContextVar
from dataclasses import dataclass, field

from ..models.agent_trace import AgentTrace
from ..models.trip_plan import TripPlan
from ..models.trip_plan_request import TripPlanRequest

_OUTPUT_LIMIT = 300


@dataclass
class AskContext:
    question: str
    trip_plan: TripPlan
    request: TripPlanRequest
    traces: list[AgentTrace] = field(default_factory=list)
    day_cursor: int = 0

    def next_day_index(self, upper_bound: int) -> int:
        index = self.day_cursor
        if index >= upper_bound:
            return -1
        self.day_cursor = index + 1
        return index

    def record(self, tool_name: str, action: str, input_summary: str,
               output_summary: str, status: str = "success") -> None:
        text = output_summary or ""
        if len(text) > _OUTPUT_LIMIT:
            text = text[:_OUTPUT_LIMIT] + "…"
        self.traces.append(AgentTrace(
            agent_name=tool_name,
            action=action,
            input_summary=input_summary,
            output_summary=text,
            status=status,
        ))


_context_var: ContextVar[AskContext | None] = ContextVar("travel_ask_context", default=None)


def set_ask_context(context: AskContext):
    return _context_var.set(context)


def reset_ask_context(token) -> None:
    _context_var.reset(token)


def current_ask_context() -> AskContext:
    context = _context_var.get()
    if context is None:
        raise RuntimeError("追问工具只能在一次追问请求的上下文内调用")
    return context
