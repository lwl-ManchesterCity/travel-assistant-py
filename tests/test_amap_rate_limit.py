"""高德限流（infocode=10021 / CUQPS_HAS_EXCEEDED_THE_LIMIT）的处理。

背景：一次规划里景点、天气、酒店三路工具是被并行调用的，个人 key 的并发配额很容易瞬时打满。
Java 只给路线做了降级，另外三路撞限流会把整个请求打成 400；用户看到的是
「请调整输入条件后重试」——一句和真实原因无关的话，于是以为是表单没填全。

这里覆盖三处改动：判定收敛到 exceptions、client 层短退避重试、API 层错误文案分流。
"""

import asyncio
import json

import pytest

from travel_assistant.api import error_handlers
from travel_assistant.clients import amap_http
from travel_assistant.config.settings import AmapSettings
from travel_assistant.exceptions import TripPlanningException, is_quota_exhausted
from travel_assistant.main import app

QUOTA_MESSAGE = "高德景点检索失败：CUQPS_HAS_EXCEEDED_THE_LIMIT（infocode=10021）"
QUOTA_BODY = {"status": "0", "info": "CUQPS_HAS_EXCEEDED_THE_LIMIT", "infocode": "10021"}
OK_BODY = {"status": "1", "pois": []}


class _StubResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload
        self.status_code = 200
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _settings(**overrides) -> AmapSettings:
    base = {
        "api_key": "test-key",
        "base_url": "http://restapi.amap.com",
        "curl_fallback_enabled": False,  # 限流路径不会走到 curl，关掉省得真起子进程
    }
    return AmapSettings(**{**base, **overrides})


def _install_stub_http(monkeypatch, payloads: list[dict]) -> list[str]:
    """按顺序回放 payloads；返回被请求过的 URL 列表（用于数调用次数）。"""
    calls: list[str] = []

    def fake_get(url, **kwargs):
        calls.append(url)
        payload = payloads[min(len(calls) - 1, len(payloads) - 1)]
        return _StubResponse(payload)

    monkeypatch.setattr(amap_http.httpx, "get", fake_get)
    return calls


def _install_sleep_recorder(monkeypatch) -> list[float]:
    slept: list[float] = []
    monkeypatch.setattr(amap_http.time, "sleep", slept.append)
    return slept


def _fetch(amap: AmapSettings) -> dict:
    return amap_http.fetch_json(
        amap,
        ["http://restapi.amap.com/v3/place/text?keywords=x"],
        log_scope="高德 POI",
        error_prefix="高德景点检索",
        request_exception_prefix="高德景点检索请求异常：",
        curl_exception_prefix="curl fallback 未获取到有效 POI 响应：",
        catch_curl_errors=True,
    )


# --- 判定 -------------------------------------------------------------------

@pytest.mark.parametrize("message", [
    QUOTA_MESSAGE,
    "高德天气检索失败：ACCESS_TOO_FREQUENT（infocode=10004）",
    "高德酒店检索失败：USER_DAILY_QUERY_OVER_LIMIT（infocode=10044）",
])
def test_quota_messages_are_recognized(message):
    assert is_quota_exhausted(TripPlanningException(message)) is True


@pytest.mark.parametrize("message", [
    "高德景点检索失败：INVALID_USER_KEY（infocode=10001）",
    "高德景点检索失败：ENGINE_RESPONSE_DATA_ERROR（infocode=20800）",
    "",
])
def test_other_messages_are_not_quota(message):
    assert is_quota_exhausted(TripPlanningException(message)) is False


# --- 退避序列解析 -------------------------------------------------------------

def test_backoff_sequence_follows_configured_seconds():
    amap = _settings(rate_limit_retry_attempts=2, rate_limit_retry_backoff_seconds="0.8,2.0")
    assert amap.rate_limit_retry_backoffs() == (0.8, 2.0)


def test_backoff_pads_with_last_value_when_fewer_seconds_than_attempts():
    amap = _settings(rate_limit_retry_attempts=4, rate_limit_retry_backoff_seconds="0.5")
    assert amap.rate_limit_retry_backoffs() == (0.5, 0.5, 0.5, 0.5)


def test_zero_attempts_disables_retry():
    amap = _settings(rate_limit_retry_attempts=0, rate_limit_retry_backoff_seconds="0.8,2.0")
    assert amap.rate_limit_retry_backoffs() == ()


def test_unparsable_backoff_means_no_retry():
    amap = _settings(rate_limit_retry_attempts=2, rate_limit_retry_backoff_seconds="abc,")
    assert amap.rate_limit_retry_backoffs() == ()


# --- 重试行为 ---------------------------------------------------------------

def test_quota_failure_is_retried_then_succeeds(monkeypatch):
    amap = _settings(rate_limit_retry_attempts=2, rate_limit_retry_backoff_seconds="0.8,2.0")
    calls = _install_stub_http(monkeypatch, [QUOTA_BODY, OK_BODY])
    slept = _install_sleep_recorder(monkeypatch)

    assert _fetch(amap) == OK_BODY
    assert len(calls) == 2, "第一次撞限流后应重试一次"
    assert slept == [0.8], "只该睡配置里的第一个退避值"


def test_quota_failure_stops_after_configured_attempts(monkeypatch):
    amap = _settings(rate_limit_retry_attempts=2, rate_limit_retry_backoff_seconds="0.8,2.0")
    calls = _install_stub_http(monkeypatch, [QUOTA_BODY])
    slept = _install_sleep_recorder(monkeypatch)

    with pytest.raises(TripPlanningException) as caught:
        _fetch(amap)

    assert "10021" in str(caught.value)
    assert len(calls) == 3, "1 次原始请求 + 2 次重试"
    assert slept == [0.8, 2.0]


def test_non_quota_failure_is_not_retried(monkeypatch):
    """参数错、key 错这类重试多少次都一样，多睡 2.8 秒只会拖慢报错。"""
    amap = _settings(rate_limit_retry_attempts=2, rate_limit_retry_backoff_seconds="0.8,2.0")
    calls = _install_stub_http(monkeypatch, [{"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"}])
    slept = _install_sleep_recorder(monkeypatch)

    with pytest.raises(TripPlanningException):
        _fetch(amap)

    assert len(calls) == 1
    assert slept == []


def test_zero_attempts_keeps_java_behaviour(monkeypatch):
    amap = _settings(rate_limit_retry_attempts=0)
    calls = _install_stub_http(monkeypatch, [QUOTA_BODY])
    slept = _install_sleep_recorder(monkeypatch)

    with pytest.raises(TripPlanningException):
        _fetch(amap)

    assert len(calls) == 1
    assert slept == []


def test_retry_logs_a_warning(monkeypatch, caplog):
    amap = _settings(rate_limit_retry_attempts=1, rate_limit_retry_backoff_seconds="0.8")
    _install_stub_http(monkeypatch, [QUOTA_BODY, OK_BODY])
    _install_sleep_recorder(monkeypatch)

    with caplog.at_level("WARNING", logger="travel_assistant.clients.amap_http"):
        _fetch(amap)

    assert any("配额" in record.message for record in caplog.records)


# --- API 层错误文案 ----------------------------------------------------------

def _detail_for(message: str) -> tuple[int, str, str]:
    handler = app.exception_handlers[TripPlanningException]
    response = asyncio.run(handler(None, TripPlanningException(message)))
    body = json.loads(response.body)
    return response.status_code, body["message"], body["details"][0]


def test_quota_failure_no_longer_blames_user_input():
    status_code, message, detail = _detail_for(QUOTA_MESSAGE)

    assert status_code == 400
    assert message == QUOTA_MESSAGE, "高德原文案要原样保留，前端会把它显示给用户"
    assert detail == error_handlers._QUOTA_ERROR_DETAIL
    assert "调整输入条件" not in detail


def test_other_planning_failure_keeps_original_hint():
    _, _, detail = _detail_for("路线规划缺少坐标信息")

    assert detail == error_handlers._PLANNING_ERROR_DETAIL
