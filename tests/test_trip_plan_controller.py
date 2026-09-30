"""对应 src/test/java/com/lwl/travelassistant/controller/TripPlanControllerTest.java。

Java 用 @WebMvcTest + @MockBean 替换 TripPlanningService，Python 侧等价做法是覆盖 FastAPI 依赖。
Java 测试类里没有 /adjust 的用例，三条 /adjust 断言取自 TripAdjustmentRequest 的
@NotNull/@NotBlank 文案与 TripAdjustmentResponse 的字段集合。
"""

import pytest
from fastapi.testclient import TestClient

from travel_assistant.config.container import get_trip_planning_service
from travel_assistant.main import app
from travel_assistant.models.budget import Budget
from travel_assistant.models.day_plan import DayPlan
from travel_assistant.models.hotel import Hotel
from travel_assistant.models.location import Location
from travel_assistant.models.loop_execution_summary import LoopExecutionSummary
from travel_assistant.models.loop_iteration_trace import LoopIterationTrace
from travel_assistant.models.meal import Meal
from travel_assistant.models.trip_adjustment_request import TripAdjustmentRequest
from travel_assistant.models.trip_adjustment_response import TripAdjustmentResponse
from travel_assistant.models.trip_plan import TripPlan
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.models.weather_info import WeatherInfo

VALID_REQUEST = {
    "city": "杭州",
    "startDate": "2026-07-01",
    "endDate": "2026-07-03",
    "budget": 3000,
    "preferences": ["美食", "自然风景"],
    "transportation": "公共交通",
    "accommodation": "舒适型酒店",
    "extraRequirements": "希望节奏轻松一点",
}

INVALID_REQUEST = {
    "city": "",
    "startDate": "2026-07-01",
    "endDate": "",
    "budget": 0,
    "preferences": [],
    "transportation": "",
    "accommodation": "",
}

VALID_ADJUST_REQUEST = {
    "originalRequest": VALID_REQUEST,
    "adjustmentPrompt": "预算改成5000，住经济型酒店",
}


class StubTripPlanningService:
    """对应 @MockBean TripPlanningService。"""

    def adjust_trip(self, request: TripAdjustmentRequest) -> TripAdjustmentResponse:
        return TripAdjustmentResponse(
            trip_plan=self.plan_trip(request.original_request),
            updated_request=request.original_request.model_copy(update={
                "budget": 5000,
                "accommodation": "经济型酒店",
            }),
            applied_changes=["已将预算调整为 5000 元", "已切换为经济型酒店偏好"],
        )

    def plan_trip(self, request: TripPlanRequest) -> TripPlan:
        return TripPlan(
            city="杭州",
            start_date="2026-07-01",
            end_date="2026-07-03",
            days=[DayPlan(
                date="2026-07-01",
                day_index=0,
                description="第1天围绕西湖展开。",
                transportation="公共交通",
                accommodation="舒适型酒店",
                hotel=Hotel(name="舒适型精选酒店", address="杭州中心城区",
                            location=Location(longitude=120.18, latitude=30.25),
                            price_range="500-800元", rating="4.5", distance="距离景点1.5公里",
                            type="舒适型酒店", estimated_cost=560),
                attractions=[],
                meals=[Meal(type="breakfast", name="杭州风味早餐", address="西湖边",
                            location=Location(longitude=120.15, latitude=30.27),
                            description="轻量开启当天行程", estimated_cost=30)],
            )],
            weather_info=[WeatherInfo(date="2026-07-01", day_weather="多云到晴", night_weather="晴",
                                      day_temp=28, night_temp=22, wind_direction="东北风", wind_power="2级")],
            overall_suggestions="杭州 3 天旅行计划",
            budget=Budget(total_attractions=150, total_hotels=1200, total_meals=540,
                          total_transportation=300, total=2190),
            planning_notes=["测试规划备注"],
            agent_traces=[],
            loop_summary=LoopExecutionSummary(
                enabled=True,
                rounds_executed=2,
                selected_round=2,
                stop_reason="第 2 轮通过规则评估，直接停止",
            ),
            loop_iterations=[LoopIterationTrace(
                round=1,
                request_summary="城市=杭州，预算=3000，住宿=舒适型酒店，交通=公共交通",
                total_cost=2760,
                remaining_budget=240,
                total_route_minutes=180,
                reflection_score=40,
                budget_severity=40,
                within_budget=False,
                should_replan=True,
                reflection_action="REDUCE_BUDGET",
                critique_source="rule",
                critique_action="REDUCE_BUDGET",
                applied_changes=["住宿偏好下调为 经济型酒店"],
                selected_as_best=False,
            )],
        )


@pytest.fixture
def client():
    app.dependency_overrides[get_trip_planning_service] = StubTripPlanningService
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_should_return_trip_plan_when_request_is_valid(client):
    response = client.post("/api/trips/plan", json=VALID_REQUEST)

    assert response.status_code == 200
    body = response.json()
    assert body["overallSuggestions"] == "杭州 3 天旅行计划"
    assert body["days"][0]["transportation"] == "公共交通"
    assert body["budget"]["total"] == 2190
    # loop 两字段按 Java 的驼峰属性名出到 HTTP 响应
    assert body["loopSummary"] == {
        "enabled": True,
        "roundsExecuted": 2,
        "selectedRound": 2,
        "stopReason": "第 2 轮通过规则评估，直接停止",
    }
    assert body["loopIterations"][0]["reflectionAction"] == "REDUCE_BUDGET"
    assert body["loopIterations"][0]["requestSummary"] == "城市=杭州，预算=3000，住宿=舒适型酒店，交通=公共交通"
    assert body["loopIterations"][0]["selectedAsBest"] is False
    assert body["loopIterations"][0]["appliedChanges"] == ["住宿偏好下调为 经济型酒店"]


def test_should_return_validation_error_when_request_is_invalid(client):
    response = client.post("/api/trips/plan", json=INVALID_REQUEST)

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert body["message"] == "请求参数校验失败"
    assert isinstance(body["details"], list)


def test_should_return_adjustment_response_when_adjust_request_is_valid(client):
    response = client.post("/api/trips/adjust", json=VALID_ADJUST_REQUEST)

    assert response.status_code == 200
    body = response.json()
    # Java 的 TripAdjustmentResponse 只有这三个属性，adjustmentSource 不进响应体
    assert set(body) == {"tripPlan", "updatedRequest", "appliedChanges"}
    assert body["appliedChanges"] == ["已将预算调整为 5000 元", "已切换为经济型酒店偏好"]
    assert body["updatedRequest"]["budget"] == 5000
    assert body["updatedRequest"]["accommodation"] == "经济型酒店"
    assert body["updatedRequest"]["extraRequirements"] == "希望节奏轻松一点"
    assert body["tripPlan"]["budget"]["total"] == 2190
    assert body["tripPlan"]["loopSummary"]["roundsExecuted"] == 2


def test_should_return_validation_error_when_original_request_is_missing(client):
    response = client.post("/api/trips/adjust", json={"adjustmentPrompt": "预算改成5000"})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "originalRequest: must not be null" in body["details"]


def test_should_return_validation_error_when_adjustment_prompt_is_blank(client):
    response = client.post("/api/trips/adjust", json={"originalRequest": VALID_REQUEST,
                                                      "adjustmentPrompt": "   "})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "adjustmentPrompt: must not be blank" in body["details"]
