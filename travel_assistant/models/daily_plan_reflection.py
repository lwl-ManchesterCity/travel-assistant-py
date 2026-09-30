from typing import Optional

from .base import CamelModel
from .daily_plan_evaluation import DailyPlanEvaluation


class DailyPlanReflection(CamelModel):
    evaluation: Optional[DailyPlanEvaluation] = None
    recommended_action: Optional[str] = None
    reason: Optional[str] = None
    reflection_notes: Optional[list[str]] = None

    def is_acceptable(self) -> bool:
        return self.evaluation is not None and self.evaluation.is_acceptable()
