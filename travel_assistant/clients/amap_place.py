"""把一个地名换成一个坐标，供选酒店时圈定候选范围用（docs/ADJUST_AGENT_DESIGN.md 9.2）。

与 amap_attraction / amap_hotel 打的是同一个高德 `/v3/place/text`，区别在两点：

- 只取第一条 POI 的经纬度，不做类别过滤、不推算票价，也不产出景点或酒店对象；
- **不接兜底数据源**：规则/本地那两套给的是按城市准备的候选清单，不是用户写的那个地标的坐标，
  拿它们顶上来等于凭空造一个位置，会把整趟行程带到别处去。所以搜不到就返回 None，
  由上层按"用户没填位置"处理并留痕。
"""

import logging
from typing import Optional

from ..config.settings import AmapSettings
from ..exceptions import TripPlanningException
from ..models.location import Location
from .amap_http import build_candidate_urls, fetch_json

log = logging.getLogger(__name__)

_PATH = "/v3/place/text"


def resolve_anchor_location(amap: AmapSettings, city: Optional[str], keyword: Optional[str]) -> Optional[Location]:
    """地名 → 坐标。搜不到、没配 key、走 mock、请求失败都返回 None，不抛错。

    锚点是可选增强而不是必需数据，为它让整个行程规划失败是最糟的取舍：用户看到的应该是
    "你给的位置附近没找到酒店"，而不是一个 500。
    """
    if not keyword or not keyword.strip():
        return None
    if amap.mock_response or not amap.api_key or not amap.api_key.strip():
        return None

    try:
        response = _fetch(amap, city, keyword.strip())
    except TripPlanningException as exception:
        log.warning("住宿位置「%s」检索失败，按未填写处理: %s", keyword, exception)
        return None
    except Exception as exception:  # noqa: BLE001 - 可选增强，任何异常都不该打断规划
        log.warning("住宿位置「%s」检索异常，按未填写处理: %s", keyword, type(exception).__name__)
        return None

    for poi in response.get("pois") or []:
        location = _parse_location(poi.get("location"))
        if location is not None:
            return location
    return None


def _fetch(amap: AmapSettings, city: Optional[str], keyword: str) -> dict:
    candidate_urls = build_candidate_urls(amap, _PATH, {
        "key": amap.api_key,
        "keywords": keyword,
        "city": city or "",
        "citylimit": bool(city),
        "offset": amap.poi_page_size,
        "page": 1,
        "extensions": "base",
    })
    return fetch_json(
        amap,
        candidate_urls,
        log_scope="高德住宿位置检索",
        error_prefix="高德住宿位置检索",
        request_exception_prefix="高德住宿位置检索请求异常：",
        curl_exception_prefix="高德住宿位置检索 curl fallback 未获取到有效响应：",
        catch_curl_errors=True,
    )


def _parse_location(raw_location: object) -> Optional[Location]:
    text = str(raw_location or "").strip()
    if not text or "," not in text:
        return None
    parts = text.split(",")
    if len(parts) != 2:
        return None
    try:
        return Location(longitude=float(parts[0]), latitude=float(parts[1]))
    except ValueError:
        return None


__all__ = ["resolve_anchor_location"]
