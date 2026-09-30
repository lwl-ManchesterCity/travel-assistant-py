from typing import Optional

from .base import CamelModel


class DailyPlanEvaluation(CamelModel):
    over_budget: bool = False
    route_too_long: bool = False
    indoor_adjustment_recommended: bool = False
    budget_tolerance: int = 0
    route_limit_minutes: int = 0
    budget_overflow_amount: int = 0
    route_overflow_minutes: int = 0
    route_overflow_distance_km: float = 0.0
    budget_diagnosis: Optional[str] = None
    route_diagnosis: Optional[str] = None
    weather_diagnosis: Optional[str] = None

    def is_acceptable(self) -> bool:
        return not self.over_budget and not self.route_too_long
