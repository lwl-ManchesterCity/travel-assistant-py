from typing import Optional

from .base import CamelModel


class LoopIterationTrace(CamelModel):
    """闭环每一轮的留档：这一轮花掉多少、各项严重度、评审给了什么动作、有没有被选为最优。

    字段名与顺序保持不变：这些名字会随 TripPlan 一起序列化出去。
    """

    round: int = 0
    request_summary: Optional[str] = None
    total_cost: int = 0
    remaining_budget: int = 0
    total_route_minutes: int = 0
    reflection_score: int = 0
    budget_severity: int = 0
    route_severity: int = 0
    weather_severity: int = 0
    experience_severity: int = 0
    within_budget: bool = False
    should_replan: bool = False
    reflection_action: Optional[str] = None
    reflection_reason: Optional[str] = None
    reflection_notes: Optional[list[str]] = None
    critique_source: Optional[str] = None
    critique_action: Optional[str] = None
    critique_reason: Optional[str] = None
    critique_notes: Optional[list[str]] = None
    applied_changes: Optional[list[str]] = None
    selected_as_best: bool = False
