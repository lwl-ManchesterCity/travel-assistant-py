"""/plan（出一份新方案）与 /adjust（按一句话改现有方案）两个端点。"""

from fastapi import APIRouter, Depends

from ..config.container import get_trip_planning_service
from ..models.trip_adjustment_request import TripAdjustmentRequest
from ..models.trip_adjustment_response import TripAdjustmentResponse
from ..models.trip_plan import TripPlan
from ..models.auth import AuthUser
from ..models.trip_plan_request import TripPlanRequest
from ..service.trip_planning_service import TripPlanningService
from .auth_dependency import require_current_user

router = APIRouter(prefix="/api/trips")

# 这两个端点会产生真实的高德调用与 LLM 调用，所以要求登录。
# 挡的判据是"会产生成本、会读写数据"，不是"是个 API"——
# 所以 /api/map/config 没挡，它吐的是本来就要发到浏览器里的高德前端 JS key。
# 见 docs/ASK_LONGTERM_MEMORY.md §2.1.6。


# 第二档起两个端点都要**认得人**（改成参数而不是 dependencies=[...]）：
# 长期偏好按 user_id 存取，拿不到用户对象就没法注入、也没法记（§2.2.3）。

@router.post("/plan", response_model=TripPlan)
def plan_trip(
    request: TripPlanRequest,
    user: AuthUser = Depends(require_current_user),
    trip_planning_service: TripPlanningService = Depends(get_trip_planning_service),
) -> TripPlan:
    return trip_planning_service.plan_trip(request, user.id)


@router.post("/adjust", response_model=TripAdjustmentResponse)
def adjust_trip(
    request: TripAdjustmentRequest,
    user: AuthUser = Depends(require_current_user),
    trip_planning_service: TripPlanningService = Depends(get_trip_planning_service),
) -> TripAdjustmentResponse:
    return trip_planning_service.adjust_trip(request, user.id)
