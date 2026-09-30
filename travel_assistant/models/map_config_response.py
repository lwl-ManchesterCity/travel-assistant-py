from typing import Optional

from .base import CamelModel


class MapConfigResponse(CamelModel):
    """对应 Java model/MapConfigResponse.java（MapConfigController 的唯一响应体）。

    Java 里 enabled 是原始 boolean，其余是 String；jsApiKey 与 securityJsCode 由 controller
    从配置原样带出，前端拿它们加载高德 JS API，因此这两个值是唯一允许出现在 HTTP 响应体里的凭据，
    与 Java 行为一致（不进代码、不进日志、不进文档）。
    """

    enabled: bool = False
    js_api_key: Optional[str] = None
    security_js_code: Optional[str] = None
    provider: Optional[str] = None
    message: Optional[str] = None
