"""高德 POI 景点检索：按偏好拼关键词搜景点，搜不到回退本地景点库。"""

import logging

from ..config.settings import AmapSettings
from ..exceptions import TripPlanningException
from ..models.attraction import Attraction
from ..models.attraction_query import AttractionQuery
from ..models.location import Location
from ..models.provider_metadata import ProviderMetadata
from .amap_http import build_candidate_urls, fetch_json
from .amap_opentime import ALL_DAY, is_all_day, parse_opentime2
from .base import AttractionClient
from .local_attraction import InMemoryAttractionClient

log = logging.getLogger(__name__)

_PATH = "/v3/place/text"


class AmapAttractionClient(AttractionClient):

    def __init__(self, amap: AmapSettings, fallback_client: InMemoryAttractionClient):
        self._amap = amap
        self._fallback_client = fallback_client

    def find_attractions(self, query: AttractionQuery) -> list[Attraction]:
        if self._amap.mock_response:
            return self._fallback_client.find_attractions(query)
        self._validate_amap_configuration()
        attractions = self._request_attractions(query)
        if not attractions:
            log.warning("高德 POI 未返回有效景点，回退到本地景点库: city=%s", query.city)
            return self._fallback_client.find_attractions(query)
        return attractions

    def find_attraction_by_name(self, city: str, name: str) -> Attraction | None:
        """按名字精确查一个景点（"某天必去某景点"补搜用）：优先同名，多个取评分最高。"""
        if not name or not name.strip():
            return None
        name = name.strip()
        if self._amap.mock_response:
            return self._fallback_client.find_attraction_by_name(city, name)
        try:
            response = self._fetch_poi_response(city, name)
        except TripPlanningException:
            return None
        pois = response.get("pois") or []
        candidates = [
            attraction
            for attraction in (self._to_attraction(poi) for poi in pois if self._is_valid_attraction_poi(poi))
            if attraction.location is not None and attraction.name
        ]
        if not candidates:
            return None
        exact = [a for a in candidates if a.name == name] or candidates
        return max(exact, key=lambda a: a.rating if a.rating is not None else 0.0)

    def get_metadata(self) -> ProviderMetadata:
        return ProviderMetadata(
            provider_code="amap-poi-search",
            provider_name="高德地图 POI 搜索",
            mock=self._amap.mock_response,
        )

    def _validate_amap_configuration(self) -> None:
        if self._amap.api_key is None or not self._amap.api_key.strip():
            raise TripPlanningException("已选择高德景点 provider，但尚未配置 AMAP_API_KEY")

    def _request_attractions(self, query: AttractionQuery) -> list[Attraction]:
        pois: list[dict] = []
        for keyword in self._build_keywords(query):
            response = self._fetch_poi_response(query.city, keyword)
            poi_items = response.get("pois")
            if poi_items is None:
                continue
            for poi in poi_items:
                if not self._contains_poi_by_id(pois, str(poi.get("id"))):
                    pois.append(poi)

        return [
            attraction
            for attraction in (self._to_attraction(poi) for poi in pois if self._is_valid_attraction_poi(poi))
            if attraction.location is not None
        ]

    def _build_keywords(self, query: AttractionQuery) -> list[str]:
        keywords: list[str] = []
        for preference in query.preferences or []:
            keyword = self._map_preference_to_keyword(preference)
            if keyword not in keywords:
                keywords.append(keyword)
        if not keywords:
            return ["景点"]
        return keywords

    def _map_preference_to_keyword(self, preference: str | None) -> str:
        normalized = (preference or "").lower()
        if "自然" in normalized:
            return "景点 公园"
        if "历史" in normalized or "人文" in normalized:
            return "博物馆 古迹"
        if "美食" in normalized:
            return "美食街 步行街 夜市"
        if "休闲" in normalized or "轻松" in normalized:
            return "公园 商圈"
        if "艺术" in normalized:
            return "美术馆 艺术中心"
        return "景点"

    def _fetch_poi_response(self, city: str, keyword: str) -> dict:
        candidate_urls = build_candidate_urls(self._amap, _PATH, {
            "key": self._amap.api_key,
            "keywords": keyword,
            "city": city,
            "citylimit": self._amap.city_limit,
            "offset": self._amap.poi_page_size,
            "page": 1,
            "extensions": "all",
        })
        return fetch_json(
            self._amap,
            candidate_urls,
            log_scope="高德 POI",
            error_prefix="高德景点检索",
            request_exception_prefix="高德景点检索请求异常：",
            curl_exception_prefix="curl fallback 未获取到有效 POI 响应：",
            catch_curl_errors=True,
        )

    def _to_attraction(self, poi: dict) -> Attraction:
        name = self._safe_string(poi.get("name"), "未知景点")
        address = self._build_address(poi)
        location = self._parse_location(self._safe_string(poi.get("location"), ""))
        type_ = self._safe_string(poi.get("type"), "城市景点")
        category = self._map_type_to_category(type_)
        hours, closed_days = self._parse_opentime(poi)
        return Attraction(
            # POI id 一直就在手上（`_contains_poi_by_id` 拿它去重），只是以前没往上传。
            id=self._safe_string(poi.get("id"), None),
            name=name,
            address=address,
            location=location,
            visit_duration=self._infer_visit_duration(category),
            description=self._build_description(name, type_, address),
            category=category,
            rating=self._parse_rating(poi),
            image_url=self._parse_photo_url(poi),
            ticket_price=self._infer_ticket_price(category),
            hours=hours,
            closed_days=closed_days,
        )

    def _is_valid_attraction_poi(self, poi: dict) -> bool:
        type_ = self._safe_string(poi.get("type"), "")
        name = self._safe_string(poi.get("name"), "")

        for banned in (
            "公司企业",
            "餐饮服务",
            "金融保险服务",
            "汽车服务",
            "汽车维修",
            "汽车销售",
            "房地产",
            "商务住宅",
            "医疗保健服务",
            "政府机构及社会团体",
        ):
            if banned in type_:
                return False

        for banned in ("有限公司", "公司", "餐饮", "奶茶", "火锅", "烧烤"):
            if banned in name:
                return False
        return True

    def _parse_photo_url(self, poi: dict) -> str | None:
        photos = poi.get("photos")
        if not isinstance(photos, list) or not photos:
            return None
        for photo in photos:
            if isinstance(photo, dict):
                url = photo.get("url")
                if url is not None and str(url).strip():
                    return str(url)
        return None

    def _contains_poi_by_id(self, pois: list[dict], poi_id: str) -> bool:
        for poi in pois:
            if str(poi.get("id")) == poi_id:
                return True
        return False

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

    def _map_type_to_category(self, type_: str) -> str:
        normalized = type_.lower()
        if "风景" in normalized or "公园" in normalized or "景点" in normalized:
            return "自然风景"
        if "博物馆" in normalized or "古迹" in normalized or "寺庙" in normalized or "名胜" in normalized:
            return "人文历史"
        if "餐饮" in normalized or "购物" in normalized or "商场" in normalized:
            return "美食购物"
        return "城市漫游"

    def _infer_visit_duration(self, category: str) -> int:
        if category == "自然风景":
            return 180
        if category == "人文历史":
            return 150
        if category == "美食购物":
            return 90
        return 120

    def _infer_ticket_price(self, category: str) -> int:
        if category == "自然风景":
            return 60
        if category == "人文历史":
            return 50
        if category == "美食购物":
            return 0
        return 30

    def _build_description(self, name: str, type_: str, address: str) -> str:
        return f"{name}，适合安排在{address}周边游览，POI 类型为 {type_}。"

    def _parse_rating(self, poi: dict) -> float | None:
        """高德没给评分就返回 None，**不编一个默认分**。

        与酒店那次（`amap_hotel._extract_rating`）同理：这个值会进"选哪个景点"的打分
        （`planner_agent._score_attraction` 里 `if attraction.rating is not None` 才加分），
        编出来的数会跟真评分混在一起、页面上也分不出来。
        以前这里填的是写死的 4.5，于是"没有评分"的景点反而跟真有 4.5 的平起平坐。
        """
        biz_ext = poi.get("biz_ext")
        if isinstance(biz_ext, dict):
            rating = biz_ext.get("rating")
            if rating is not None and str(rating).strip():
                try:
                    return float(str(rating))
                except ValueError:
                    return None
        return None

    def _parse_opentime(self, poi: dict) -> tuple[str | None, list[str] | None]:
        """高德 POI → `(hours, closed_days)`，拿不到就是 `(None, None)`——不编默认值。

        形状与 `_parse_rating` 一致。`opentime2` 一个字段里同时含开放时段与闭馆日
        （覆盖率实测 ~66%，见 docs/ASK_LIVE_HOURS.md §2.1），所以两格都从它出。

        **空的时候给 `None` 而不是 `[]`**：`[]` 在 JSON 里会被读成"确认没有闭馆日"，
        而 `(None, [])` 实际的意思是"高德没给"。两种含义不该长得一样。
        """
        biz_ext = poi.get("biz_ext")
        if not isinstance(biz_ext, dict):
            return None, None
        hours, closed_days = parse_opentime2(biz_ext.get("opentime2"))
        if hours is None:
            # `open_time` 是简版时段（实测覆盖 ~55%），`opentime2` 缺失时的兜底。
            # 与 normalize.py 那条离线链同一条口径：简版里没有闭馆日，所以只取 hours。
            hours, _ = parse_opentime2(biz_ext.get("open_time"))
        if hours is None and (is_all_day(biz_ext.get("opentime2")) or is_all_day(biz_ext.get("open_time"))):
            # **「全天开放」是答案，不是空值。** `parse_opentime2` 会把它当占位跳过，
            # 于是"高德说了、而且是全天"变成一句「未给出」——白云山那次就栽在这。
            # 只有在真没取到时段时才用它：opentime2 里同时有 00:00-24:00 与具体时段时，
            # 具体时段才是更准的那个（parse_opentime2 已经优先取了它）。
            # 注意与离线那条链**有意分叉**：KB 的 hours 要的是结构化时段，追问侧要的是答案。
            hours = ALL_DAY
        return hours, closed_days or None

    def _safe_string(self, value: object, default: str | None) -> str | None:
        if value is None:
            return default
        text = str(value).strip()
        return default if not text.strip() or text == "[]" else text
