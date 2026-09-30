"""对应 Java agent/WeatherAgent.java。"""

from ..models.trip_plan_request import TripPlanRequest
from ..models.weather_info import WeatherInfo
from ..models.weather_query import WeatherQuery
from ..models.weather_query_result import WeatherQueryResult


class WeatherAgent:

    def __init__(self, weather_client):
        self._weather_client = weather_client

    def query(self, request: TripPlanRequest) -> WeatherQueryResult:
        weather_query = WeatherQuery(city=request.city, start_date=request.start_date, end_date=request.end_date)
        weather_info = self._weather_client.get_weather(weather_query)
        return WeatherQueryResult(
            city=request.city,
            start_date=request.start_date,
            end_date=request.end_date,
            weather_info=weather_info,
            summary=self._build_summary(weather_info),
            provider=self._weather_client.get_metadata(),
        )

    def _build_summary(self, weather_info: list[WeatherInfo]) -> str:
        if not weather_info:
            return "未获取到天气信息"
        first_day = weather_info[0]
        return f"已获取 {len(weather_info)} 天天气，首日为 {first_day.day_weather}，白天 {first_day.day_temp}°C"
