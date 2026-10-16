"""依赖装配：按依赖关系手工把整套对象建出来，供各端点取用。

没有自动注入的容器，谁依赖谁全在这里显式写出来；装配顺序就是构造参数的先后，
改了某个类的构造签名，这里要跟着改。
"""

from dataclasses import dataclass

from ..agent.ask_agent import AskAgent
from ..agent.attraction_agent import AttractionAgent
from ..agent.data_tools import build_planning_tools
from ..agent.hotel_agent import HotelAgent
from ..agent.llm_critic_agent import LlmCriticAgent
from ..agent.loop_decision_executor import LoopDecisionExecutor
from ..agent.loop_planning_agent import LoopPlanningAgent
from ..agent.orchestration import PlanningDataAgent
from ..agent.planner_agent import PlannerAgent
from ..agent.reflection_agent import ReflectionAgent
from ..agent.route_agent import RouteAgent
from ..agent.trip_adjustment_agent import TripAdjustmentAgent
from ..agent.weather_agent import WeatherAgent
from ..clients.amap_attraction import AmapAttractionClient
from ..clients.amap_hotel import AmapHotelClient
from ..clients.amap_route import AmapRouteClient
from ..clients.amap_weather import AmapWeatherClient
from ..clients.knowledge_base import TravelKnowledgeService
from ..clients.llm import LlmClient, OpenAiCompatibleLlmClient
from ..clients.local_attraction import InMemoryAttractionClient
from ..clients.postgres_auth_store import PostgresAuthStore
from ..clients.postgres_episode_store import PostgresEpisodeStore
from ..clients.postgres_preference_store import PostgresPreferenceStore
from ..clients.rule_hotel import RuleBasedHotelClient
from ..clients.rule_route import RuleBasedRouteClient
from ..clients.rule_weather import RuleBasedWeatherClient
from ..clients.selector import (
    ConfigurableAttractionClient,
    ConfigurableHotelClient,
    ConfigurableRouteClient,
    ConfigurableWeatherClient,
)
from ..evaluator.budget_evaluator import BudgetEvaluator
from ..evaluator.daily_plan_evaluator import DailyPlanEvaluator
from ..evaluator.route_evaluator import RouteEvaluator
from ..evaluator.weather_evaluator import WeatherEvaluator
from ..service.auth_service import AuthService
from ..service.episode_service import EpisodeService
from ..service.llm_narration_service import LlmNarrationService
from ..service.hotel_anchor_service import HotelAnchorService
from ..service.intercity_service import IntercityService
from ..service.planner_input_builder import PlannerInputBuilder
from ..service.preference_service import PreferenceService
from ..service.request_optimization_strategy_service import RequestOptimizationStrategyService
from ..service.requirement_analysis_service import RequirementAnalysisService
from ..service.trip_planning_service import TripPlanningService
from .settings import AmapSettings, Settings, get_settings


@dataclass
class Container:
    settings: Settings
    llm_client: LlmClient
    trip_planning_service: TripPlanningService
    ask_agent: AskAgent
    # 追问链把它裹进工具里，手上不留引用；验收探针要单独按名字查一个景点
    # （挑「本地库没有、高德给得出开放时间」的靶子），所以在这里也露一格。
    attraction_agent: AttractionAgent
    auth_service: AuthService
    preference_service: PreferenceService
    episode_service: EpisodeService


def build_container(settings: Settings | None = None) -> Container:
    settings = settings or get_settings()
    providers = settings.providers
    amap = providers.amap

    # 兜底实现：不连外网也能跑完规划（规则版 / 内存版）
    travel_knowledge_service = TravelKnowledgeService()
    in_memory_attraction_client = InMemoryAttractionClient(travel_knowledge_service)
    rule_hotel_client = RuleBasedHotelClient()
    rule_weather_client = RuleBasedWeatherClient()
    rule_route_client = RuleBasedRouteClient()

    # 高德实现：真实 HTTP 调用，走 httpx，httpx 这条路全失败时再用本机 curl 重试一遍
    amap_attraction_client = AmapAttractionClient(amap, in_memory_attraction_client)
    amap_hotel_client = AmapHotelClient(amap, rule_hotel_client)
    amap_weather_client = AmapWeatherClient(amap, rule_weather_client)
    amap_route_client = AmapRouteClient(amap, rule_route_client)

    # provider 选择器：按配置在兜底实现与高德实现之间挑一个
    attraction_client = ConfigurableAttractionClient(
        providers, in_memory_attraction_client, amap_attraction_client
    )
    hotel_client = ConfigurableHotelClient(providers, rule_hotel_client, amap_hotel_client)
    weather_client = ConfigurableWeatherClient(providers, rule_weather_client, amap_weather_client)
    route_client = ConfigurableRouteClient(providers, rule_route_client, amap_route_client)

    llm_client = OpenAiCompatibleLlmClient(settings.llm)

    attraction_agent = AttractionAgent(attraction_client)
    weather_agent = WeatherAgent(weather_client)
    hotel_agent = HotelAgent(hotel_client)
    route_agent = RouteAgent(route_client)

    daily_plan_evaluator = DailyPlanEvaluator(
        BudgetEvaluator(), RouteEvaluator(), WeatherEvaluator()
    )

    # 长期偏好（docs/ASK_LONGTERM_MEMORY.md 第二档）。**规划链路也不碰它**——
    # 装配它不会去连库，池子是第一次用到才连（见 db.py）。
    # 两个写入点（追问链 / 需求理解）与两处注入共用**同一个** service 实例。
    preference_service = PreferenceService(PostgresPreferenceStore())

    # 情节记忆（docs/ASK_LONGTERM_MEMORY.md §2.4）。与偏好**分开两个 service**，
    # 但共用同一个 Store 语义：两个写入点、两处注入也都共用同一个实例。
    episode_service = EpisodeService(PostgresEpisodeStore())

    requirement_analysis_service = RequirementAnalysisService(llm_client)
    llm_narration_service = LlmNarrationService(llm_client)
    planner_agent = PlannerAgent(route_agent, daily_plan_evaluator, llm_narration_service, settings.anchor)

    planning_data_agent = PlanningDataAgent(
        llm_client, build_planning_tools(attraction_agent, weather_agent, hotel_agent)
    )
    planner_input_builder = PlannerInputBuilder(
        planning_data_agent, requirement_analysis_service,
        HotelAnchorService(amap, amap_hotel_client, settings.anchor),
        attraction_client,
        IntercityService(amap, route_client),
        preference_service,
        episode_service)

    # Loop 闭环：规划 + 反思 + LLM 评审 + 决策执行
    request_optimization_strategy_service = RequestOptimizationStrategyService()
    reflection_agent = ReflectionAgent(daily_plan_evaluator)
    llm_critic_agent = LlmCriticAgent(llm_client)
    loop_decision_executor = LoopDecisionExecutor(request_optimization_strategy_service)
    loop_planning_agent = LoopPlanningAgent(
        planner_input_builder,
        planner_agent,
        reflection_agent,
        llm_critic_agent,
        loop_decision_executor,
        settings.loop,
    )

    # 行程调整：与 loop 共用上面那同一个策略服务实例
    trip_adjustment_agent = TripAdjustmentAgent(llm_client, request_optimization_strategy_service)

    # 追问 agent：回答"某天怎么玩"这类问题，复用上面那三个数据 Agent
    ask_agent = AskAgent(llm_client, attraction_agent, weather_agent, hotel_agent,
                         preference_service, episode_service)

    auth_service = AuthService(PostgresAuthStore(), settings.db.session_days)

    return Container(
        settings=settings,
        llm_client=llm_client,
        trip_planning_service=TripPlanningService(trip_adjustment_agent, loop_planning_agent),
        ask_agent=ask_agent,
        attraction_agent=attraction_agent,
        auth_service=auth_service,
        preference_service=preference_service,
        episode_service=episode_service,
    )


_container: Container | None = None


def get_container() -> Container:
    global _container
    if _container is None:
        _container = build_container()
    return _container


def reset_container() -> None:
    global _container
    _container = None


def get_trip_planning_service() -> TripPlanningService:
    """端点依赖：取容器里的规划服务（/plan 与 /adjust 都用它）。"""
    return get_container().trip_planning_service


def get_amap_settings() -> AmapSettings:
    """端点依赖：取容器配置里的高德段（map_controller 用它吐前端地图键）。"""
    return get_container().settings.providers.amap


def get_ask_agent() -> AskAgent:
    """方案 A 的追问端点按类型注入 AskAgent。"""
    return get_container().ask_agent


def get_auth_service() -> AuthService:
    """登录相关端点按类型注入 AuthService。"""
    return get_container().auth_service


def get_preference_service() -> PreferenceService:
    """规划端点按类型注入 PreferenceService（第二档起，按 user_id 读写长期偏好）。"""
    return get_container().preference_service


def get_episode_service() -> EpisodeService:
    """按类型注入 EpisodeService（第四档起，按 user_id + 城市读写情节记忆）。"""
    return get_container().episode_service
