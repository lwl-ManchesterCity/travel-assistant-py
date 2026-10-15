"""生成-评估-反思-修正的多轮闭环。

每一轮都复用 PlannerInputBuilder -> PlannerAgent 这条既有链路（取数仍由 LangChain 取数智能体
自选工具完成），本类只做评分、停止条件与下一轮请求的调整，不引入新的取数顺序。
"""

from typing import TYPE_CHECKING

from ..config.settings import LoopSettings
from ..models.agent_trace import AgentTrace
from ..models.loop_critique import LoopCritique
from ..models.loop_execution_summary import LoopExecutionSummary
from ..models.loop_iteration_trace import LoopIterationTrace
from ..models.trip_plan import TripPlan
from ..models.trip_plan_reflection import TripPlanReflection
from ..models.trip_plan_request import TripPlanRequest
from ..utils import render_list
from .llm_critic_agent import LlmCriticAgent
from .loop_decision_executor import LoopDecisionExecutor
from .planner_agent import PlannerAgent
from .reflection_agent import ReflectionAgent, TripPlanRequestView

if TYPE_CHECKING:  # 只做类型标注：agent 包运行期导入 service 包会形成循环导入
    from ..service.planner_input_builder import PlannerInputBuilder


class LoopPlanningAgent:

    def __init__(self,
                 planner_input_builder: "PlannerInputBuilder",
                 planner_agent: PlannerAgent,
                 reflection_agent: ReflectionAgent,
                 llm_critic_agent: LlmCriticAgent,
                 loop_decision_executor: LoopDecisionExecutor,
                 travel_loop_properties: LoopSettings):
        self._planner_input_builder = planner_input_builder
        self._planner_agent = planner_agent
        self._reflection_agent = reflection_agent
        self._llm_critic_agent = llm_critic_agent
        self._loop_decision_executor = loop_decision_executor
        self._travel_loop_properties = travel_loop_properties

    def plan(self, original_request: TripPlanRequest, user_id: int | None = None) -> TripPlan:
        """`user_id` 只往下传给 builder（长期偏好按它存取）。None 时行为与从前一字不差。"""
        if not self._travel_loop_properties.enabled:
            trip_plan = self._planner_agent.build_plan(
                self._planner_input_builder.build(original_request, user_id))
            self._append_loop_result(trip_plan, [], [], 1, "loop-disabled")
            return trip_plan

        working_request = self._copy_request(original_request)
        loop_traces: list[AgentTrace] = []
        loop_iterations: list[LoopIterationTrace] = []
        seen_request_signatures: set[str] = set()
        best_plan: TripPlan | None = None
        best_score = float("inf")
        previous_score = float("inf")
        best_round = 1
        no_improvement_rounds = 0

        for round_number in range(1, max(1, self._travel_loop_properties.max_rounds) + 1):
            candidate_plan = self._planner_agent.build_plan(
                self._planner_input_builder.build(working_request, user_id))
            loop_traces.append(AgentTrace(
                agent_name="LoopPlanningAgent",
                action=f"第 {round_number} 轮生成初稿",
                input_summary=self._build_request_summary(working_request),
                output_summary=(
                    f"总费用={candidate_plan.budget.total}，"
                    f"剩余预算={candidate_plan.budget.remaining_budget}"
                ),
                status="success",
            ))

            reflection = self._reflection_agent.reflect_trip_plan(
                candidate_plan,
                TripPlanRequestView(working_request.budget),
            )
            candidate_score = self._score_plan(candidate_plan, reflection)
            loop_traces.append(AgentTrace(
                agent_name="ReflectionAgent",
                action=f"评估第 {round_number} 轮整份方案",
                input_summary=(
                    f"总费用={candidate_plan.budget.total}，"
                    f"总通勤={self._calculate_total_route_minutes(candidate_plan)} 分钟"
                ),
                output_summary=(
                    f"建议动作={reflection.recommended_action}，"
                    f"原因={reflection.reason}，"
                    f"评分={reflection.overall_score}"
                ),
                status="accept" if reflection.acceptable else "review",
            ))

            if candidate_score < best_score:
                best_plan = candidate_plan
                best_score = candidate_score
                best_round = round_number
            if round_number == 1 or candidate_score < previous_score:
                no_improvement_rounds = 0
            else:
                no_improvement_rounds += 1
            previous_score = candidate_score

            if reflection.acceptable:
                loop_iterations.append(self._build_iteration_trace(
                    round_number, working_request, candidate_plan, reflection, None, [], round_number == best_round
                ))
                self._mark_selected_round(loop_iterations, round_number)
                self._append_loop_result(
                    candidate_plan, loop_traces, loop_iterations, round_number,
                    f"第 {round_number} 轮通过规则评估，直接停止"
                )
                return candidate_plan

            critique = self._llm_critic_agent.critique(working_request, candidate_plan, reflection)
            loop_traces.append(AgentTrace(
                agent_name="LlmCriticAgent",
                action=f"评估第 {round_number} 轮方案",
                input_summary=f"预算={candidate_plan.budget.total}，天数={len(candidate_plan.days)}",
                output_summary=(
                    f"动作={critique.action}，原因={critique.reason}，来源={critique.source}"
                ),
                status="replan" if critique.is_should_replan() else "accept",
            ))
            loop_iterations.append(self._build_iteration_trace(
                round_number, working_request, candidate_plan, reflection, critique, [], round_number == best_round
            ))

            if not critique.is_should_replan():
                self._mark_selected_round(loop_iterations, round_number)
                self._append_loop_result(
                    candidate_plan, loop_traces, loop_iterations, round_number,
                    f"第 {round_number} 轮达到可接受状态"
                )
                return candidate_plan

            execution_result = self._loop_decision_executor.apply(working_request, candidate_plan, critique)
            loop_iterations[-1] = self._build_iteration_trace(
                round_number,
                working_request,
                candidate_plan,
                reflection,
                critique,
                execution_result.change_notes,
                round_number == best_round,
            )
            if no_improvement_rounds >= max(1, self._travel_loop_properties.max_no_improvement_rounds):
                self._mark_selected_round(loop_iterations, best_round)
                selected_plan = candidate_plan if best_plan is None else best_plan
                self._append_loop_result(
                    selected_plan, loop_traces, loop_iterations, best_round,
                    f"连续 {no_improvement_rounds} 轮没有改善，提前停止并保留当前最优方案"
                )
                return selected_plan
            if len(execution_result.change_notes) == 0:
                self._mark_selected_round(loop_iterations, best_round)
                selected_plan = candidate_plan if best_plan is None else best_plan
                self._append_loop_result(
                    selected_plan, loop_traces, loop_iterations, best_round,
                    "批评已给出，但未生成新的可执行调整"
                )
                return selected_plan

            next_request = execution_result.updated_request
            request_signature = self._build_request_signature(next_request)
            loop_traces.append(AgentTrace(
                agent_name="LoopDecisionExecutor",
                action=f"生成第 {round_number + 1} 轮输入",
                input_summary=(
                    critique.reason
                    if critique.notes is None or len(critique.notes) == 0
                    else "；".join(critique.notes)
                ),
                output_summary="；".join(execution_result.change_notes),
                status="success",
            ))
            if request_signature in seen_request_signatures:
                self._mark_selected_round(loop_iterations, best_round)
                selected_plan = candidate_plan if best_plan is None else best_plan
                self._append_loop_result(
                    selected_plan, loop_traces, loop_iterations, best_round,
                    "检测到循环调整重复，保留当前最优方案"
                )
                return selected_plan
            seen_request_signatures.add(request_signature)
            working_request = next_request

        self._mark_selected_round(loop_iterations, best_round)
        self._append_loop_result(
            best_plan, loop_traces, loop_iterations, best_round,
            "达到最大循环轮次，保留当前最优方案"
        )
        return best_plan

    def _append_loop_result(self,
                            trip_plan: TripPlan,
                            loop_traces: list[AgentTrace],
                            loop_iterations: list[LoopIterationTrace],
                            best_round: int,
                            loop_summary: str) -> None:
        planning_notes = [] if trip_plan.planning_notes is None else list(trip_plan.planning_notes)
        planning_notes.append("LoopPlanningAgent 已完成生成-评估-反思-修正闭环")
        planning_notes.append(f"LoopPlanningAgent 选定第 {best_round} 轮作为当前最优方案")
        planning_notes.append(loop_summary)
        trip_plan.planning_notes = planning_notes

        merged_traces = [] if trip_plan.agent_traces is None else list(trip_plan.agent_traces)
        merged_traces[0:0] = loop_traces
        trip_plan.agent_traces = merged_traces
        trip_plan.loop_summary = LoopExecutionSummary(
            enabled=self._travel_loop_properties.enabled,
            rounds_executed=len(loop_iterations),
            selected_round=best_round,
            stop_reason=loop_summary,
        )
        trip_plan.loop_iterations = loop_iterations

    def _build_iteration_trace(self,
                               round_number: int,
                               request: TripPlanRequest,
                               candidate_plan: TripPlan,
                               reflection: TripPlanReflection | None,
                               critique: LoopCritique | None,
                               applied_changes: list[str],
                               selected_as_best: bool) -> LoopIterationTrace:
        return LoopIterationTrace(
            round=round_number,
            request_summary=self._build_request_summary(request),
            total_cost=0 if candidate_plan.budget is None else candidate_plan.budget.total,
            remaining_budget=0 if candidate_plan.budget is None else candidate_plan.budget.remaining_budget,
            total_route_minutes=self._calculate_total_route_minutes(candidate_plan),
            reflection_score=0 if reflection is None else reflection.overall_score,
            budget_severity=0 if reflection is None else reflection.budget_severity,
            route_severity=0 if reflection is None else reflection.route_severity,
            weather_severity=0 if reflection is None else reflection.weather_severity,
            experience_severity=0 if reflection is None else reflection.experience_severity,
            within_budget=candidate_plan.budget is not None and candidate_plan.budget.within_budget,
            should_replan=critique is not None and critique.is_should_replan(),
            reflection_action=None if reflection is None else reflection.recommended_action,
            reflection_reason=None if reflection is None else reflection.reason,
            reflection_notes=None if reflection is None else reflection.reflection_notes,
            critique_source=None if critique is None else critique.source,
            critique_action=None if critique is None else critique.action,
            critique_reason=None if critique is None else critique.reason,
            critique_notes=None if critique is None else critique.notes,
            applied_changes=applied_changes,
            selected_as_best=selected_as_best,
        )

    def _mark_selected_round(self, loop_iterations: list[LoopIterationTrace], selected_round: int) -> None:
        for loop_iteration in loop_iterations:
            loop_iteration.selected_as_best = loop_iteration.round == selected_round

    def _score_plan(self, trip_plan: TripPlan, reflection: TripPlanReflection | None) -> float:
        if reflection is not None:
            return reflection.overall_score
        overflow = (
            0
            if trip_plan.budget is None or trip_plan.budget.within_budget
            else abs(trip_plan.budget.remaining_budget)
        )
        total_duration_minutes = self._calculate_total_route_minutes(trip_plan)
        return overflow * 10.0 + total_duration_minutes

    def _calculate_total_route_minutes(self, trip_plan: TripPlan) -> int:
        total_duration_minutes = 0
        if trip_plan.days is not None:
            for day in trip_plan.days:
                if day.route_plan is not None:
                    total_duration_minutes += day.route_plan.total_duration_minutes
        return total_duration_minutes

    def _build_request_summary(self, request: TripPlanRequest) -> str:
        return (
            f"城市={request.city}"
            f"，预算={request.budget}"
            f"，住宿={request.accommodation}"
            f"，交通={'智能推荐' if request.transportation is None else request.transportation}"
        )

    def _build_request_signature(self, request: TripPlanRequest) -> str:
        return "|".join((
            _signature_part(request.city),
            _signature_part(request.budget),
            _signature_part(request.accommodation),
            _signature_part(request.transportation),
            _signature_part(request.extra_requirements),
            _signature_part(request.preferences),
        ))

    def _copy_request(self, request: TripPlanRequest) -> TripPlanRequest:
        return TripPlanRequest(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            budget=request.budget,
            preferences=[] if request.preferences is None else list(request.preferences),
            transportation=request.transportation,
            accommodation=request.accommodation,
            extra_requirements=request.extra_requirements,
            # 这三格漏掉的后果很隐蔽：loop 第一轮就把它们丢了，整趟看起来"没填"。
            hotel_anchor=request.hotel_anchor,
            day_hotel_anchors=dict(request.day_hotel_anchors) if request.day_hotel_anchors else None,
            hotel_continuity=request.hotel_continuity,
            day_attraction_pins=({day: list(names) for day, names in request.day_attraction_pins.items()}
                                 if request.day_attraction_pins else None),
            origin_city=request.origin_city,
        )


def _signature_part(value: object) -> str:
    """拼签名的取值口径：None 渲染成 "null"，列表走 `render_list`，其余走 `str()`。"""
    if value is None:
        return "null"
    if isinstance(value, list):
        return render_list(value)
    return str(value)
