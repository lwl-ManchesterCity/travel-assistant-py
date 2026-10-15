from typing import Optional

from .base import CamelModel


class MapConfigResponse(CamelModel):
    """GET /api/map/config 的响应体。

    enabled 是 bool，其余是字符串；jsApiKey 与 securityJsCode 由 controller 从配置原样带出，
    前端拿它们加载高德 JS API，因此这两个值是唯一允许出现在 HTTP 响应体里的凭据
    （不进代码、不进日志、不进文档）。
    """

    enabled: bool = False
    js_api_key: Optional[str] = None
    security_js_code: Optional[str] = None
    provider: Optional[str] = None
    message: Optional[str] = None
