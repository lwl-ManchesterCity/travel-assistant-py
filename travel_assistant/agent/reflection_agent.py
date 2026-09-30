"""对应 Java agent/ReflectionAgent.java。

reflectDailyPlan 的实现仍在 evaluator/daily_plan_evaluator.py（第二轮批准的落位约定），
这里按那个约定做薄封装回指，不复制第二份判定逻辑；reflectTripPlan 及其评分/动作私有方法
是 Java 里 Python 侧原本完全缺失的部分，按源码逐条搬过来。
"""

from dataclasses import dataclass

from ..evaluator.daily_plan_evaluator import DailyPlanEvaluator
from ..models.daily_plan_reflection import DailyPlanReflection
from ..models.planning_constraints import PlanningConstraints
from ..models.route_plan import RoutePlan
from ..models.trip_plan import TripPlan
from ..models.trip_plan_reflection import TripPlanReflection
from ..models.weather_info import WeatherInfo
from ..utils import jround

_RISKY_WEATHER_KEYWORDS = ("雨", "雪", "雷")
_ACCEPTABLE_OVERALL_SCORE_LIMIT = 15


@dataclass
class TripPlanRequestView:
    """对应 Java ReflectionAgent.TripPlanRequestView record。"""

    requested_budget: int


class ReflectionAgent:

    def __init__(self, daily_plan_evaluator: DailyPlanEvaluator):
        self._daily_plan_evaluator = daily_plan_evaluator

    def reflect_daily_plan(self,
                           estimated_cost: int,
                           daily_budget_limit: int,
                           route_plan: RoutePlan | None,
                           constraints: PlanningConstraints | None,
                           current_weather: WeatherInfo | None) -> DailyPlanReflection:
        return self._daily_plan_evaluator.reflect_daily_plan(
            estimated_cost, daily_budget_limit, route_plan, constraints, current_weather
        )

    def reflect_trip_plan(self,
                          trip_plan: TripPlan,
                          request_view: TripPlanRequestView | None) -> TripPlanReflection:
        total_route_minutes = 0
        route_overflow_minutes = 0
        indoor_adjustment_recommended = False
        notes: list[str] = []

        if trip_plan.days is not None:
            for day_plan in trip_plan.days:
                if day_plan.route_plan is not None:
                    total_route_minutes += day_plan.route_plan.total_duration_minutes
        expected_route_limit = 0 if trip_plan.days is None else len(trip_plan.days) * 180
        if total_route_minutes > expected_route_limit:
            route_overflow_minutes = total_route_minutes - expected_route_limit
        if trip_plan.weather_info is not None:
            for weather_info in trip_plan.weather_info:
                if _is_risky_weather(weather_info.day_weather):
                    indoor_adjustment_recommended = True
                    break

        over_budget = trip_plan.budget is not None and not trip_plan.budget.within_budget
        budget_overflow_amount = abs(trip_plan.budget.remaining_budget) if over_budget else 0
        route_too_long = route_overflow_minutes > 0
        budget_severity = self._calculate_budget_severity(over_budget, budget_overflow_amount, request_view)
        route_severity = self._calculate_route_severity(route_overflow_minutes, trip_plan)
        weather_severity = self._calculate_weather_severity(indoor_adjustment_recommended, trip_plan)
        experience_severity = self._calculate_experience_severity(trip_plan, request_view)
        overall_score = self._calculate_overall_score(
            budget_severity, route_severity, weather_severity, experience_severity
        )
        acceptable = overall_score <= _ACCEPTABLE_OVERALL_SCORE_LIMIT
        action = self._recommend_trip_action(
            budget_severity, route_severity, weather_severity, experience_severity, acceptable
        )
        reason = self._build_trip_reason(
            action, budget_overflow_amount, route_overflow_minutes, indoor_adjustment_recommended, overall_score
        )

        if over_budget:
            notes.append(f"总预算超支 {budget_overflow_amount} 元")
        if route_too_long:
            notes.append(f"总通勤超出建议阈值约 {route_overflow_minutes} 分钟")
        if indoor_adjustment_recommended:
            notes.append("出行期存在雨雪雷天气，建议增加室内备选")
        if experience_severity > 0:
            notes.append("预算结余较多，当前方案体验利用偏保守")
        notes.append(
            f"当前评分={overall_score}"
            f"（预算={budget_severity}"
            f"，路线={route_severity}"
            f"，天气={weather_severity}"
            f"，体验={experience_severity}）"
        )
        if acceptable:
            notes.append("整份方案在预算、路线、天气上都处于可接受范围")

        return TripPlanReflection(
            acceptable=acceptable,
            over_budget=over_budget,
            route_too_long=route_too_long,
            indoor_adjustment_recommended=indoor_adjustment_recommended,
            total_budget_overflow_amount=budget_overflow_amount,
            total_route_overflow_minutes=route_overflow_minutes,
            budget_severity=budget_severity,
            route_severity=route_severity,
            weather_severity=weather_severity,
            experience_severity=experience_severity,
            overall_score=overall_score,
            recommended_action=action,
            reason=reason,
            reflection_notes=notes,
        )

    def _recommend_trip_action(self,
                               budget_severity: int,
                               route_severity: int,
                               weather_severity: int,
                               experience_severity: int,
                               acceptable: bool) -> str:
        if acceptable:
            return "STOP"
        if (budget_severity >= route_severity and budget_severity >= weather_severity
                and budget_severity >= experience_severity and budget_severity > 0):
            return "REDUCE_BUDGET"
        if (route_severity >= budget_severity and route_severity >= weather_severity
                and route_severity >= experience_severity and route_severity > 0):
            return "COMPRESS_ROUTE"
        if (weather_severity >= budget_severity and weather_severity >= route_severity
                and weather_severity >= experience_severity and weather_severity > 0):
            return "REPLACE_OUTDOOR_WITH_INDOOR"
        if experience_severity > 0:
            return "UPGRADE_EXPERIENCE"
        return "STOP"

    def _build_trip_reason(self,
                           action: str,
                           budget_overflow_amount: int,
                           route_overflow_minutes: int,
                           indoor_adjustment_recommended: bool,
                           overall_score: int) -> str:
        if action == "REDUCE_BUDGET":
            return (f"当前方案整体评分为 {overall_score}，其中预算问题最突出，"
                    f"超支 {budget_overflow_amount} 元，建议优先压缩预算")
        if action == "COMPRESS_ROUTE":
            return (f"当前方案整体评分为 {overall_score}，其中路线问题最突出，"
                    f"通勤超出建议阈值 {route_overflow_minutes} 分钟，建议优先压缩路线")
        if action == "REPLACE_OUTDOOR_WITH_INDOOR":
            if indoor_adjustment_recommended:
                return f"当前方案整体评分为 {overall_score}，天气存在风险，建议增加室内备选方案"
            return "建议替换部分户外景点"
        if action == "UPGRADE_EXPERIENCE":
            return (f"当前方案整体评分为 {overall_score}，预算利用偏保守，"
                    f"可适度提升住宿或体验强度")
        return f"当前方案整体评分为 {overall_score}，已处于可接受范围，无需继续重规划"

    def _calculate_budget_severity(self,
                                   over_budget: bool,
                                   budget_overflow_amount: int,
                                   request_view: TripPlanRequestView | None) -> int:
        if not over_budget or request_view is None or request_view.requested_budget <= 0:
            return 0
        ratio = budget_overflow_amount / request_view.requested_budget
        return self._clamp_score(jround(20 + ratio * 100))

    def _calculate_route_severity(self, route_overflow_minutes: int, trip_plan: TripPlan) -> int:
        if route_overflow_minutes <= 0 or trip_plan.days is None or len(trip_plan.days) == 0:
            return 0
        expected_route_limit = len(trip_plan.days) * 180
        ratio = 0 if expected_route_limit <= 0 else route_overflow_minutes / expected_route_limit
        return self._clamp_score(jround(15 + ratio * 120))

    def _calculate_weather_severity(self, indoor_adjustment_recommended: bool, trip_plan: TripPlan) -> int:
        if (not indoor_adjustment_recommended
                or trip_plan.weather_info is None
                or len(trip_plan.weather_info) == 0):
            return 0
        risky_days = sum(1 for weather_info in trip_plan.weather_info if _is_risky_weather(weather_info.day_weather))
        return self._clamp_score(risky_days * 25)

    def _calculate_experience_severity(self, trip_plan: TripPlan, request_view: TripPlanRequestView | None) -> int:
        if (trip_plan.budget is None
                or not trip_plan.budget.within_budget
                or request_view is None
                or request_view.requested_budget < 5000):
            return 0
        remaining_budget = trip_plan.budget.remaining_budget
        threshold = max(1800, request_view.requested_budget // 3)
        if remaining_budget <= threshold:
            return 0
        ratio = (remaining_budget - threshold) / request_view.requested_budget
        return self._clamp_score(jround(10 + ratio * 80))

    def _calculate_overall_score(self,
                                 budget_severity: int,
                                 route_severity: int,
                                 weather_severity: int,
                                 experience_severity: int) -> int:
        return budget_severity + route_severity + weather_severity + experience_severity

    def _clamp_score(self, score: int) -> int:
        return max(0, min(100, score))


def _is_risky_weather(day_weather: str | None) -> bool:
    return day_weather is not None and any(keyword in day_weather for keyword in _RISKY_WEATHER_KEYWORDS)
