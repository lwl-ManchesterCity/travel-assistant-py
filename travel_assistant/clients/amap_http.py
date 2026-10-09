"""高德 HTTP 请求的公共实现，对应 Java 四个 Amap*Client 里逐字重复的 fetchXxxResponse / fetchXxxByCurl。

Java 在四个 client 中各自复制了一份「候选 URL 依次尝试 + curl fallback」逻辑，只是提示文案不同；
这里收敛成一个函数，各 client 用参数保留自己原本的 error/log 前缀与兜底规则。
唯一行为差异：写进日志和异常信息的 URL 会把 api-key 打码，见 _redact。
"""

import json
import logging
import subprocess
import time
from urllib.parse import quote, urlencode

import httpx

from ..config.settings import AmapSettings
from ..exceptions import TripPlanningException, is_quota_exhausted

log = logging.getLogger(__name__)

_USER_AGENT = "travel-assistant-java/1.0"
_CONNECT_TIMEOUT_SECONDS = 10
_REQUEST_TIMEOUT_SECONDS = 15
_CURL_MAX_TIME_SECONDS = 20


def build_candidate_urls(amap: AmapSettings, path: str, params: dict[str, object]) -> list[str]:
    """对应 Java buildCandidateUris：配置的 baseUrl 加另一个协议，再按字符串去重。"""
    base_url = amap.base_url
    candidates = [build_url(base_url, path, params)]
    if base_url.startswith("https://"):
        candidates.append(build_url(base_url.replace("https://", "http://"), path, params))
    elif base_url.startswith("http://"):
        candidates.append(build_url(base_url.replace("http://", "https://"), path, params))

    unique: list[str] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return unique


def build_url(base_url: str, path: str, params: dict[str, object]) -> str:
    """对应 UriComponentsBuilder.fromHttpUrl(baseUrl).path(path).queryParam(...).encode(UTF_8)。"""
    return f"{base_url}{path}?{urlencode(_stringify_params(params), quote_via=_quote_value)}"


def _quote_value(value: object, safe: str = "", encoding: str | None = None, errors: str | None = None) -> str:
    # Spring 的 encode() 不编码查询串里的逗号（origin/destination 形如 lng,lat），空格编码成 %20。
    return quote(str(value), safe=",", encoding=encoding, errors=errors)


def _stringify_params(params: dict[str, object]) -> dict[str, str]:
    # Java 的布尔查询参数渲染为 true/false；直接 str(True) 会得到 True，高德会判为非法值。
    return {
        key: ("true" if value else "false") if isinstance(value, bool) else str(value)
        for key, value in params.items()
    }


def fetch_json(
    amap: AmapSettings,
    candidate_urls: list[str],
    *,
    log_scope: str,
    error_prefix: str,
    request_exception_prefix: str,
    curl_exception_prefix: str,
    catch_curl_errors: bool,
) -> dict:
    """对应 Java fetchXxxResponse，外加一层 Java 没有的限流退避重试。

    Java 只给路线做了限流降级，景点/天气/酒店撞上 CUQPS 会直接把整个请求打成 400；
    而一次规划里这三路是被并行调用的，个人 key 的并发配额很容易瞬时打满。
    退避几百毫秒重试通常就能过，比重试失败再降级更能保住真实数据。

    重试只针对配额/频率类失败：参数错、key 错这类重试多少次都一样，直接抛更快。
    """
    failures: list[str] = []
    backoffs = amap.rate_limit_retry_backoffs()
    attempt = 0

    while True:
        try:
            return _fetch_once(
                amap,
                candidate_urls,
                failures,
                log_scope=log_scope,
                error_prefix=error_prefix,
                request_exception_prefix=request_exception_prefix,
                curl_exception_prefix=curl_exception_prefix,
                catch_curl_errors=catch_curl_errors,
            )
        except TripPlanningException as exception:
            if attempt >= len(backoffs) or not is_quota_exhausted(exception):
                raise
            backoff = backoffs[attempt]
            attempt += 1
            # 用 warning 而不是 info：撞限流是外部依赖降级的信号，运维要在默认日志级别下看见。
            log.warning("%s命中高德配额限制，%.1fs 后重试（第 %d 次）: %s",
                        log_scope, backoff, attempt, exception)
            time.sleep(backoff)


def _fetch_once(
    amap: AmapSettings,
    candidate_urls: list[str],
    failures: list[str],
    *,
    log_scope: str,
    error_prefix: str,
    request_exception_prefix: str,
    curl_exception_prefix: str,
    catch_curl_errors: bool,
) -> dict:
    """一次完整尝试：先用 http client 逐个候选地址请求，失败后按开关走 curl fallback。"""
    for url in candidate_urls:
        display = _redact(url, amap.api_key)
        try:
            response = httpx.get(
                url,
                timeout=httpx.Timeout(_REQUEST_TIMEOUT_SECONDS, connect=_CONNECT_TIMEOUT_SECONDS),
                follow_redirects=True,
                headers={"Accept": "application/json", "User-Agent": _USER_AGENT},
            )
            if response.status_code >= 500:
                failures.append(f"{display} -> HTTP {response.status_code}")
                continue
            if not response.text or not response.text.strip():
                failures.append(f"{display} -> empty body")
                continue
            return check_status(response.json(), error_prefix)
        except TripPlanningException:
            raise
        except Exception as exception:  # noqa: BLE001 - 对齐 Java 的 catch (Exception)
            failures.append(f"{display} -> {type(exception).__name__}: {exception}")

    if amap.curl_fallback_enabled:
        log.warning("HTTP client 调用%s失败，开始尝试 curl fallback: %s", log_scope, " | ".join(failures))
        if catch_curl_errors:
            try:
                return fetch_json_by_curl(candidate_urls, amap.api_key, log_scope, error_prefix, curl_exception_prefix, failures)
            except TripPlanningException:
                raise
            except Exception as exception:  # noqa: BLE001
                failures.append(f"curl fallback -> {type(exception).__name__}: {exception}")
        else:
            return fetch_json_by_curl(candidate_urls, amap.api_key, log_scope, error_prefix, curl_exception_prefix, failures)

    raise TripPlanningException(f"{request_exception_prefix}{' | '.join(failures)}")


def fetch_json_by_curl(
    candidate_urls: list[str],
    api_key: str | None,
    log_scope: str,
    error_prefix: str,
    curl_exception_prefix: str,
    failures: list[str],
) -> dict:
    """对应 Java fetchXxxByCurl：curl fallback 仍是真实的高德请求，只是换了请求方式，不是 mock。"""
    for url in candidate_urls:
        display = _redact(url, api_key)
        log.warning("执行 curl fallback 请求%s: %s", log_scope, display)
        process = subprocess.run(  # noqa: S603 - 命令与 Java ProcessBuilder 参数一致
            [
                "/usr/bin/curl",
                "-s",
                "--connect-timeout", str(_CONNECT_TIMEOUT_SECONDS),
                "--max-time", str(_CURL_MAX_TIME_SECONDS),
                url,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if process.returncode != 0:
            stderr = process.stderr.strip()
            failures.append(f"curl {display} -> exitCode={process.returncode}{f', stderr={stderr}' if stderr else ''}")
            continue
        if not process.stdout or not process.stdout.strip():
            failures.append(f"curl {display} -> empty body")
            continue
        return check_status(json.loads(process.stdout), error_prefix)
    raise TripPlanningException(f"{curl_exception_prefix}{' | '.join(failures)}")


def check_status(response_body: dict, error_prefix: str) -> dict:
    """对应 Java 的 if (!"1".equals(status)) throw new TripPlanningException(...)。"""
    if str(response_body.get("status")) != "1":
        info = str(response_body.get("info", "未知错误"))
        info_code = str(response_body.get("infocode", "unknown"))
        raise TripPlanningException(f"{error_prefix}失败：{info}（infocode={info_code}）")
    return response_body


def _redact(url: str, api_key: str | None) -> str:
    if api_key:
        return url.replace(f"key={api_key}", "key=***")
    return url
