"""把批评动作落成下一轮的请求副本。"""

from dataclasses import dataclass

from ..models.loop_critique import LoopCritique
from ..models.trip_plan import TripPlan
from ..models.trip_plan_request import TripPlanRequest
from ..service.request_optimization_strategy_service import RequestOptimizationStrategyService


class LoopDecisionExecutor:

    def __init__(self, request_optimization_strategy_service: RequestOptimizationStrategyService):
        self._request_optimization_strategy_service = request_optimization_strategy_service

    def apply(self,
              request: TripPlanRequest,
              plan: TripPlan,
              critique: LoopCritique) -> "LoopDecisionExecutor.ExecutionResult":
        updated_request = self._request_optimization_strategy_service.copy_request(request)
        change_notes: list[str] = []
        self._request_optimization_strategy_service.apply_action(
            updated_request, plan, critique.action, change_notes
        )
        return LoopDecisionExecutor.ExecutionResult(updated_request, change_notes)

    @dataclass
    class ExecutionResult:
        updated_request: TripPlanRequest
        change_notes: list[str]
