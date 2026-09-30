"""对应 Java evaluator/RouteEvaluator.java。"""

from ..models.planning_constraints import PlanningConstraints
from ..models.route_plan import RoutePlan
from ..models.weather_info import WeatherInfo

_DISTANCE_LIMIT_KM = 70


class RouteEvaluator:
    def determine_route_limit_minutes(self, constraints: PlanningConstraints | None, indoor_adjustment_recommended: bool) -> int:
        route_limit = 210
        if constraints is not None and constraints.relaxed_pace:
            route_limit = 150
        if indoor_adjustment_recommended:
            route_limit = min(route_limit, 135)
        return route_limit

    def is_route_too_long(self, route_plan: RoutePlan | None, constraints: PlanningConstraints | None, current_weather: WeatherInfo | None, indoor_adjustment_recommended: bool) -> bool:
        # current_weather 在 Java 里就是这个签名（判定只用 routeLimit 与里程阈值），保留形参不改变判定。
        if route_plan is None:
            return False
        route_limit = self.determine_route_limit_minutes(constraints, indoor_adjustment_recommended)
        return route_plan.total_duration_minutes > route_limit or route_plan.total_distance_km > _DISTANCE_LIMIT_KM

    def calculate_route_overflow_minutes(self, route_plan: RoutePlan | None, constraints: PlanningConstraints | None, indoor_adjustment_recommended: bool) -> int:
        if route_plan is None:
            return 0
        route_limit = self.determine_route_limit_minutes(constraints, indoor_adjustment_recommended)
        return max(0, route_plan.total_duration_minutes - route_limit)

    def calculate_route_overflow_distance_km(self, route_plan: RoutePlan | None) -> float:
        if route_plan is None:
            return 0
        return max(0, route_plan.total_distance_km - _DISTANCE_LIMIT_KM)
