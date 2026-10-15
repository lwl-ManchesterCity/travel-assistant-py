"""启动时打印一份运行时配置概览。

只打印 provider 名、base-url 和开关，不打印任何 api-key。
"""

import logging
import platform

from .settings import Settings, get_settings

log = logging.getLogger(__name__)


def log_travel_runtime(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    amap = settings.providers.amap
    log.info(
        "Travel runtime config -> python.version=%s, attraction.provider=%s, weather.provider=%s, "
        "hotel.provider=%s, route.provider=%s, amap.baseUrl=%s, amap.mockResponse=%s, "
        "amap.curlFallbackEnabled=%s, amap.webJsEnabled=%s",
        platform.python_version(),
        settings.providers.attraction,
        settings.providers.weather,
        settings.providers.hotel,
        settings.providers.route,
        amap.base_url,
        amap.mock_response,
        amap.curl_fallback_enabled,
        bool(amap.web_js_key and amap.web_js_key.strip()),
    )
