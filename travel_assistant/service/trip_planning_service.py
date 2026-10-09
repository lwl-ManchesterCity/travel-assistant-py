"""对应 Java service/TripPlanningService.java：planTrip 走 LoopPlanningAgent，adjustTrip 先解析再复用 planTrip。

Java 构造器里的 plannerInputBuilder/plannerAgent 两个参数在两个方法里都没有被用到
（Spring 注入的历史遗留），Python 侧不保留这两个空参数，差异记在 MIGRATION_NOTES.md。
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

    def plan_trip(self, request: TripPlanRequest) -> TripPlan:
        return self._loop_planning_agent.plan(request)

    def adjust_trip(self, request: TripAdjustmentRequest) -> TripAdjustmentResponse:
        decision = self._trip_adjustment_agent.adjust(
            request.original_request,
            request.adjustment_prompt,
            request.anchor_hint,
            request.anchor_day,
            request.pin_hints,
            request.pin_day,
            request.pin_move_hotel,
        )
        trip_plan = self.plan_trip(decision.updated_request)
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
