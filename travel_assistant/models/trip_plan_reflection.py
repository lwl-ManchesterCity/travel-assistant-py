from typing import Optional

from .base import CamelModel


class TripPlanReflection(CamelModel):
    """对应 Java model/TripPlanReflection.java，字段名、顺序与零值默认值保持一致。"""

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
