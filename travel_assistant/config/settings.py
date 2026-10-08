"""对应 Java config/TravelLoopProperties.java、TravelProviderProperties.java、TravelLlmProperties.java
与 application.yml 的 travel 段。

Java 用 @ConfigurationProperties(prefix="travel") 绑定 yml；Python 侧没有 yml，等价做法是每个配置项
声明自己的环境变量名，密钥只能来自环境变量或本地未提交的 .env，绝不写进代码。
"""

import os

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class TravelDefaults(BaseModel):
    """配置模型公共行为：既支持环境变量名，也允许用字段名构造（测试与容器装配用）。

    注意 env_file 必须在每个 BaseSettings 类上单独声明：pydantic-settings 只在自己的
    SettingsConfigDict 里读 dotenv，放在公共父类的 ConfigDict 中不会生效。
    """

    model_config = ConfigDict(populate_by_name=True, extra="ignore")


class LoopSettings(TravelDefaults, BaseSettings):
    """对应 Java config/TravelLoopProperties.java + application.yml 的 travel.loop 段。"""

    enabled: bool = Field(default=True, validation_alias="TRAVEL_LOOP_ENABLED")
    max_rounds: int = Field(default=3, validation_alias="TRAVEL_LOOP_MAX_ROUNDS")
    max_no_improvement_rounds: int = Field(default=2, validation_alias="TRAVEL_LOOP_MAX_NO_IMPROVEMENT_ROUNDS")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


class LlmSettings(TravelDefaults, BaseSettings):
    """对应 Java config/TravelLlmProperties.java + application.yml 的 travel.llm 段。"""

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
        # application.yml: api-key: ${TRAVEL_LLM_API_KEY:${DASHSCOPE_API_KEY:}}
        if self.api_key:
            return self.api_key
        return os.environ.get("DASHSCOPE_API_KEY", "")


class AmapSettings(TravelDefaults, BaseSettings):
    """对应 Java TravelProviderProperties.Amap。"""

    api_key: str = Field(default="", validation_alias="AMAP_API_KEY")
    base_url: str = Field(default="http://restapi.amap.com", validation_alias="AMAP_BASE_URL")
    mock_response: bool = Field(default=False, validation_alias="AMAP_MOCK_RESPONSE")
    route_strategy: str = Field(default="0", validation_alias="AMAP_ROUTE_STRATEGY")
    route_extensions: str = Field(default="base", validation_alias="AMAP_ROUTE_EXTENSIONS")
    curl_fallback_enabled: bool = Field(default=True, validation_alias="AMAP_CURL_FALLBACK_ENABLED")
    poi_page_size: int = Field(default=10, validation_alias="AMAP_POI_PAGE_SIZE")
    city_limit: bool = Field(default=True, validation_alias="AMAP_CITY_LIMIT")
    # 高德前端地图 SDK 的两个键：由 api/map_controller.py 原样吐给 GET /api/map/config，
    # runtime_logger 只取 web_js_key 的非空布尔。值只能来自环境变量或 .env，不写进代码；
    # 后端取数链路（四个 Amap client）不使用它们。
    web_js_key: str = Field(default="", validation_alias="AMAP_WEB_JS_KEY")
    security_js_code: str = Field(default="", validation_alias="AMAP_SECURITY_JS_CODE")

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)


class ProviderSettings(TravelDefaults, BaseSettings):
    """对应 Java config/TravelProviderProperties.java + application.yml 的 travel.providers 段。"""

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


class Settings(BaseSettings):
    """对应 application.yml 顶层：server.port + travel.loop + travel.llm + travel.providers。"""

    server_port: int = Field(default=8080, validation_alias="SERVER_PORT")
    loop: LoopSettings = Field(default_factory=LoopSettings)
    llm: LlmSettings = Field(default_factory=LlmSettings)
    providers: ProviderSettings = Field(default_factory=ProviderSettings)
    anchor: AnchorSettings = Field(default_factory=AnchorSettings)

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
