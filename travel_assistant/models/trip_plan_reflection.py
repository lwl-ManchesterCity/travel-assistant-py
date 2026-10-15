from typing import Optional

from .base import CamelModel


class TripPlanReflection(CamelModel):
    """一轮反思的结论：是否可接受、各项超标多少、严重度与总分、建议怎么动。

    字段与零值默认值不要随手改：评审与选优都直接读这些名字和数值。
    """

    acceptable: bool = False
    over_budget: bool = False
    route_too_long: bool = False
    indoor_adjustment_recommended: bool = False
    total_budget_overflow_amount: int = 0
    total_route_overflow_minutes: int = 0
    budget_severity: int = 0
    route_severity: int = 0
    weather_severity: int = 0
    experience_severity: int = 0
    overall_score: int = 0
    recommended_action: Optional[str] = None
    reason: Optional[str] = None
    reflection_notes: Optional[list[str]] = None
