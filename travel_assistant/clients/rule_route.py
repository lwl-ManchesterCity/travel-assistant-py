"""对应 Java client/impl/RuleBasedRouteClient.java（规则路线兜底引擎）。"""

import math

from ..models.attraction import Attraction
from ..models.hotel import Hotel
from ..models.location import Location
from ..models.map_point import MapPoint
from ..models.provider_metadata import ProviderMetadata
from ..models.route_plan import RoutePlan
from ..models.route_query import RouteQuery
from ..models.route_step import RouteStep
from ..utils import jround, round1
from .base import RouteClient

_PROVIDER = ProviderMetadata(
    provider_code="rule-route-engine",
    provider_name="规则路线规划引擎",
    mock=True,
)


class RuleBasedRouteClient(RouteClient):
    def plan_route(self, query: RouteQuery) -> RoutePlan:
        hotel = query.hotel
        attractions = query.attractions or []
        steps: list[RouteStep] = []
        # 白天从第一个景点起算（用户是先到景点再开始玩的，不存在"早上从酒店发车"这一段），
        # 只有收工时那段回酒店保留——酒店锚在最后一站附近，就是为了这一段短。
        start_point = (self._build_attraction_point(attractions[0]) if attractions
                       else self._build_hotel_point(hotel))
        current_point = start_point
        total_distance_km = 0.0
        total_duration_minutes = 0

        for index, attraction in enumerate(attractions[1:], start=1):
            next_point = self._build_attraction_point(attraction)
            distance_km = self._estimate_distance(current_point.location, next_point.location)
            duration_minutes = self._estimate_duration(distance_km, query.transportation)
            total_distance_km += distance_km
            total_duration_minutes += duration_minutes
            steps.append(RouteStep(
                order_index=index,
                from_name=current_point.name,
                to_name=next_point.name,
                transportation_mode=query.transportation,
                distance_km=round1(distance_km),
                duration_minutes=duration_minutes,
                instruction=self._build_instruction(
                    current_point.name, next_point.name, query.transportation, distance_km
                ),
            ))
            current_point = next_point

        if attractions and hotel is not None and current_point.location is not None:
            back_distance_km = self._estimate_distance(current_point.location, hotel.location)
            back_duration_minutes = self._estimate_duration(back_distance_km, query.transportation)
            total_distance_km += back_distance_km
            total_duration_minutes += back_duration_minutes
            steps.append(RouteStep(
                order_index=len(steps) + 1,
                from_name=current_point.name,
                to_name=hotel.name,
                transportation_mode=query.transportation,
                distance_km=round1(back_distance_km),
                duration_minutes=back_duration_minutes,
                instruction=self._build_instruction(
                    current_point.name, hotel.name, query.transportation, back_distance_km
                ),
            ))

        end_point = self._build_hotel_point(hotel) if hotel is not None else current_point
        return RoutePlan(
            city=query.city,
            day_index=query.day_index,
            start_point=start_point,
            end_point=end_point,
            steps=steps,
            total_distance_km=round1(total_distance_km),
            total_duration_minutes=total_duration_minutes,
            transportation_mode=query.transportation,
            summary=self._build_summary(query, total_distance_km, total_duration_minutes, len(steps)),
            provider=_PROVIDER,
        )

    def get_metadata(self) -> ProviderMetadata:
        return _PROVIDER

    def _build_hotel_point(self, hotel: Hotel | None) -> MapPoint:
        if hotel is None:
            return MapPoint(name="市中心出发点", address="默认出发点", location=Location(longitude=0, latitude=0), point_type="hotel")
        return MapPoint(name=hotel.name, address=hotel.address, location=hotel.location, point_type="hotel")

    def _build_attraction_point(self, attraction: Attraction) -> MapPoint:
        return MapPoint(
            name=attraction.name,
            address=attraction.address,
            location=attraction.location,
            point_type="attraction",
        )

    def _build_instruction(self, from_name: str, to_name: str, transportation: str, distance_km: float) -> str:
        return f"从{from_name}前往{to_name}，建议使用{transportation}，约 {round1(distance_km):.1f} 公里。"

    def _build_summary(self, query: RouteQuery, total_distance_km: float, total_duration_minutes: int, step_count: int) -> str:
        return (
            f"第{query.day_index + 1}天路线已生成，共 {step_count} 段，"
            f"预计 {round1(total_distance_km):.1f} 公里，通勤约 {total_duration_minutes} 分钟。"
        )

    def _estimate_duration(self, distance_km: float, transportation: str | None) -> int:
        if transportation is not None and "步行" in transportation:
            speed_km_per_hour = 5
        elif transportation is not None and "打车" in transportation:
            speed_km_per_hour = 28
        else:
            speed_km_per_hour = 18
        return max(10, jround(distance_km / speed_km_per_hour * 60))

    def _estimate_distance(self, fr: Location | None, to: Location | None) -> float:
        if fr is None or to is None:
            return 3.0
        lat_diff = fr.latitude - to.latitude
        lng_diff = fr.longitude - to.longitude
        return math.sqrt(lat_diff * lat_diff + lng_diff * lng_diff) * 111
