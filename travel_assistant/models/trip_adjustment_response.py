from typing import Optional

from .base import CamelModel
from .trip_plan import TripPlan
from .trip_plan_request import TripPlanRequest


class TripAdjustmentResponse(CamelModel):
    """对应 Java model/TripAdjustmentResponse.java（Java 的 adjustmentSource 不进响应体，只进 planningNotes）。"""

    trip_plan: Optional[TripPlan] = None
    updated_request: Optional[TripPlanRequest] = None
    applied_changes: Optional[list[str]] = None
