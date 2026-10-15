"""高德天气预报：按日期与规则天气逐日合并，高德缺哪天就补哪天的规则值。"""

import logging

from ..config.settings import AmapSettings
from ..exceptions import TripPlanningException
from ..models.provider_metadata import ProviderMetadata
from ..models.weather_info import WeatherInfo
from ..models.weather_query import WeatherQuery
from .amap_http import build_candidate_urls, fetch_json
from .base import WeatherClient
from .rule_weather import RuleBasedWeatherClient

log = logging.getLogger(__name__)


class AmapWeatherClient(WeatherClient):

    def __init__(self, amap: AmapSettings, fallback_client: RuleBasedWeatherClient):
        self._amap = amap
        self._fallback_client = fallback_client

    def get_weather(self, query: WeatherQuery) -> list[WeatherInfo]:
        if self._amap.mock_response:
            return self._fallback_client.get_weather(query)
        self._validate_amap_configuration()

        fallback_weather = self._fallback_client.get_weather(query)
        try:
            adcode = self._resolve_adcode(query.city)
            response = self._fetch_weather_response(adcode)
            real_weather_by_date = self._extract_weather_by_date(response)
            if not real_weather_by_date:
                log.warning("高德天气未返回有效预报，回退到规则天气: city=%s", query.city)
                return fallback_weather
            return self._merge_weather(fallback_weather, real_weather_by_date)
        except Exception as exception:  # noqa: BLE001 - 高德请求失败就退规则天气，不能让它打断规划
            log.warning("高德天气请求失败，回退到规则天气: city=%s, reason=%s", query.city, exception)
            return fallback_weather

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider_code="amap-weather-service",
            provider_name="高德地图天气服务",
            mock=self._amap.mock_response,
        )

    def _validate_amap_configuration(self) -> None:
        if self._amap.api_key is None or not self._amap.api_key.strip():
            raise TripPlanningException("已选择高德天气 provider，但尚未配置 AMAP_API_KEY")

    def _merge_weather(self, fallback_weather: list[WeatherInfo], real_weather_by_date: dict[str, WeatherInfo]) -> list[WeatherInfo]:
        merged: list[WeatherInfo] = []
        for fallback_item in fallback_weather:
            real_item = real_weather_by_date.get(fallback_item.date)
            if real_item is not None:
                merged.append(real_item)
            else:
                fallback_item.source = "rule-fallback"
                fallback_item.source_note = "高德未返回该日期天气，已使用规则天气补全"
                merged.append(fallback_item)
        return merged

    def _extract_weather_by_date(self, response: dict) -> dict[str, WeatherInfo]:
        real_weather_by_date: dict[str, WeatherInfo] = {}
        forecasts = response.get("forecasts")
        if not forecasts:
            return real_weather_by_date

        first_forecast = forecasts[0]
        casts = first_forecast.get("casts")
        if casts is None:
            return real_weather_by_date

        for cast in casts:
            date = self._safe_string(cast.get("date"), None)
            if date is None or not date.strip():
                continue
            real_weather_by_date[date] = WeatherInfo(
                date=date,
                day_weather=self._safe_string(cast.get("dayweather"), "未知"),
                night_weather=self._safe_string(cast.get("nightweather"), "未知"),
                day_temp=self._parse_int(cast.get("daytemp")),
                night_temp=self._parse_int(cast.get("nighttemp")),
                wind_direction=self._build_wind_direction(cast),
                wind_power=self._build_wind_power(cast),
                source="amap-live",
                source_note="高德实时天气预报",
            )
        return real_weather_by_date

    def _build_wind_direction(self, cast: dict) -> str:
        day_wind = self._safe_string(cast.get("daywind"), "")
        night_wind = self._safe_string(cast.get("nightwind"), "")
        if day_wind.strip() and night_wind.strip() and day_wind != night_wind:
            return f"{day_wind}转{night_wind}"
        if day_wind.strip():
            return day_wind
        if night_wind.strip():
            return night_wind
        return "未知风向"

    def _build_wind_power(self, cast: dict) -> str:
        day_power = self._safe_string(cast.get("daypower"), "")
        night_power = self._safe_string(cast.get("nightpower"), "")
        if day_power.strip() and night_power.strip() and day_power != night_power:
            return f"{day_power}-{night_power}级"
        if day_power.strip():
            return f"{day_power}级"
        if night_power.strip():
            return f"{night_power}级"
        return "未知风力"

    def _resolve_adcode(self, city: str) -> str:
        response = self._fetch_json(self._build_geocode_candidate_urls(city), "高德城市编码解析")
        return self._extract_adcode(response, city)

    def _extract_adcode(self, response: dict, city: str) -> str:
        geocodes = response.get("geocodes")
        if not geocodes:
            raise TripPlanningException(f"高德未返回城市编码：{city}")
        adcode = self._safe_string(geocodes[0].get("adcode"), "")
        if not adcode.strip():
            raise TripPlanningException(f"高德返回的城市编码为空：{city}")
        return adcode

    def _fetch_weather_response(self, adcode: str) -> dict:
        return self._fetch_json(self._build_weather_candidate_urls(adcode), "高德天气查询")

    def _fetch_json(self, candidate_urls: list[str], action_name: str) -> dict:
        return fetch_json(
            self._amap,
            candidate_urls,
            log_scope=action_name,
            error_prefix=action_name,
            request_exception_prefix=f"{action_name}请求异常：",
            curl_exception_prefix=f"{action_name} curl fallback 未获取到有效响应：",
            catch_curl_errors=False,
        )

    def _build_geocode_candidate_urls(self, city: str) -> list[str]:
        return build_candidate_urls(self._amap, "/v3/geocode/geo", {
            "key": self._amap.api_key,
            "address": city,
            "city": city,
        })

    def _build_weather_candidate_urls(self, adcode: str) -> list[str]:
        return build_candidate_urls(self._amap, "/v3/weather/weatherInfo", {
            "key": self._amap.api_key,
            "city": adcode,
            "extensions": "all",
        })

    def _parse_int(self, value: object) -> int:
        if value is None:
            return 0
        return int(str(value))

    def _safe_string(self, value: object, default: str | None) -> str | None:
        if value is None:
            return default
        text = str(value).strip()
        return default if not text.strip() else text
