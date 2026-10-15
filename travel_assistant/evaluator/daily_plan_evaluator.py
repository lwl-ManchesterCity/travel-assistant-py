"""把预算、路线、天气三家的判定合成当天评估，并给出当天的反思建议动作。

每日反思的判定按落位约定放在 evaluator 包，agent/reflection_agent.py 的 reflect_daily_plan
只是回指这里的薄封装。
"""

from ..models.daily_plan_evaluation import DailyPlanEvaluation
from ..models.daily_plan_reflection import DailyPlanReflection
from ..models.planning_constraints import PlanningConstraints
from ..models.route_plan import RoutePlan
from ..models.weather_info import WeatherInfo
from .budget_evaluator import BudgetEvaluator
from .route_evaluator import RouteEvaluator
from .weather_evaluator import WeatherEvaluator


class DailyPlanEvaluator:

    def __init__(self, budget_evaluator: BudgetEvaluator, route_evaluator: RouteEvaluator, weather_evaluator: WeatherEvaluator):
        self._budget_evaluator = budget_evaluator
        self._route_evaluator = route_evaluator
        self._weather_evaluator = weather_evaluator

    def evaluate(self,
                 estimated_cost: int,
                 daily_budget_limit: int,
                 route_plan: RoutePlan | None,
                 constraints: PlanningConstraints | None,
                 current_weather: WeatherInfo | None) -> DailyPlanEvaluation:
        indoor_adjustment_recommended = self._weather_evaluator.should_prefer_indoor(constraints, current_weather)
        over_budget = self._budget_evaluator.is_over_budget(estimated_cost, daily_budget_limit)
        route_too_long = self._route_evaluator.is_route_too_long(
            route_plan, constraints, current_weather, indoor_adjustment_recommended
        )
        route_limit_minutes = self._route_evaluator.determine_route_limit_minutes(constraints, indoor_adjustment_recommended)
        budget_overflow_amount = max(
            0, estimated_cost - (daily_budget_limit + self._budget_evaluator.get_budget_tolerance())
        )
        route_overflow_minutes = self._route_evaluator.calculate_route_overflow_minutes(
            route_plan, constraints, indoor_adjustment_recommended
        )
        route_overflow_distance_km = self._route_evaluator.calculate_route_overflow_distance_km(route_plan)
        budget_diagnosis = (
            f"当日预计花费超出预算缓冲 {budget_overflow_amount} 元" if over_budget else "当日预算处于可控范围"
        )
        route_diagnosis = (
            f"当日通勤超出建议阈值，超出约 {route_overflow_minutes} 分钟" if route_too_long else "当日通勤处于可接受范围"
        )
        weather_diagnosis = (
            "当天天气不佳，建议优先安排室内或低暴露景点" if indoor_adjustment_recommended else "当天天气对当前景点结构影响较小"
        )
        return DailyPlanEvaluation(
            over_budget=over_budget,
            route_too_long=route_too_long,
            indoor_adjustment_recommended=indoor_adjustment_recommended,
            budget_tolerance=self._budget_evaluator.get_budget_tolerance(),
            route_limit_minutes=route_limit_minutes,
            budget_overflow_amount=budget_overflow_amount,
            route_overflow_minutes=route_overflow_minutes,
            route_overflow_distance_km=route_overflow_distance_km,
            budget_diagnosis=budget_diagnosis,
            route_diagnosis=route_diagnosis,
            weather_diagnosis=weather_diagnosis,
        )

    def reflect_daily_plan(self,
                           estimated_cost: int,
                           daily_budget_limit: int,
                           route_plan: RoutePlan | None,
                           constraints: PlanningConstraints | None,
                           current_weather: WeatherInfo | None) -> DailyPlanReflection:
        evaluation = self.evaluate(estimated_cost, daily_budget_limit, route_plan, constraints, current_weather)
        action = self._recommend_action(evaluation)
        return DailyPlanReflection(
            evaluation=evaluation,
            recommended_action=action,
            reason=self._build_reason(evaluation, action),
            reflection_notes=self._build_reflection_notes(evaluation, action),
        )

    def _recommend_action(self, evaluation: DailyPlanEvaluation) -> str:
        if evaluation.is_acceptable():
            return "accept"
        if evaluation.indoor_adjustment_recommended:
            return "replace_with_indoor"
        if evaluation.over_budget:
            return "reduce_budget"
        if evaluation.route_too_long:
            return "compress_route"
        return "manual_review"

    def _build_reason(self, evaluation: DailyPlanEvaluation, action: str) -> str:
        if action == "accept":
            return "预算和路线均处于可接受范围"
        if action == "replace_with_indoor":
            return evaluation.weather_diagnosis
        if action == "reduce_budget":
            return evaluation.budget_diagnosis
        if action == "compress_route":
            return evaluation.route_diagnosis
        return "当前方案存在约束冲突，需要进入保底调整"

    def _build_reflection_notes(self, evaluation: DailyPlanEvaluation, action: str) -> list[str]:
        notes = [f"ReflectionAgent建议：{self._translate_action(action)}"]
        if evaluation.over_budget:
            notes.append(evaluation.budget_diagnosis)
        if evaluation.route_too_long:
            notes.append(evaluation.route_diagnosis)
        if evaluation.indoor_adjustment_recommended:
            notes.append(evaluation.weather_diagnosis)
        return notes

    def _translate_action(self, action: str) -> str:
        if action == "accept":
            return "接受当前方案"
        if action == "replace_with_indoor":
            return "优先替换为室内或低暴露景点"
        if action == "reduce_budget":
            return "优先压缩预算"
        if action == "compress_route":
            return "优先压缩通勤"
        return "进入保底调整"
