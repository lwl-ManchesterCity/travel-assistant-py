"""对应 Java client/impl/AmapHotelClient.java（高德酒店检索，异常与空结果都回退规则酒店）。"""

import logging

from ..config.settings import AmapSettings
from ..exceptions import TripPlanningException
from ..models.hotel import Hotel
from ..models.hotel_query import HotelQuery
from ..models.location import Location
from ..models.provider_metadata import ProviderMetadata
from ..utils import jround
from .amap_http import build_candidate_urls, fetch_json
from .base import HotelClient
from .rule_hotel import RuleBasedHotelClient

log = logging.getLogger(__name__)

_PATH = "/v3/place/text"
_AROUND_PATH = "/v3/place/around"


class AmapHotelClient(HotelClient):

    def __init__(self, amap: AmapSettings, fallback_client: RuleBasedHotelClient):
        self._amap = amap
        self._fallback_client = fallback_client

    def recommend_hotels(self, query: HotelQuery) -> list[Hotel]:
        if self._amap.mock_response:
            return self._fallback_client.recommend_hotels(query)
        self._validate_amap_configuration()

        try:
            hotels = self._request_hotels(query)
            if not hotels:
                log.warning("高德酒店检索未返回有效结果，回退到规则酒店: city=%s", query.city)
                return self._fallback_client.recommend_hotels(query)
            return hotels
        except Exception as exception:  # noqa: BLE001 - 与 Java 的 catch (Exception) 一致
            log.warning("高德酒店检索失败，回退到规则酒店: city=%s, reason=%s", query.city, exception)
            return self._fallback_client.recommend_hotels(query)

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider_code="amap-hotel-search",
            provider_name="高德地图酒店检索",
            mock=self._amap.mock_response,
        )

    def search_near_location(self, query: HotelQuery, location: Location, radius_km: float) -> list[Hotel]:
        """以锚点为圆心做一次周边酒店检索（`/v3/place/around`，按距离排序）。

        必须单独发这一趟：默认的 `/v3/place/text` 是按关键词相关性返回 10 家，实测
        "舒适型酒店 精品酒店"在杭州最近的离西湖 6.1 公里——全城 10 家里挑，再怎么画圈也
        圈不到湖边的店。周边检索才是"附近的酒店"这件事的对应能力。
        失败或没结果时返回空列表，绝不回退到规则酒店：规则酒店没有坐标，顶上来会伪装成"附近找到了"。
        """
        if self._amap.mock_response:
            return []
        try:
            response = self._fetch_around_response(query, location, radius_km)
        except Exception as exception:  # noqa: BLE001 - 锚点是增强，不能因为它打断规划
            log.warning("锚点周边酒店检索失败: reason=%s", type(exception).__name__)
            return []
        pois = response.get("pois")
        if pois is None:
            return []
        hotels = [
            self._to_hotel(poi, query)
            for poi in pois
            if self._is_hotel_in_requested_city(poi, query.city)
        ]
        return [hotel for hotel in hotels if hotel.location is not None]

    def _fetch_around_response(self, query: HotelQuery, location: Location, radius_km: float) -> dict:
        self._validate_amap_configuration()
        candidate_urls = build_candidate_urls(self._amap, _AROUND_PATH, {
            "key": self._amap.api_key,
            "keywords": "酒店",
            "location": f"{location.longitude},{location.latitude}",
            "radius": int(round(radius_km * 1000)),
            "offset": self._amap.poi_page_size,
            "page": 1,
            "extensions": "all",
            "sortrule": "distance",
        })
        return fetch_json(
            self._amap,
            candidate_urls,
            log_scope="高德周边酒店检索",
            error_prefix="高德周边酒店检索",
            request_exception_prefix="高德周边酒店检索请求异常：",
            curl_exception_prefix="高德周边酒店检索 curl fallback 未获取到有效响应：",
            catch_curl_errors=True,
        )

    def _validate_amap_configuration(self) -> None:
        if self._amap.api_key is None or not self._amap.api_key.strip():
            raise TripPlanningException("已选择高德酒店 provider，但尚未配置 travel.providers.amap.api-key")

    def _request_hotels(self, query: HotelQuery) -> list[Hotel]:
        response = self._fetch_hotel_response(query)
        pois = response.get("pois")
        if pois is None:
            return []
        hotels = [
            self._to_hotel(poi, query)
            for poi in pois
            if self._is_hotel_in_requested_city(poi, query.city)
        ]
        hotels = [hotel for hotel in hotels if hotel.location is not None]
        return sorted(hotels, key=lambda hotel: hotel.estimated_cost)

    def _is_hotel_in_requested_city(self, poi: dict, requested_city: str | None) -> bool:
        normalized_requested_city = self._normalize_city_name(requested_city)
        if not normalized_requested_city.strip():
            return True
        for key in ("pname", "cityname", "adname", "address"):
            if normalized_requested_city in self._normalize_city_name(self._safe_string(poi.get(key), "")):
                return True
        return False

    def _normalize_city_name(self, city: str | None) -> str:
        if city is None:
            return ""
        for suffix in ("省", "市", "壮族自治区", "回族自治区", "维吾尔自治区", "自治区", "特别行政区"):
            city = city.replace(suffix, "")
        return city.strip()

    def _fetch_hotel_response(self, query: HotelQuery) -> dict:
        candidate_urls = build_candidate_urls(self._amap, _PATH, {
            "key": self._amap.api_key,
            "keywords": self._build_hotel_keyword(query),
            "city": query.city,
            "citylimit": self._amap.city_limit,
            "offset": self._amap.poi_page_size,
            "page": 1,
            "extensions": "all",
        })
        return fetch_json(
            self._amap,
            candidate_urls,
            log_scope="高德酒店检索",
            error_prefix="高德酒店检索",
            request_exception_prefix="高德酒店检索请求异常：",
            curl_exception_prefix="高德酒店检索 curl fallback 未获取到有效响应：",
            catch_curl_errors=False,
        )

    def _build_hotel_keyword(self, query: HotelQuery) -> str:
        accommodation = query.accommodation or ""
        normalized = accommodation.lower()
        budget_per_day = max(1, query.budget // max(1, query.days))
        if budget_per_day <= 450:
            return "经济型酒店 连锁酒店"
        if budget_per_day <= 700 and ("豪华" in normalized or "高端" in normalized or "轻奢" in normalized):
            return "舒适型酒店 精品酒店"
        if "经济" in normalized or "便捷" in normalized:
            return "经济型酒店 连锁酒店"
        if "舒适" in normalized or "精选" in normalized:
            return "舒适型酒店 精品酒店"
        if "豪华" in normalized or "高端" in normalized or "轻奢" in normalized:
            return "豪华酒店 高端酒店"
        if "民宿" in normalized or "客栈" in normalized:
            return "民宿 客栈"
        return "酒店"

    def _to_hotel(self, poi: dict, query: HotelQuery) -> Hotel:
        estimated_cost = self._extract_estimated_cost(poi, query)
        return Hotel(
            name=self._safe_string(poi.get("name"), "未知酒店"),
            address=self._build_address(poi),
            location=self._parse_location(self._safe_string(poi.get("location"), "")),
            price_range=self._build_price_range(estimated_cost),
            rating=self._extract_rating(poi),
            distance=self._build_distance_label(poi),
            type=query.accommodation,
            estimated_cost=estimated_cost,
        )

    def _extract_rating(self, poi: dict) -> str:
        """高德没给评分就返回空串，**不编一个默认分**。

        这个值会进"选哪家酒店"的打分（`planner_agent._parse_hotel_rating_score`
        对空值另有处理）。以前这里填的是写死的 "4.3"，于是"没有评分"的酒店
        反而赢过真评分 4.0 的那家——编出来的数字直接进了决策。
        """
        biz_ext = poi.get("biz_ext")
        if isinstance(biz_ext, dict):
            rating = self._safe_string(biz_ext.get("rating"), "")
            if rating.strip():
                return rating
        return ""

    def _extract_estimated_cost(self, poi: dict, query: HotelQuery) -> int:
        biz_ext = poi.get("biz_ext")
        if isinstance(biz_ext, dict):
            cost = self._safe_string(biz_ext.get("cost"), "")
            if cost.strip():
                try:
                    return self._normalize_hotel_cost(jround(float(cost)), query)
                except ValueError:
                    pass
        return self._infer_estimated_cost(query)

    def _infer_estimated_cost(self, query: HotelQuery) -> int:
        budget_per_day = max(1, query.budget // max(1, query.days))
        accommodation = query.accommodation or ""
        if budget_per_day <= 450:
            return min(320, max(220, budget_per_day - 20))
        if "经济" in accommodation:
            return min(380, budget_per_day)
        if "舒适" in accommodation:
            return min(680, max(320, budget_per_day))
        if "豪华" in accommodation or "高端" in accommodation:
            return min(1280, max(520, budget_per_day))
        return min(580, max(360, budget_per_day))

    def _normalize_hotel_cost(self, raw_cost: int, query: HotelQuery) -> int:
        inferred_cost = self._infer_estimated_cost(query)
        budget_per_day = max(1, query.budget // max(1, query.days))
        if budget_per_day <= 450:
            return min(raw_cost, inferred_cost)
        return max(220, min(raw_cost, max(inferred_cost, budget_per_day + 120)))

    def _build_price_range(self, estimated_cost: int) -> str:
        low = max(estimated_cost - 120, 0)
        high = estimated_cost + 180
        return f"{low}-{high}元"

    def _build_distance_label(self, poi: dict) -> str:
        adname = self._safe_string(poi.get("adname"), "")
        if adname.strip():
            return f"位于{adname}区域"
        return "位于城市核心片区"

    def _build_address(self, poi: dict) -> str:
        joined = "".join([
            self._safe_string(poi.get("pname"), ""),
            self._safe_string(poi.get("cityname"), ""),
            self._safe_string(poi.get("adname"), ""),
            self._safe_string(poi.get("address"), ""),
        ])
        return "暂无详细地址" if not joined.strip() else joined

    def _parse_location(self, raw_location: str) -> Location | None:
        if raw_location is None or not raw_location.strip() or "," not in raw_location:
            return None
        parts = raw_location.split(",")
        if len(parts) != 2:
            return None
        try:
            return Location(longitude=float(parts[0]), latitude=float(parts[1]))
        except ValueError:
            return None

    def _safe_string(self, value: object, default: str) -> str:
        if value is None:
            return default
        text = str(value).strip()
        return default if not text.strip() else text
