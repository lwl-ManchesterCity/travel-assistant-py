"""对应 Java client/impl/AmapRouteClient.java（高德路线分段规划，仅限流时回退规则路线）。"""

import logging
import re

from ..config.settings import AmapSettings
from ..exceptions import TripPlanningException, is_quota_exhausted
from ..models.intercity_plan import (
    MODE_HIGH_SPEED_RAIL,
    MODE_ORDINARY_RAIL,
    InterCityOption,
)
from ..models.location import Location
from ..models.map_point import MapPoint
from ..models.provider_metadata import ProviderMetadata
from ..models.route_plan import RoutePlan
from ..models.route_query import RouteQuery
from ..models.route_step import RouteStep
from ..utils import jround, round1
from .amap_http import build_candidate_urls, fetch_json
from .base import RouteClient
from .rule_route import RuleBasedRouteClient

log = logging.getLogger(__name__)

# 车次首字母：G/D/C/S 是高铁动车城际，T/K/Z 和纯数字是普速。
_HIGH_SPEED_PREFIX = re.compile(r"^[GDCS]")


def _parse_stop_location(raw: object) -> Location | None:
    """站点坐标。高德这里给的是空格分隔的 "113.246341 23.190910"，而别处用逗号分隔，两种都收。"""
    text = str(raw or "").strip()
    if not text:
        return None
    parts = re.split(r"[,\s]+", text)
    if len(parts) != 2:
        return None
    try:
        return Location(longitude=float(parts[0]), latitude=float(parts[1]))
    except ValueError:
        return None


class AmapRouteClient(RouteClient):

    def __init__(self, amap: AmapSettings, fallback_client: RuleBasedRouteClient):
        self._amap = amap
        self._fallback_client = fallback_client

    def plan_route(self, query: RouteQuery) -> RoutePlan:
        if self._amap.mock_response:
            route_plan = self._fallback_client.plan_route(query)
            route_plan.provider = self.get_metadata()
            route_plan.summary = route_plan.summary + " 当前为高德路线 provider 的模拟返回。"
            return route_plan
        self._validate_amap_configuration()
        try:
            return self._request_route_plan(query)
        except TripPlanningException as exception:
            if self._should_fallback_to_rule_route(exception):
                log.warning("高德路线触发限流或配额保护，回退到规则路线: %s", exception)
                route_plan = self._fallback_client.plan_route(query)
                route_plan.summary = route_plan.summary + " 高德路线当前受限，已自动切换为规则路线估算。"
                return route_plan
            raise

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider_code="amap-route-service",
            provider_name="高德地图路线规划",
            mock=self._amap.mock_response,
        )

    def plan_intercity(self,
                       origin_city: str,
                       origin: Location,
                       destination_city: str,
                       destination: Location,
                       straight_km: float) -> list[InterCityOption]:
        """跨城换乘方案：只取其中的铁路主段，按 G/D/C/S 字头分高铁、其余分普速。

        接口给的 `cost` 是"这一条方案全程花费"（含两端地铁），所以它是估算口径；
        `duration` 只留档不进计算——本版按"早上出发、中午前后到"的统一假设排景点。
        请求失败返回空列表，由上层按直线距离兜底，不让一次跨城查询打断整单规划。
        """
        if self._amap.mock_response:
            return []
        try:
            response = self._fetch_intercity(origin_city, origin, destination_city, destination)
        except TripPlanningException as exception:
            log.warning("高德跨城检索失败，改由直线距离估算大交通: %s", exception)
            return []
        except Exception as exception:  # noqa: BLE001 - 可选增强，异常不打断规划
            log.warning("高德跨城检索异常，改由直线距离估算大交通: %s", type(exception).__name__)
            return []

        options: list[InterCityOption] = []
        for transit in (response.get("route") or {}).get("transits") or []:
            option = self._to_intercity_option(transit)
            if option is not None:
                options.append(option)
        return options

    def _fetch_intercity(self,
                         origin_city: str,
                         origin: Location,
                         destination_city: str,
                         destination: Location) -> dict:
        # extensions=all 才带 arrival_stop/departure_stop：车次名里的"广州南"是这趟车的终点站，
        # 乘客真正下车的那一站要看 arrival_stop（实测 G251 名写"湛江北"、arrival_stop 是"广州白云"）。
        candidate_urls = build_candidate_urls(self._amap, "/v3/direction/transit/integrated", {
            "key": self._amap.api_key,
            "origin": self._format_location(origin),
            "destination": self._format_location(destination),
            "city1": origin_city,
            "city2": destination_city,
            "strategy": "0",
            "extensions": "all",
        })
        return fetch_json(
            self._amap,
            candidate_urls,
            log_scope="高德跨城",
            error_prefix="高德跨城检索",
            request_exception_prefix="高德跨城检索请求异常：",
            curl_exception_prefix="curl fallback 未获取到有效跨城响应：",
            catch_curl_errors=True,
        )

    def _to_intercity_option(self, transit: dict) -> InterCityOption | None:
        one_way_cost = self._parse_double(transit.get("cost"))
        railway = self._dominant_railway(transit)
        if one_way_cost <= 0 or railway is None:
            return None
        name = str(railway.get("name") or "").strip()
        trip = str(railway.get("trip") or name).strip()
        arrival = railway.get("arrival_stop") if isinstance(railway.get("arrival_stop"), dict) else {}
        return InterCityOption(
            mode=MODE_HIGH_SPEED_RAIL if _HIGH_SPEED_PREFIX.match(trip) else MODE_ORDINARY_RAIL,
            round_trip_cost=max(1, jround(one_way_cost * 2)),
            one_way_minutes=max(1, jround(self._parse_double(railway.get("time") or transit.get("duration")) / 60.0)),
            arrival_station=str(arrival.get("name") or "").strip() or None,
            arrival_location=_parse_stop_location(arrival.get("location")),
        )

    def _dominant_railway(self, transit: dict) -> dict | None:
        """取这条方案里唯一那段铁路的完整对象。

        只有一趟铁路才认：两头地铁加一趟车是跨城的常态；出现两段以上铁路说明这不是"一趟车到"，
        价格与落地站都不可信，宁可交给直线估。
        """
        found: list[dict] = []
        for segment in transit.get("segments") or []:
            railway = segment.get("railway")
            if isinstance(railway, dict) and str(railway.get("name") or "").strip():
                found.append(railway)
        return found[0] if len(found) == 1 else None

    def _validate_amap_configuration(self) -> None:
        if self._amap.api_key is None or not self._amap.api_key.strip():
            raise TripPlanningException("已选择高德路线 provider，但尚未配置 travel.providers.amap.api-key")

    def _request_route_plan(self, query: RouteQuery) -> RoutePlan:
        # points 是"首个景点→…→末个景点→酒店"这条链路，相邻两点逐段各请求一次高德
        points = self._build_route_points(query)
        if len(points) < 2:
            raise TripPlanningException("高德路线规划至少需要两个有效坐标点")

        steps: list[RouteStep] = []
        total_distance_km = 0.0
        total_duration_minutes = 0

        for index in range(len(points) - 1):
            from_point = points[index]
            to_point = points[index + 1]
            route_response = self._fetch_segment_route(query.transportation, from_point, to_point)
            summary = self._extract_route_summary(route_response, query.transportation)
            distance_km = float(summary["distance_km"])
            duration_minutes = int(summary["duration_minutes"])
            total_distance_km += distance_km
            total_duration_minutes += duration_minutes
            steps.append(RouteStep(
                order_index=index + 1,
                from_name=from_point.name,
                to_name=to_point.name,
                transportation_mode=query.transportation,
                distance_km=round1(distance_km),
                duration_minutes=duration_minutes,
                instruction=summary["instruction"],
            ))

        return RoutePlan(
            city=query.city,
            day_index=query.day_index,
            start_point=points[0],
            end_point=points[len(points) - 1],
            steps=steps,
            total_distance_km=round1(total_distance_km),
            total_duration_minutes=total_duration_minutes,
            transportation_mode=query.transportation,
            summary=(
                f"高德路线已生成，共 {len(steps)} 段，"
                f"预计 {round1(total_distance_km):.1f} 公里，通勤约 {total_duration_minutes} 分钟。"
            ),
            provider=self.get_metadata(),
        )

    def _build_route_points(self, query: RouteQuery) -> list[MapPoint]:
        """点位链：起点（前一晚酒店／到达口岸，没给就是第一个景点）→ 各景点 → 终点（当晚酒店／返程口岸）。

        早上那段"从当晚还没入住的酒店出发"不算——游客是自行抵达首站的；
        酒店锚在最后一站附近，压的正是最后那段回程。
        """
        attractions = [a for a in (query.attractions or []) if a.location is not None]
        points: list[MapPoint] = []
        start = query.start_point
        if start is not None and start.location is not None:
            points.append(start)
        for attraction in attractions:
            points.append(MapPoint(
                name=attraction.name,
                address=attraction.address,
                location=attraction.location,
                point_type="attraction",
            ))
        end = query.end_point
        if end is None and query.hotel is not None and query.hotel.location is not None and attractions:
            end = MapPoint(name=query.hotel.name, address=query.hotel.address,
                           location=query.hotel.location, point_type="hotel")
        if end is not None and end.location is not None and (not points or points[-1].name != end.name):
            points.append(end)
        return points

    def _extract_route_summary(self, response: dict, transportation: str) -> dict:
        route = response.get("route")
        paths = route.get("paths") if route is not None else None
        if not paths:
            raise TripPlanningException("高德路线规划返回为空，请稍后重试")
        first_path = paths[0]
        distance_meters = self._parse_double(first_path.get("distance"))
        duration_seconds = self._parse_double(first_path.get("duration"))
        instruction = self._extract_instruction(first_path, transportation)

        return {
            "distance_km": distance_meters / 1000.0,
            "duration_minutes": max(1, jround(duration_seconds / 60.0)),
            "instruction": instruction,
        }

    def _extract_instruction(self, first_path: dict, transportation: str) -> str:
        steps = first_path.get("steps")
        if steps and steps[0].get("instruction") is not None:
            return str(steps[0]["instruction"])
        return f"{transportation} 出行"

    def _fetch_segment_route(self, transportation: str, from_point: MapPoint, to_point: MapPoint) -> dict:
        candidate_urls = self._build_candidate_uris(transportation, from_point.location, to_point.location)
        return fetch_json(
            self._amap,
            candidate_urls,
            log_scope="高德",
            error_prefix="高德路线规划调用",
            request_exception_prefix="高德路线规划请求异常：",
            curl_exception_prefix="curl fallback 未获取到有效响应：",
            catch_curl_errors=True,
        )

    def _build_candidate_uris(self, transportation: str, origin: Location | None, destination: Location | None) -> list[str]:
        params = {
            "key": self._amap.api_key,
            "origin": self._format_location(origin),
            "destination": self._format_location(destination),
        }
        if not self._is_walking(transportation):
            params["strategy"] = self._amap.route_strategy
            params["extensions"] = self._amap.route_extensions
        path = "/v3/direction/walking" if self._is_walking(transportation) else "/v3/direction/driving"
        return build_candidate_urls(self._amap, path, params)

    def _is_walking(self, transportation: str | None) -> bool:
        return transportation is not None and "步行" in transportation

    def _should_fallback_to_rule_route(self, exception: TripPlanningException) -> bool:
        # 判定收敛到 exceptions.is_quota_exhausted：client 层的退避重试、这里的降级、
        # API 层的错误文案必须对「什么算限流」持同一套标准，否则会出现「重试了但文案说参数错」这种自相矛盾。
        return is_quota_exhausted(exception)

    def _format_location(self, location: Location | None) -> str:
        if location is None:
            raise TripPlanningException("路线规划缺少坐标信息")
        return f"{location.longitude:.6f},{location.latitude:.6f}"

    def _parse_double(self, value: object) -> float:
        if value is None:
            return 0
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
        return float(str(value))
