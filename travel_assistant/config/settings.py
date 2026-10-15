"""运行时配置集中在这里，每个配置项声明自己的环境变量名。

密钥只能来自环境变量或本地未提交的 .env，绝不写进代码。
"""

import logging
import os

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

log = logging.getLogger(__name__)


class TravelDefaults(BaseModel):
    """配置模型公共行为：既支持环境变量名，也允许用字段名构造（测试与容器装配用）。

    注意 env_file 必须在每个 BaseSettings 类上单独声明：pydantic-settings 只在自己的
    SettingsConfigDict 里读 dotenv，放在公共父类的 ConfigDict 中不会生效。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class LoopSettings(TravelDefaults, BaseSettings):
    """规划闭环：开关、最大轮次、连续几轮无改善就停。"""

    enabled: bool = Field(default=True, validation_alias="TRAVEL_LOOP_ENABLED")
    max_rounds: int = Field(default=3, validation_alias="TRAVEL_LOOP_MAX_ROUNDS")
    max_no_improvement_rounds: int = Field(default=2, validation_alias="TRAVEL_LOOP_MAX_NO_IMPROVEMENT_ROUNDS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


class LlmSettings(TravelDefaults, BaseSettings):
    """规划与追问里用到的 LLM：开关、接入地址、密钥与生成参数。"""

    enabled: bool = Field(default=False, validation_alias="TRAVEL_LLM_ENABLED")
    provider: str = Field(default="dashscope", validation_alias="TRAVEL_LLM_PROVIDER")
    base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1",
        validation_alias="TRAVEL_LLM_BASE_URL",
    )
    api_key: str = Field(default="", validation_alias="TRAVEL_LLM_API_KEY")
    model: str = Field(default="qwen-plus", validation_alias="TRAVEL_LLM_MODEL")
    temperature: float = Field(default=0.2, validation_alias="TRAVEL_LLM_TEMPERATURE")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    def resolved_api_key(self) -> str:
        # 没配 TRAVEL_LLM_API_KEY 时，退回通用的 DASHSCOPE_API_KEY 环境变量。
        if self.api_key:
            return self.api_key
        return os.environ.get("DASHSCOPE_API_KEY", "")


class AmapSettings(TravelDefaults, BaseSettings):
    """高德侧接入参数：key、base_url、路线策略、翻页与重试等。"""

    api_key: str = Field(default="", validation_alias="AMAP_API_KEY")
    base_url: str = Field(default="http://restapi.amap.com", validation_alias="AMAP_BASE_URL")
    mock_response: bool = Field(default=False, validation_alias="AMAP_MOCK_RESPONSE")
    route_strategy: str = Field(default="0", validation_alias="AMAP_ROUTE_STRATEGY")
    route_extensions: str = Field(default="base", validation_alias="AMAP_ROUTE_EXTENSIONS")
    curl_fallback_enabled: bool = Field(default=True, validation_alias="AMAP_CURL_FALLBACK_ENABLED")
    poi_page_size: int = Field(default=10, validation_alias="AMAP_POI_PAGE_SIZE")
    city_limit: bool = Field(default=True, validation_alias="AMAP_CITY_LIMIT")
    # 撞上高德配额/频率类失败时的短退避重试（此前只有路线做了降级，景点/天气/酒店撞上就直接失败）。
    # 一次规划里景点/天气/酒店是被并行调用的，个人 key 的并发配额很容易瞬时打满，
    # 退避几百毫秒重试通常就能过。attempts=0 即关闭重试，失败直接往上抛。
    # 退避秒数写成逗号分隔的字符串而不是 list[float]：pydantic-settings 对复杂类型会按 JSON 解析，
    # "0.8,2.0" 不是合法 JSON，用 str 收再自己拆更可控，也和 preferences 那类逗号串的写法一致。
    rate_limit_retry_attempts: int = Field(default=2, validation_alias="AMAP_RATE_LIMIT_RETRY_ATTEMPTS")
    rate_limit_retry_backoff_seconds: str = Field(
        default="0.8,2.0", validation_alias="AMAP_RATE_LIMIT_RETRY_BACKOFF_SECONDS"
    )
    # 高德前端地图 SDK 的两个键：由 api/map_controller.py 原样吐给 GET /api/map/config，
    # runtime_logger 只取 web_js_key 的非空布尔。值只能来自环境变量或 .env，不写进代码；
    # 后端取数链路（四个 Amap client）不使用它们。
    web_js_key: str = Field(default="", validation_alias="AMAP_WEB_JS_KEY")
    security_js_code: str = Field(default="", validation_alias="AMAP_SECURITY_JS_CODE")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    def rate_limit_retry_backoffs(self) -> tuple[float, ...]:
        """解析成每次重试前各睡多少秒，长度即重试次数。

        配置的秒数比 attempts 少时，多出来的几次沿用最后一个值；解析不出任何秒数就当作不重试。
        """
        parsed: list[float] = []
        for chunk in str(self.rate_limit_retry_backoff_seconds or "").split(","):
            chunk = chunk.strip()
            if not chunk:
                continue
            try:
                parsed.append(max(0.0, float(chunk)))
            except ValueError:
                log.warning("AMAP_RATE_LIMIT_RETRY_BACKOFF_SECONDS 里的 %r 不是数字，已忽略", chunk)
        if not parsed:
            return ()
        attempts = max(0, int(self.rate_limit_retry_attempts))
        return tuple(parsed[index] if index < len(parsed) else parsed[-1] for index in range(attempts))


class ProviderSettings(TravelDefaults, BaseSettings):
    """四类数据（景点/天气/酒店/路线）各走哪个 provider 实现。"""

    attraction: str = Field(default="amap", validation_alias="TRAVEL_PROVIDERS_ATTRACTION")
    weather: str = Field(default="amap", validation_alias="TRAVEL_PROVIDERS_WEATHER")
    hotel: str = Field(default="amap", validation_alias="TRAVEL_PROVIDERS_HOTEL")
    route: str = Field(default="amap", validation_alias="TRAVEL_PROVIDERS_ROUTE")
    amap: AmapSettings = Field(default_factory=AmapSettings)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


class AnchorSettings(TravelDefaults, BaseSettings):
    """用户指定住宿位置时怎么圈定候选酒店，对应 docs/ADJUST_AGENT_DESIGN.md 第 9 节。

    这三个数只决定"谁有资格进打分"，不参与任何分数计算，所以改它们不会重排已有候选。
    """

    radius_km: float = Field(default=5.0, validation_alias="TRAVEL_HOTEL_ANCHOR_RADIUS_KM")
    near_km: float = Field(default=1.5, validation_alias="TRAVEL_HOTEL_ANCHOR_NEAR_KM")
    mid_km: float = Field(default=3.0, validation_alias="TRAVEL_HOTEL_ANCHOR_MID_KM")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


class DbSettings(TravelDefaults, BaseSettings):
    """长期记忆的用户库，对应 docs/ASK_LONGTERM_MEMORY.md §2.1.1。

    默认 DSN 是 `postgresql:///travel_assistant`：三段分别是「空 host = 走 unix socket /
    空 user = 当前系统用户 / 库名」，所以**本机不需要用户名密码**。
    换机器、换库、上线上密码，都只改 TRAVEL_DB_DSN 这一个环境变量。
    """

    dsn: str = Field(default="postgresql:///travel_assistant", validation_alias="TRAVEL_DB_DSN")
    # 会话有效期。30 天是"常用的机器不用反复登"与"丢了手机也能等到它过期"之间的折中；
    # 真要立刻失效，有登出（删库里的那一行）。
    session_days: int = Field(default=30, validation_alias="TRAVEL_DB_SESSION_DAYS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


class Settings(BaseSettings):
    """顶层配置：服务端口，以及 loop / llm / providers / anchor / db 各段。"""

    server_port: int = Field(default=8080, validation_alias="SERVER_PORT")
    loop: LoopSettings = Field(default_factory=LoopSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    anchor: AnchorSettings = Field(default_factory=AnchorSettings)
    db: DbSettings = Field(default_factory=DbSettings)

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None
