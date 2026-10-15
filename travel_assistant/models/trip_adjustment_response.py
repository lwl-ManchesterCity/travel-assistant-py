from typing import Optional

from .base import CamelModel
from .trip_plan import TripPlan
from .trip_plan_request import TripPlanRequest


class TripAdjustmentResponse(CamelModel):
    """一次调整的结果：新方案、更新后的请求，以及改了哪些地方。

    adjustmentSource 不进响应体，只落进 planningNotes。
    """

    trip_plan: Optional[TripPlan] = None
    updated_request: Optional[TripPlanRequest] = None
    applied_changes: Optional[list[str]] = None
