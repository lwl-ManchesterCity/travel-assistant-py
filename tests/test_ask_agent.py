"""方案 A 追问 agent 的行为断言。

不接真实模型：用一个"按脚本调工具"的假 graph 顶掉 create_agent，
这样跑的是真工具、真上下文、真留痕，只有模型那一层是假的。
Java 侧没有对应物（/api/trips/ask 是移植基线之外的扩展），期望值取自 docs/ASK_AGENT_DESIGN.md。
"""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from travel_assistant.agent.ask_agent import AskAgent
from travel_assistant.config.container import get_ask_agent
from travel_assistant.exceptions import TripPlanningException
from travel_assistant.main import app
from travel_assistant.models.ask_request import AskRequest
from travel_assistant.models.attraction import Attraction
from travel_assistant.models.budget import Budget
from travel_assistant.models.day_plan import DayPlan
from travel_assistant.models.hotel import Hotel
from travel_assistant.models.location import Location
from travel_assistant.models.meal import Meal
from travel_assistant.models.route_plan import RoutePlan
from travel_assistant.models.trip_plan import TripPlan
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.models.weather_info import WeatherInfo

_LOCATION = Location(latitude=30.25, longitude=120.15)


def _trip_plan() -> TripPlan:
    return TripPlan(
        city="杭州",
        start_date="2026-09-30",
        end_date="2026-10-01",
        days=[
            DayPlan(
                day_index=0,
                date="2026-09-30",
                transportation="公共交通",
                hotel=Hotel(name="西湖湖畔酒店", price_range="300-500", estimated_cost=400, location=_LOCATION),
                attractions=[Attraction(name="杭州西湖风景名胜区", category="公园", ticket_price=0,
                                        visit_duration=180, address="西湖区", location=_LOCATION)],
                meals=[Meal(type="午餐", name="楼外楼", estimated_cost=120)],
                route_plan=RoutePlan(total_distance_km=12.5, total_duration_minutes=45,
                                     summary="高德路线已生成，共 2 段"),
                estimated_cost=520,
                optimization_notes=["首日大雨，已预留室内备选"],
            ),
            DayPlan(
                day_index=1,
                date="2026-10-01",
                transportation="公共交通",
                hotel=Hotel(name="西湖湖畔酒店", price_range="300-500", estimated_cost=400, location=_LOCATION),
                attractions=[
                    Attraction(name="西溪国家湿地公园", category="湿地公园", ticket_price=80,
                               visit_duration=240, address="余杭区", location=_LOCATION),
                    Attraction(name="灵隐寺", category="古迹", ticket_price=45,
                               visit_duration=150, address="西湖区", location=_LOCATION),
                ],
                meals=[],
                route_plan=RoutePlan(total_distance_km=23.8, total_duration_minutes=60,
                                     summary="高德路线已生成，共 2 段"),
                estimated_cost=525,
            ),
        ],
        weather_info=[
            WeatherInfo(date="2026-09-30", day_weather="大雨", night_weather="中雨",
                        day_temp=29, night_temp=20, source="amap-live"),
            WeatherInfo(date="2026-10-01", day_weather="小雨", night_weather="阴",
                        day_temp=27, night_temp=19, source="amap-live"),
        ],
        budget=Budget(total=1045, requested_budget=3000, remaining_budget=1955,
                      status="within_budget", within_budget=True),
    )


def _request() -> TripPlanRequest:
    return TripPlanRequest(
        city="杭州",
        start_date="2026-09-30",
        end_date="2026-10-01",
        budget=3000,
        preferences=["自然风景"],
        transportation="公共交通",
        accommodation="舒适型酒店",
    )


class _StubLlmClient:
    def __init__(self, available: bool):
        self._available = available

    def is_available(self) -> bool:
        return self._available


class _StubAttractionAgent:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def search(self, request):
        from travel_assistant.models.attraction_search_result import AttractionSearchResult
        if self.error:
            raise self.error
        return AttractionSearchResult(
            summary="高德景点检索到 2 个候选",
            attractions=[Attraction(name="河坊街", category="街区", address="上城区", location=_LOCATION)],
        )


class _StubWeatherAgent:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def query(self, request):
        from travel_assistant.models.weather_query_result import WeatherQueryResult
        if self.error:
            raise self.error
        return WeatherQueryResult(
            summary="天气已取到 2 天",
            weather_info=[WeatherInfo(date="2026-10-02", day_weather="晴", day_temp=30,
                                      night_temp=21, source="amap-live")],
        )


class _StubHotelAgent:
    def __init__(self, error: Exception | None = None):
        self.error = error

    def recommend(self, request):
        from travel_assistant.models.hotel_search_result import HotelSearchResult
        if self.error:
            raise self.error
        return HotelSearchResult(
            summary="检索到 1 家候选酒店",
            hotels=[Hotel(name="西溪度假酒店", type="舒适型", price_range="400-600")],
        )


class _ScriptedGraph:
    """代替 create_agent 编译出的图：按脚本调用真工具，再返回一句最终回答。"""

    def __init__(self, tools, script, answer):
        self._tools = {tool.name: tool for tool in tools}
        self._script = script
        self._answer = answer

    def invoke(self, state):
        messages = list(state["messages"])
        for call_id, (name, args) in enumerate(self._script):
            output = self._tools[name].invoke(args)
            messages.append(AIMessage(content="", tool_calls=[
                {"name": name, "args": args, "id": f"call-{call_id}", "type": "tool_call"}]))
            messages.append(ToolMessage(content=output, name=name, tool_call_id=f"call-{call_id}"))
        messages.append(AIMessage(content=self._answer))
        return {"messages": messages}


def _agent(script, answer="第二天排了两个景点，通勤 60 分钟。", weather_error=None):
    agent = AskAgent(_StubLlmClient(True), _StubAttractionAgent(),
                     _StubWeatherAgent(weather_error), _StubHotelAgent())
    agent._get_agent = lambda: _ScriptedGraph(agent._tools, script, answer)
    return agent


def _ask_request(question="第二天为什么这么赶"):
    return AskRequest(question=question, trip_plan=_trip_plan(), request=_request())


def _names(traces):
    return [trace.agent_name for trace in traces]


def _inputs(traces, tool_name):
    return [trace.input_summary for trace in traces if trace.agent_name == tool_name]


def test_shouldRefuseAskWhenNoModelAvailable():
    agent = AskAgent(_StubLlmClient(False), _StubAttractionAgent(), _StubWeatherAgent(), _StubHotelAgent())

    with pytest.raises(TripPlanningException) as error:
        agent.ask(_ask_request())

    assert str(error.value) == "追问能力需要可用模型"


def test_shouldOnlyReadPlanAndRecordOneToolTraceWhenOverviewIsEnough():
    response = _agent([("get_plan_overview", {})]).ask(_ask_request())

    assert response.intent == "answer"
    assert response.answer == "第二天排了两个景点，通勤 60 分钟。"
    assert _names(response.traces) == ["get_plan_overview", "AskAgent"]
    overview = response.traces[0].output_summary
    assert "共 2 天" in overview and "结余 1955 元" in overview
    assert "2026-10-01" in overview


def test_shouldServeNextDayOnEachCallWhenModelKeepsAsking():
    response = _agent([("get_day_plan", {}), ("get_day_plan", {})]).ask(_ask_request())

    assert _inputs(response.traces, "get_day_plan") == ["day_index=0", "day_index=1"]
    first, second = [t.output_summary for t in response.traces if t.agent_name == "get_day_plan"]
    assert "杭州西湖风景名胜区" in first and "西溪国家湿地公园" in second
    assert "大雨" in first and "小雨" in second


def test_shouldListPlannedAttractionsAndNotGoOnlineWhenNameMissesPlan():
    response = _agent([("get_attraction_detail", {"attraction_name": "故宫"})]).ask(
        _ask_request("故宫门票多少"))

    trace = response.traces[0]
    assert trace.status == "not_found"
    assert "方案里没有叫「故宫」的景点" in trace.output_summary
    assert "杭州西湖风景名胜区" in trace.output_summary


def test_shouldStillAnswerAndMarkFailedTraceWhenWeatherSourceBreaks():
    agent = _agent([("query_weather", {})], answer="实时天气这次没查到，方案里记的是小雨。",
                   weather_error=TripPlanningException("高德天气检索失败：CUQPS_HAS_EXCEEDED_THE_LIMIT（infocode=10021）"))

    response = agent.ask(_ask_request("第二天真实天气如何"))

    trace = response.traces[0]
    assert trace.status == "failed"
    assert "CUQPS_HAS_EXCEEDED_THE_LIMIT" in trace.output_summary
    assert response.answer.startswith("实时天气这次没查到")


def test_shouldReturnValidationErrorWhenQuestionIsBlank():
    payload = _ask_request().model_dump(by_alias=True)
    payload["question"] = "   "

    class _NeverAsk:
        def ask(self, request):
            raise AssertionError("校验未通过时不应进入追问逻辑")

    app.dependency_overrides[get_ask_agent] = lambda: _NeverAsk()
    try:
        response = TestClient(app).post("/api/trips/ask", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "question: must not be blank" in body["details"]
