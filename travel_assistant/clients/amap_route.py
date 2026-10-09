"""对应 Java client/impl/AmapRouteClient.java（高德路线分段规划，仅限流时回退规则路线）。"""

import logging

from ..config.settings import AmapSettings
from ..exceptions import TripPlanningException, is_quota_exhausted
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
        # 与规则路线同构：第一个景点起步，只在收工时回酒店。早上那段"酒店→首个景点"不计入，
        # 因为游客是自行抵达首站的；酒店锚在最后一站附近，压的就是最后那段回程。
        points: list[MapPoint] = []
        hotel = query.hotel
        attractions = query.attractions or []
        for attraction in attractions:
            if attraction.location is not None:
                points.append(MapPoint(
                    name=attraction.name,
                    address=attraction.address,
                    location=attraction.location,
                    point_type="attraction",
                ))
        if hotel is not None and hotel.location is not None and attractions:
            points.append(MapPoint(name=hotel.name, address=hotel.address, location=hotel.location, point_type="hotel"))
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
