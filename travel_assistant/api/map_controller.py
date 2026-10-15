"""GET /api/map/config：把高德前端地图的键交给浏览器。

CORS 不在这个路由上单独处理——main.py 注册了全局 CORSMiddleware，所有路由都覆盖
（见 docs/DESIGN_NOTES.md 第一节第 13 条）。
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
