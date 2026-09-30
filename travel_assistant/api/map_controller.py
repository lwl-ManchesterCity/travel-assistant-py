"""对应 Java controller/MapConfigController.java 的 GET /api/map/config。

Java 用 @CrossOrigin(origins="*") 挂在该 controller 上，Python 侧由 main.py 的全局
CORSMiddleware 覆盖（见 MIGRATION_NOTES.md 第三节第 13 条）。
"""

from fastapi import APIRouter, Depends

from ..config.container import get_amap_settings
from ..config.settings import AmapSettings
from ..models.map_config_response import MapConfigResponse

router = APIRouter(prefix="/api/map")


@router.get("/config", response_model=MapConfigResponse)
def get_map_config(amap: AmapSettings = Depends(get_amap_settings)) -> MapConfigResponse:
    web_js_key = amap.web_js_key
    enabled = web_js_key is not None and web_js_key.strip() != ""
    return MapConfigResponse(
        enabled=enabled,
        js_api_key=web_js_key if enabled else "",
        security_js_code=amap.security_js_code,
        provider="amap-jsapi",
        message="高德地图前端配置已就绪" if enabled
        else "尚未配置 AMAP_WEB_JS_KEY，当前地图暂显示占位提示",
    )
