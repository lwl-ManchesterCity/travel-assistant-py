"""规则天气源：按月份给一套写死的天气，兜底用（mock）。"""

from datetime import date, timedelta

from ..models.provider_metadata import ProviderMetadata
from ..models.weather_info import WeatherInfo
from ..models.weather_query import WeatherQuery
from .base import WeatherClient

_PROVIDER = ProviderMetadata(
    provider_code="rule-weather-engine",
    provider_name="规则天气引擎",
    mock=True,
)


class RuleBasedWeatherClient(WeatherClient):
    def get_weather(self, query: WeatherQuery) -> list[WeatherInfo]:
        start = date.fromisoformat(query.start_date)
        end = date.fromisoformat(query.end_date)
        days = (end - start).days + 1
        return [self._build_weather(start + timedelta(days=offset)) for offset in range(days)]

    def get_metadata(self) -> ProviderMetadata:
        return _PROVIDER

    def _build_weather(self, day: date) -> WeatherInfo:
        month = day.month
        if 6 <= month <= 9:
            return WeatherInfo(
                date=day.isoformat(),
                day_weather="晴到多云",
                night_weather="局部阵雨",
                day_temp=32,
                night_temp=25,
                wind_direction="东南风",
                wind_power="3级",
                source="rule-fallback",
                source_note="规则天气补全",
            )
        if month >= 12 or month <= 2:
            return WeatherInfo(
                date=day.isoformat(),
                day_weather="多云",
                night_weather="晴",
                day_temp=11,
                night_temp=3,
                wind_direction="北风",
                wind_power="2级",
                source="rule-fallback",
                source_note="规则天气补全",
            )
        return WeatherInfo(
            date=day.isoformat(),
            day_weather="多云到晴",
            night_weather="晴",
            day_temp=24,
            night_temp=16,
            wind_direction="东北风",
            wind_power="2级",
            source="rule-fallback",
            source_note="规则天气补全",
        )
