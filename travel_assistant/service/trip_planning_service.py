"""planTrip 走 LoopPlanningAgent，adjustTrip 先解析再复用 planTrip。

构造器只收两个 agent：早先注入进来却从没用过的两个空参数这里不再保留，
取舍见 docs/DESIGN_NOTES.md。
"""

from typing import TYPE_CHECKING

from ..models.trip_adjustment_request import TripAdjustmentRequest
from ..models.trip_adjustment_response import TripAdjustmentResponse
from ..models.trip_plan import TripPlan
from ..models.trip_plan_request import TripPlanRequest

if TYPE_CHECKING:  # 只做类型标注：运行期导入会和 agent 包形成循环导入
    from ..agent.loop_planning_agent import LoopPlanningAgent
    from ..agent.trip_adjustment_agent import TripAdjustmentAgent


class TripPlanningService:

    def __init__(self,
                 trip_adjustment_agent: "TripAdjustmentAgent",
                 loop_planning_agent: "LoopPlanningAgent"):
        self._trip_adjustment_agent = trip_adjustment_agent
        self._loop_planning_agent = loop_planning_agent

    def plan_trip(self, request: TripPlanRequest, user_id: int | None = None) -> TripPlan:
        return self._loop_planning_agent.plan(request, user_id)

    def adjust_trip(self, request: TripAdjustmentRequest,
                    user_id: int | None = None) -> TripAdjustmentResponse:
        """`user_id` 只用来存取长期偏好。改方案最后也走 plan_trip，两条路共用一处（§2.2.4）。

        注意它在**每一轮 loop 迭代**里都会过一次 builder，也就是每一轮都会试写一次
        偏好——反复写同一条是幂等的（库里 unique 去重），代价只是几次小查询。
        """
        decision = self._trip_adjustment_agent.adjust(
            request.original_request,
            request.adjustment_prompt,
            request.anchor_hint,
            request.anchor_day,
            request.pin_hints,
            request.pin_day,
            request.pin_move_hotel,
        )
        trip_plan = self.plan_trip(decision.updated_request, user_id)
        planning_notes = [] if trip_plan.planning_notes is None else list(trip_plan.planning_notes)
        planning_notes.append(f"改计划解析来源：{decision.adjustment_source}")
        planning_notes.append(
            "本次改计划要求：" + ("null" if request.adjustment_prompt is None else request.adjustment_prompt)
        )
        planning_notes.extend(decision.applied_changes)
        trip_plan.planning_notes = planning_notes
        return TripAdjustmentResponse(
            trip_plan=trip_plan,
            updated_request=decision.updated_request,
            applied_changes=decision.applied_changes,
        )
