"""对应 src/test/java/com/lwl/travelassistant/agent/PlannerAgentTest.java。"""

from travel_assistant.agent.planner_agent import PlannerAgent
from travel_assistant.agent.route_agent import RouteAgent
from travel_assistant.clients.llm import LlmClient
from travel_assistant.clients.rule_route import RuleBasedRouteClient
from travel_assistant.evaluator.budget_evaluator import BudgetEvaluator
from travel_assistant.evaluator.daily_plan_evaluator import DailyPlanEvaluator
from travel_assistant.evaluator.route_evaluator import RouteEvaluator
from travel_assistant.evaluator.weather_evaluator import WeatherEvaluator
from travel_assistant.models.attraction import Attraction
from travel_assistant.models.attraction_search_result import AttractionSearchResult
from travel_assistant.models.hotel import Hotel
from travel_assistant.models.hotel_search_result import HotelSearchResult
from travel_assistant.models.location import Location
from travel_assistant.models.planner_input import PlannerInput
from travel_assistant.models.planning_constraints import PlanningConstraints
from travel_assistant.models.provider_metadata import ProviderMetadata
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.models.weather_info import WeatherInfo
from travel_assistant.models.weather_query_result import WeatherQueryResult
from travel_assistant.service.llm_narration_service import LlmNarrationService


class DisabledLlmClient(LlmClient):
    """对应测试里的 DisabledLlmClient。"""

    def is_available(self) -> bool:
        return False

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        return ""


def build_planner_agent() -> PlannerAgent:
    return PlannerAgent(
        RouteAgent(RuleBasedRouteClient()),
        # Java 这里注入的是 ReflectionAgent(DailyPlanEvaluator)，本轮 ReflectionAgent 未搬，
        # reflect_daily_plan 直接落在 DailyPlanEvaluator 上。
        DailyPlanEvaluator(BudgetEvaluator(), RouteEvaluator(), WeatherEvaluator()),
        LlmNarrationService(DisabledLlmClient()),
    )


def build_planner_input() -> PlannerInput:
    request = TripPlanRequest(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        budget=3000,
        preferences=["美食", "自然风景"],
        transportation="公共交通",
        accommodation="舒适型酒店",
        extra_requirements="希望整体节奏轻松一些，对海鲜过敏",
    )
    attraction_result = AttractionSearchResult(
        city="杭州",
        preferences=request.preferences,
        attractions=[
            Attraction(name="西湖", address="杭州西湖景区", location=Location(longitude=120.1551, latitude=30.2741),
                       visit_duration=180, description="经典必去", category="自然风景", rating=4.8, ticket_price=60),
            Attraction(name="河坊街", address="杭州上城区", location=Location(longitude=120.1712, latitude=30.2429),
                       visit_duration=90, description="适合夜游", category="美食购物", rating=4.5, ticket_price=0),
            Attraction(name="灵隐寺", address="杭州西湖区", location=Location(longitude=120.1045, latitude=30.2417),
                       visit_duration=120, description="适合安静体验", category="人文历史", rating=4.7, ticket_price=45),
        ],
        summary="已匹配景点",
        provider=ProviderMetadata(provider_code="local-knowledge-base", provider_name="本地知识库景点源", mock=True),
    )
    weather_result = WeatherQueryResult(
        city="杭州",
        start_date=request.start_date,
        end_date=request.end_date,
        weather_info=[
            WeatherInfo(date="2026-07-01", day_weather="多云到晴", night_weather="晴", day_temp=28,
                        night_temp=22, wind_direction="东北风", wind_power="2级"),
            WeatherInfo(date="2026-07-02", day_weather="多云", night_weather="晴", day_temp=27,
                        night_temp=21, wind_direction="东风", wind_power="2级"),
            WeatherInfo(date="2026-07-03", day_weather="晴", night_weather="晴", day_temp=29,
                        night_temp=22, wind_direction="东南风", wind_power="3级"),
        ],
        summary="已获取天气",
        provider=ProviderMetadata(provider_code="rule-weather-engine", provider_name="规则天气引擎", mock=True),
    )
    hotel_result = HotelSearchResult(
        city="杭州",
        accommodation=request.accommodation,
        hotels=[
            Hotel(name="舒适型精选酒店", address="杭州中心城区", location=Location(longitude=120.18, latitude=30.25),
                  price_range="500-800元", rating="4.5", distance="距离景点1.5公里",
                  type="舒适型酒店", estimated_cost=560),
        ],
        summary="已推荐酒店",
        provider=ProviderMetadata(provider_code="rule-hotel-engine", provider_name="规则酒店推荐引擎", mock=True),
    )
    return PlannerInput(
        request=request,
        attraction_result=attraction_result,
        weather_result=weather_result,
        hotel_result=hotel_result,
        constraints=PlanningConstraints(
            relaxed_pace=True,
            avoid_seafood=True,
            wants_landmark_ceremony=False,
            prefers_indoor_backup=False,
            extracted_tags=["轻松节奏", "餐饮避开海鲜"],
        ),
        planning_notes=["优先考虑用户偏好：美食、自然风景"],
        planner_query="请生成杭州旅行计划",
        agent_traces=[],
    )


def test_should_build_structured_trip_plan_from_planner_input():
    trip_plan = build_planner_agent().build_plan(build_planner_input())

    assert len(trip_plan.days) == 3
    assert trip_plan.days[0].transportation == "公共交通"
    assert len(trip_plan.weather_info) == 3
    assert "杭州" in trip_plan.overall_suggestions
    assert trip_plan.budget.total > 0
    assert trip_plan.budget.requested_budget > 0
    assert trip_plan.days[0].meals[2].type == "dinner"
    assert "避开海鲜" in trip_plan.days[0].meals[2].description
    route_plan = trip_plan.days[0].route_plan
    assert len(route_plan.steps) >= 2
    assert route_plan.total_distance_km > 0
    assert trip_plan.days[0].estimated_cost > 0
    assert "ReflectionAgent 已对每日方案完成预算、路线、天气反思评估" in trip_plan.planning_notes
    assert any(trace.agent_name == "ReflectionAgent" for trace in trip_plan.agent_traces)
