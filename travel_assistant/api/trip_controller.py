"""对应 Java controller/TripPlanController.java 的 /plan 与 /adjust 两个端点。"""

from fastapi import APIRouter, Depends

from ..config.container import get_trip_planning_service
from ..models.trip_adjustment_request import TripAdjustmentRequest
from ..models.trip_adjustment_response import TripAdjustmentResponse
from ..models.trip_plan import TripPlan
from ..models.trip_plan_request import TripPlanRequest
from ..service.trip_planning_service import TripPlanningService

router = APIRouter(prefix="/api/trips")


@router.post("/plan", response_model=TripPlan)
def plan_trip(
    request: TripPlanRequest,
    trip_planning_service: TripPlanningService = Depends(get_trip_planning_service),
) -> TripPlan:
    return trip_planning_service.plan_trip(request)


@router.post("/adjust", response_model=TripAdjustmentResponse)
def adjust_trip(
    request: TripAdjustmentRequest,
    trip_planning_service: TripPlanningService = Depends(get_trip_planning_service),
) -> TripAdjustmentResponse:
    return trip_planning_service.adjust_trip(request)
