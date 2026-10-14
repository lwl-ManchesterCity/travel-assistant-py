"""对应 Java controller/MapConfigController.java 的行为断言。

Java 侧没有 MapConfigControllerTest，期望值全部取自源码：enabled 的判定式
`webJsKey != null && !webJsKey.isBlank()`、`enabled ? webJsKey : ""`、provider 字面量 "amap-jsapi"、
以及两句 message 文案。静态挂载一项断言 Spring Boot 的 `src/main/resources/static/` 根路径等价行为。
"""

import pytest
from fastapi.testclient import TestClient

from travel_assistant.config.container import get_amap_settings, get_trip_planning_service
from travel_assistant.config.settings import AmapSettings
from travel_assistant.main import app


class _StubPlanningService:
    def plan_trip(self, request):
        raise AssertionError("静态挂载不应改变 /api/trips/plan 的路由归属")


def _override_js_key(web_js_key: str, security_js_code: str = ""):
    app.dependency_overrides[get_amap_settings] = lambda: AmapSettings(
        web_js_key=web_js_key, security_js_code=security_js_code
    )


@pytest.fixture
def client(without_login_gate):
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_should_return_enabled_config_when_web_js_key_is_present(client):
    _override_js_key("fake-web-js-key", "fake-security-js-code")

    body = client.get("/api/map/config").json()

    assert body == {
        "enabled": True,
        "jsApiKey": "fake-web-js-key",
        "securityJsCode": "fake-security-js-code",
        "provider": "amap-jsapi",
        "message": "高德地图前端配置已就绪",
    }


def test_should_disable_and_blank_key_when_web_js_key_is_absent(client):
    _override_js_key("", "fake-security-js-code")

    body = client.get("/api/map/config").json()

    assert body["enabled"] is False
    # Java：enabled ? webJsKey : "" —— 未启用时不吐 key
    assert body["jsApiKey"] == ""
    # Java：securityJsCode 无条件透传，不受 enabled 影响
    assert body["securityJsCode"] == "fake-security-js-code"
    assert body["provider"] == "amap-jsapi"
    assert body["message"] == "尚未配置 AMAP_WEB_JS_KEY，当前地图暂显示占位提示"


def test_should_disable_when_web_js_key_is_only_whitespace(client):
    # Java 用的是 String.isBlank()，纯空白同样判为未配置
    _override_js_key("   ")

    body = client.get("/api/map/config").json()

    assert body["enabled"] is False
    assert body["jsApiKey"] == ""


def test_should_serve_index_html_from_root_and_keep_api_routes_ahead_of_static(client):
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "智能旅行助手" in response.text
    # P0 拆分后接口路径串只存在于外联 JS，改对 /js/app.js 的响应体断言（顺带验证静态挂载）
    assert "/js/app.js" in response.text

    js_response = client.get("/js/app.js")
    assert js_response.status_code == 200
    assert "/api/trips/plan" in js_response.text
    assert "/api/trips/adjust" in js_response.text
    assert "/api/map/config" in js_response.text

    # 挂载 "/" 之后 /api/ 仍归路由管：校验失败要拿回 Java 形状的错误体，而不是 index.html
    app.dependency_overrides[get_trip_planning_service] = _StubPlanningService
    invalid = client.post("/api/trips/plan", json={"city": ""})
    assert invalid.status_code == 400
    assert invalid.json()["code"] == "VALIDATION_ERROR"

    assert client.get("/api/map/config").status_code == 200
