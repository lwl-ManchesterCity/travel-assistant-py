"""对应 Java evaluator/WeatherEvaluator.java。"""

from ..models.planning_constraints import PlanningConstraints
from ..models.weather_info import WeatherInfo


class WeatherEvaluator:
    def should_prefer_indoor(self, constraints: PlanningConstraints | None, current_weather: WeatherInfo | None) -> bool:
        if constraints is None or not constraints.prefers_indoor_backup:
            return False
        if current_weather is None or current_weather.day_weather is None:
            return False
        weather = current_weather.day_weather
        return "雨" in weather or "雪" in weather or "雷" in weather
