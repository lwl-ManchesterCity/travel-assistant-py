"""方案 A + B 追问 agent 的行为断言。

不接真实模型：用一个"按脚本调工具"的假 graph 顶掉 create_agent，
这样跑的是真工具、真上下文、真留痕，只有模型那一层是假的。
Java 侧没有对应物（/api/trips/ask 是移植基线之外的扩展），期望值取自
docs/ASK_AGENT_DESIGN.md 与 docs/ADJUST_AGENT_DESIGN.md。
"""

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from travel_assistant.agent.ask_agent import AskAgent
from travel_assistant.api.auth_dependency import require_current_user
from travel_assistant.config.container import get_ask_agent
from travel_assistant.exceptions import TripPlanningException
from travel_assistant.main import app
from travel_assistant.models.ask_request import AskRequest, AskTurn
from travel_assistant.models.auth import AuthUser
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


@pytest.fixture(autouse=True)
def _without_login_gate(without_login_gate):
    """本文件里那几条走 HTTP 的用例测的是**追问信封**，不是登录，
    所以把门禁摘掉，免得每条都先注册一个用户。门禁本身由
    tests/test_auth_controller.py 守——这个 autouse 只作用于本文件。
    """
    yield


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


def _request(city: str = "杭州") -> TripPlanRequest:
    return TripPlanRequest(
        city=city,
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


def _answer_json(text: str) -> str:
    """模型按约定的最后一轮输出格式回的一句只是回答。"""
    return '{"intent":"answer","answer":"%s"}' % text


def _agent(script, answer=None, weather_error=None):
    agent = AskAgent(_StubLlmClient(True), _StubAttractionAgent(),
                     _StubWeatherAgent(weather_error), _StubHotelAgent())
    agent._get_agent = lambda: _ScriptedGraph(
        agent._tools, script, answer or _answer_json("第二天排了两个景点，通勤 60 分钟。"))
    return agent


def _ask_request(question="第二天为什么这么赶", city="杭州"):
    return AskRequest(question=question, trip_plan=_trip_plan(), request=_request(city))


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


def test_shouldCarryAdjustmentPromptAndAnchorHintWhenModelProposesAChange():
    raw = ('{"intent":"need_change","answer":"你是想把第二天换到西湖边住，对吗？'
           '确认后我按这个条件重新排。","adjustmentPrompt":"第二天住得离西湖近一点",'
           '"anchorHint":"西湖","anchorDay":1}')
    response = _agent([("get_day_plan", {"day_index": 1})], answer=raw).ask(
        _ask_request("第二天住得离西湖近一点"))

    assert response.intent == "need_change"
    assert response.adjustment_prompt == "第二天住得离西湖近一点"
    assert response.anchor_hint == "西湖"
    assert response.anchor_day == 1
    assert _names(response.traces)[-1] == "AskAgent"
    assert response.traces[-1].status == "pending"


def test_shouldFallBackToPlainAnswerWhenLastTurnIsNotTheAgreedJson():
    response = _agent([], answer="预算当然是 3000 元").ask(_ask_request("预算还剩多少"))

    assert response.intent == "answer"
    assert response.adjustment_prompt is None
    assert response.answer == "预算当然是 3000 元"
    fallback = [trace for trace in response.traces if trace.status == "fallback"]
    assert len(fallback) == 1
    assert "不是约定的 JSON" in fallback[0].output_summary


def test_shouldFallBackWhenIntentValueIsUnknown():
    response = _agent([], answer='{"intent":"modify","answer":"已帮你改好了"}').ask(
        _ask_request("预算改成2000"))

    assert response.intent == "answer"
    assert response.answer == "已帮你改好了"
    assert [trace for trace in response.traces if trace.status == "fallback"]


def test_shouldFallBackWhenNeedChangeCarriesNoAdjustmentPrompt():
    response = _agent([], answer='{"intent":"need_change","answer":"可以改"}').ask(
        _ask_request("预算改成2000"))

    assert response.intent == "answer"
    assert response.adjustment_prompt is None
    assert "说要改但没给出改哪句" in [t.output_summary for t in response.traces
                                    if t.status == "fallback"][-1]


def test_shouldNotTouchAnyPlanningCodeWhenProposingAChange():
    """B 的护栏：判成要改也只是产一句文本，追问 agent 手上没有任何写动作。"""
    agent = _agent([], answer='{"intent":"need_change","answer":"确认后再重排",'
                              '"adjustmentPrompt":"预算改成2000"}')
    response = agent.ask(_ask_request("预算改成2000"))

    assert response.intent == "need_change"
    assert set(response.model_dump(by_alias=True)) == {
        "answer", "intent", "adjustmentPrompt", "anchorHint", "anchorDay",
        "pinHints", "pinDay", "recordedPreferences", "traces"}
    assert all(tool.name in {"get_plan_overview", "get_day_plan", "get_attraction_detail",
                             "search_attractions", "search_attractions_by_tag",
                             "query_weather", "recommend_hotels"}
               for tool in agent._tools)


def test_shouldProduceAPromptTheExistingAdjustRulePathCanApply():
    """B 的归一化句必须落在下游真做得到的维度上：直接喂现成的规则解析，看它认不认。"""
    from travel_assistant.agent.trip_adjustment_agent import TripAdjustmentAgent
    from travel_assistant.service.request_optimization_strategy_service import (
        RequestOptimizationStrategyService,
    )

    agent = _agent([], answer='{"intent":"need_change","answer":"确认后再重排",'
                              '"adjustmentPrompt":"预算改成2000，住经济型酒店"}')
    prompt = agent.ask(_ask_request("预算改成2000，住经济型")).adjustment_prompt

    decision = TripAdjustmentAgent(_StubLlmClient(False),
                                   RequestOptimizationStrategyService()).adjust(_request(), prompt)

    assert decision.updated_request.budget == 2000
    assert decision.updated_request.accommodation == "经济型酒店"
    assert len(decision.applied_changes) >= 2


def test_shouldExposeTheNewAskFieldsInCamelCaseOverHttp():
    """前端按驼峰读这三个新格子，响应体的键名必须一字不差。"""
    from travel_assistant.models.ask_response import AskResponse

    class _Proposer:
        def ask(self, request, user_id=None):
            return AskResponse(answer="确认后再重排", intent="need_change",
                               adjustment_prompt="第二天住得离西湖近一点",
                               anchor_hint="西湖", anchor_day=1, traces=[])

    app.dependency_overrides[get_ask_agent] = lambda: _Proposer()
    try:
        body = TestClient(app).post("/api/trips/ask", json=_ask_request().model_dump(by_alias=True)).json()
    finally:
        app.dependency_overrides.clear()

    assert body["intent"] == "need_change"
    assert body["adjustmentPrompt"] == "第二天住得离西湖近一点"
    assert body["anchorHint"] == "西湖"
    assert body["anchorDay"] == 1
    assert "adjustment_prompt" not in body


def test_shouldReturnValidationErrorWhenQuestionIsBlank():
    payload = _ask_request().model_dump(by_alias=True)
    payload["question"] = "   "

    class _NeverAsk:
        def ask(self, request, user_id=None):
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


def test_shouldRejectEmptyBodyWithValidationErrors():
    class _NeverAsk:
        def ask(self, request, user_id=None):
            raise AssertionError("空请求体应在校验层就被挡掉")

    app.dependency_overrides[get_ask_agent] = lambda: _NeverAsk()
    try:
        response = TestClient(app).post("/api/trips/ask", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "VALIDATION_ERROR"
    assert "question: must not be blank" in body["details"]
    assert "tripPlan: must not be null" in body["details"]
    assert "request: must not be null" in body["details"]


# ---------- 本地景点库工具 search_attractions_by_tag ----------
#
# 这组守的是"接上本地检索之后，模型不会拿到一个自信的错误答案"。
# 真检索不打桩会去读 data/chroma（gitignore 的产物，CI 上没有），
# 所以这里把 indexed_cities / search_kb_attractions 换掉：
# 跑的是真工具、真上下文、真留痕，只有数据源是假的。

_KB_ROW = {
    "id": "hz-001",
    "name": "浙江省博物馆(孤山馆区)",
    "tags": ["室内", "博物馆", "免费", "亲子"],
    "category": "人文历史",
    "ticket_price": 0,
    "hours": "09:00-17:00",
    "closed_days": ["周一"],
    "description": "全程室内参观，不受天气影响，适合雨天。",
    "score": 0.6169,
}


def _kb_agent(monkeypatch, script, cities=("杭州",), rows=None, boom=None):
    from travel_assistant.agent import ask_tools

    def fake_cities(index_dir=None):
        if boom is not None:
            raise boom
        return list(cities)

    monkeypatch.setattr(ask_tools, "indexed_cities", fake_cities)
    monkeypatch.setattr(ask_tools, "search_kb_attractions", lambda **kwargs: list(rows if rows is not None else [_KB_ROW]))
    return _agent(script)


def _kb_trace(response):
    traces = [t for t in response.traces if t.agent_name == "search_attractions_by_tag"]
    assert traces, f"没有记到本地库那一笔留痕：{_names(response.traces)}"
    return traces[0]


def test_shouldSayLocalKbHasNoSuchCityInsteadOfReturningHangzhouRows(monkeypatch):
    """非杭州：要照实说库里没有，**不许**把杭州的景点当广州的答出去。"""
    agent = _kb_agent(monkeypatch, [("search_attractions_by_tag", {"tags": ["室内"]})])

    response = agent.ask(_ask_request("广州下雨天去哪", city="广州"))

    trace = _kb_trace(response)
    assert trace.status == "not_found"
    assert "杭州" in trace.output_summary and "广州" in trace.output_summary
    assert "浙江省博物馆" not in trace.output_summary


def test_shouldRejectTagThatIsNotInTheVocabulary(monkeypatch):
    """「山」是上一轮删掉的空词。模型挑到它时要被当面拒绝，而不是筛出 0 条让模型以为"库里没有"。"""
    agent = _kb_agent(monkeypatch, [("search_attractions_by_tag", {"tags": ["山"]})])

    response = agent.ask(_ask_request("杭州哪里能爬山"))

    trace = _kb_trace(response)
    assert trace.status == "failed"
    assert "不在可选词表" in trace.output_summary
    assert "山" in trace.output_summary


def test_shouldSurviveWhenLocalIndexIsNotBuilt(monkeypatch):
    """索引是 gitignore 的产物：没建时要把话说清楚，不能让栈冒到模型面前。"""
    from travel_assistant.kb import IndexNotBuilt

    agent = _kb_agent(monkeypatch, [("search_attractions_by_tag", {"tags": ["室内"]})],
                      boom=IndexNotBuilt("索引还没建：/nowhere/chroma"))

    response = agent.ask(_ask_request("杭州下雨天去哪"))

    trace = _kb_trace(response)
    assert trace.status == "failed"
    assert "还没建好" in trace.output_summary


def test_shouldRenderKbPriceWithoutCallingItAnEstimate(monkeypatch):
    """本地库的门票是 KB 自己查证的（0=确定免费），与方案里那套「按类别估算」不是一个来源。"""
    agent = _kb_agent(monkeypatch, [("search_attractions_by_tag", {"tags": ["室内"]})])

    response = agent.ask(_ask_request("杭州下雨天去哪"))

    output = _kb_trace(response).output_summary
    assert "浙江省博物馆" in output
    assert "免费" in output
    assert "估算" not in output, "本地库的票价不许说成估算"


def test_shouldSayNothingMatchedInsteadOfReturningAnEmptyBlob(monkeypatch):
    agent = _kb_agent(monkeypatch, [("search_attractions_by_tag", {"tags": ["需预约"]})], rows=[])

    response = agent.ask(_ask_request("杭州有什么要预约的"))

    assert "没有命中的景点" in _kb_trace(response).output_summary


def test_shouldPutVocabularyAndScopeIntoTheToolDescription():
    """词表是现读的，不是抄的常量；范围写死在说明里，好让模型别去调明显答不了的城市。"""
    from travel_assistant.kb import KB_CITIES, vocabulary_words

    description = {tool.name: tool.description for tool in _agent([])._tools}[
        "search_attractions_by_tag"
    ]
    for word in vocabulary_words():
        assert word in description, f"工具说明里漏了词表里的「{word}」"
    for city in KB_CITIES:
        assert city in description
    assert "不许自创" in description


def test_shouldKeepTheLocalToolApartFromTheOnlineOne():
    """本地库那个不能和联网那个撞名，否则模型没法选。"""
    names = [tool.name for tool in _agent([])._tools]

    assert "search_attractions" in names
    assert "search_attractions_by_tag" in names
    assert len(names) == len(set(names))


# ---------- 多轮：历史对话进上下文 ----------
#
# 守的是 docs/ASK_MULTITURN_MEMORY.md。三个要点：历史拼在当前这句**前面**、
# 窗口与字符两道闸门、以及**不传 history 时一字不变**。

class _RecordingGraph(_ScriptedGraph):
    """多记一笔：把 invoke 收到的 messages 原样留下来给断言看。"""

    def __init__(self, tools, script, answer):
        super().__init__(tools, script, answer)
        self.seen = []

    def invoke(self, state):
        self.seen = list(state["messages"])
        return super().invoke(state)


def _history_agent(answer=None):
    agent = AskAgent(_StubLlmClient(True), _StubAttractionAgent(),
                     _StubWeatherAgent(), _StubHotelAgent())
    graph = _RecordingGraph(agent._tools, [], answer or _answer_json("答。"))
    agent._get_agent = lambda: graph
    return agent, graph


def _turn_pairs(count, human_prefix="问", assistant_prefix="答"):
    """造 count 轮成对的历史。"""
    turns = []
    for index in range(count):
        turns.append(AskTurn(role="user", content=f"{human_prefix}{index}"))
        turns.append(AskTurn(role="assistant", content=f"{assistant_prefix}{index}"))
    return turns


def test_shouldPutHistoryBeforeTheCurrentQuestion():
    """历史在前、当前这句在后——模型的 messages 列表就是这个形状。"""
    agent, graph = _history_agent()
    request = _ask_request("那它几点关门")
    request.history = [AskTurn(role="user", content="西溪湿地要门票吗"),
                       AskTurn(role="assistant", content="西溪湿地门票 80 元，开放 07:30-17:30")]

    agent.ask(request)

    assert [(type(m).__name__, m.content) for m in graph.seen] == [
        ("HumanMessage", "西溪湿地要门票吗"),
        ("AIMessage", "西溪湿地门票 80 元，开放 07:30-17:30"),
        ("HumanMessage", "那它几点关门"),
    ]


def test_shouldKeepSingleTurnBehaviourWhenHistoryIsAbsent():
    """判据 3：不传 history 就是今天的样子——一条 HumanMessage，不多不少。"""
    agent, graph = _history_agent()

    agent.ask(_ask_request("第二天为什么这么赶"))

    assert [(type(m).__name__, m.content) for m in graph.seen] == [
        ("HumanMessage", "第二天为什么这么赶"),
    ]


def test_shouldKeepOnlyTheMostRecentSixTurns():
    """判据 4：十轮历史只取最近六轮，且**成对**——不出现孤立的 Human 或 AI。"""
    agent, graph = _history_agent()
    request = _ask_request()
    request.history = _turn_pairs(10)

    agent.ask(request)

    roles = [type(m).__name__ for m in graph.seen]
    assert roles == ["HumanMessage", "AIMessage"] * 6 + ["HumanMessage"]
    assert graph.seen[0].content == "问4", "取的该是最近六轮：问4 到 问9"
    assert graph.seen[11].content == "答9"


def test_shouldDropTheTrailingUserTurnThatHasNoAnswer():
    """末尾孤立的 Human 先摘掉。留着它，从末尾配对第一步就配不上，**整段**历史会一起丢。"""
    agent, graph = _history_agent()
    request = _ask_request()
    request.history = _turn_pairs(2) + [AskTurn(role="user", content="还没答完的那句")]

    agent.ask(request)

    assert [m.content for m in graph.seen] == ["问0", "答0", "问1", "答1", "第二天为什么这么赶"]


def test_shouldDropALeadingAssistantTurnThatHasNoQuestion():
    """开头的孤立 AI 同理，它是配对剩下的边角料。"""
    agent, graph = _history_agent()
    request = _ask_request()
    request.history = [AskTurn(role="assistant", content="没头没脑的一句")] + _turn_pairs(1)

    agent.ask(request)

    assert [m.content for m in graph.seen] == ["问0", "答0", "第二天为什么这么赶"]


def test_shouldTrimHistoryByCharacterBudget():
    """判据 4 的另一半：8000 字符兜底，超了就**整对**丢掉。"""
    agent, graph = _history_agent()
    request = _ask_request()
    # 每轮 2000 字 → 一对 4000 字 → 8000 的预算只放得下两对（单条上限也是 2000，正好不触发）。
    request.history = []
    for _ in range(4):
        request.history.append(AskTurn(role="user", content="甲" * 2000))
        request.history.append(AskTurn(role="assistant", content="乙" * 2000))

    agent.ask(request)

    assert len(graph.seen) == 5, "两对历史 + 当前这句"
    assert [type(m).__name__ for m in graph.seen] == [
        "HumanMessage", "AIMessage", "HumanMessage", "AIMessage", "HumanMessage"]


def test_shouldAlwaysKeepTheMostRecentPairEvenWhenItAloneBlowsTheBudget():
    """最近这一对无论多大都要留着：丢了它，「那它几点关门」里的「它」就没有指代对象了。"""
    agent, graph = _history_agent()
    request = _ask_request()
    request.history = [
        AskTurn(role="user", content="很久以前的一句"),
        AskTurn(role="assistant", content="很久以前的答"),
        AskTurn(role="user", content="甲" * 5000),
        AskTurn(role="assistant", content="乙" * 5000),
    ]

    agent.ask(request)

    assert [m.content for m in graph.seen][:2] == ["甲" * 2000, "乙" * 2000]


def test_shouldDropMalformedHistoryEntriesInsteadOfRejectingTheWholeRequest():
    """history 是可选字段，一个坏元素不该把整次追问打成 400——用户问的这句没问题。"""
    request = AskRequest.model_validate({
        "question": "那它几点关门",
        "tripPlan": _trip_plan().model_dump(by_alias=True),
        "request": _request().model_dump(by_alias=True),
        "history": [
            {"role": "user", "content": "好的"},
            {"role": "assistant", "content": "西溪湿地门票 80 元"},
            {"role": "system", "content": "角色不认识"},
            {"role": "user", "content": "   "},
            "整条不是对象",
            {"role": "USER", "content": "大小写要认"},
        ],
    })

    assert [(t.role, t.content) for t in request.history] == [
        ("user", "好的"),
        ("assistant", "西溪湿地门票 80 元"),
        ("user", "大小写要认"),
    ]


def test_shouldTreatNonListHistoryAsEmptyInsteadOfCrashing():
    request = AskRequest.model_validate({
        "question": "问",
        "tripPlan": _trip_plan().model_dump(by_alias=True),
        "request": _request().model_dump(by_alias=True),
        "history": "一万轮",
    })

    assert request.history == []


# ---------- 城市锁：search_attractions_by_tag 的 city 参数 ----------

def _city_agent(monkeypatch, script, cities=("杭州", "北京", "上海", "广州")):
    """返回 (agent, seen)：seen 里是最后一次检索实际传下去的参数。"""
    from travel_assistant.agent import ask_tools

    seen = {}
    monkeypatch.setattr(ask_tools, "indexed_cities", lambda index_dir=None: list(cities))

    def fake_search(**kwargs):
        seen.update(kwargs)
        return [_KB_ROW]

    monkeypatch.setattr(ask_tools, "search_kb_attractions", fake_search)
    return _agent(script), seen


def test_shouldSearchTheCityTheModelAsksForNotThePlanCity(monkeypatch):
    """北京方案问「广州塔门票多少」：模型把 city 填成广州，检索就要按广州走。"""
    agent, seen = _city_agent(
        monkeypatch,
        [("search_attractions_by_tag", {"attraction_name": "广州塔", "city": "广州"})])

    agent.ask(_ask_request("广州塔门票多少钱", city="北京"))

    assert seen["city"] == "广州"
    assert seen["query"] == "广州塔"


def test_shouldFallBackToThePlanCityWhenModelLeavesCityEmpty(monkeypatch):
    """不填 city 就是改动前的老行为，一个字不变。"""
    agent, seen = _city_agent(
        monkeypatch, [("search_attractions_by_tag", {"tags": ["室内"]})])

    agent.ask(_ask_request("杭州下雨天去哪", city="杭州"))

    assert seen["city"] == "杭州"


def test_shouldSayTheLibraryHasNoSuchCityWhenModelAsksForOneWeDoNotHave(monkeypatch):
    """填了一个库里没有的城市：照实说查不了，不许把杭州的景点当巴黎的答出去。"""
    agent, seen = _city_agent(
        monkeypatch,
        [("search_attractions_by_tag", {"attraction_name": "埃菲尔铁塔", "city": "巴黎"})],
        cities=("杭州",))

    trace = _kb_trace(agent.ask(_ask_request("埃菲尔铁塔门票多少", city="杭州")))

    assert trace.status == "not_found"
    assert "巴黎" in trace.output_summary
    assert "浙江省博物馆" not in trace.output_summary
    assert seen == {}, "城市就不在库里，不该往下走去检索"


def test_shouldCarryHistoryFromTheRequestBodyIntoTheAgent():
    """前端发的是驼峰 history，字段名与元素形状要一字不差地到 agent 手里。"""
    from travel_assistant.models.ask_response import AskResponse

    captured = {}

    class _Capture:
        def ask(self, request, user_id=None):
            captured["history"] = request.history
            return AskResponse(answer="答。", intent="answer", traces=[])

    app.dependency_overrides[get_ask_agent] = lambda: _Capture()
    try:
        payload = _ask_request().model_dump(by_alias=True)
        payload["history"] = [
            {"role": "user", "content": "西溪湿地要门票吗"},
            {"role": "assistant", "content": "80 元"},
        ]
        response = TestClient(app).post("/api/trips/ask", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert [(turn.role, turn.content) for turn in captured["history"]] == [
        ("user", "西溪湿地要门票吗"), ("assistant", "80 元")]


def test_shouldStillAcceptARequestWithoutHistory():
    """判据 3 的接口面：history 不传，老前端一行不改也照跑。"""
    from travel_assistant.models.ask_response import AskResponse

    captured = {}

    class _Capture:
        def ask(self, request, user_id=None):
            captured["history"] = request.history
            return AskResponse(answer="答。", intent="answer", traces=[])

    app.dependency_overrides[get_ask_agent] = lambda: _Capture()
    try:
        response = TestClient(app).post("/api/trips/ask",
                                        json=_ask_request().model_dump(by_alias=True))
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert captured["history"] is None


# ---------- 第二档：记偏好与注入（docs/ASK_LONGTERM_MEMORY.md §2.2）----------
#
# 守两件事：信封里那一格 preferences 进不进库、以及偏好有没有真的顶到提示词最前。
# 判据 1/2/3/4 的单元面在这里，端到端那面归 /tmp/preference_probe.py。

def _preference_agent(store, preferences, script=None, answer=None):
    """带长期记忆的 agent。偏好从 store 里读，记也记回同一个 store。"""
    from travel_assistant.service.preference_service import PreferenceService

    service = PreferenceService(store)
    agent = AskAgent(_StubLlmClient(True), _StubAttractionAgent(),
                     _StubWeatherAgent(), _StubHotelAgent(), service)
    graph = _RecordingGraph(agent._tools, script or [], answer or _answer_json("答。"))
    agent._get_agent = lambda: graph
    return agent, graph


def _answer_with_preferences(preferences):
    import json
    return json.dumps({"intent": "answer", "answer": "好。", "preferences": preferences},
                      ensure_ascii=False)


def test_shouldRecordThePreferencesFromTheEnvelope():
    """判据 1 的单元面：信封里给了就落库，来源是 chat。"""
    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    agent, _ = _preference_agent(store, [], answer=_answer_with_preferences(["不能吃辣"]))

    response = agent.ask(_ask_request(), user_id=7)

    assert response.recorded_preferences == ["不能吃辣"]
    assert [(row.content, row.source) for row in store.list_for_user(7)] == [
        ("不能吃辣", "chat")]


def test_shouldRecordNothingWhenTheEnvelopeSaysSo():
    """判据 2/3 的单元面：「想去看升旗」「你好」都该是空数组，库里一条不多。"""
    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    agent, _ = _preference_agent(store, [], answer=_answer_with_preferences([]))

    response = agent.ask(_ask_request(question="你好"), user_id=7)

    assert response.recorded_preferences is None
    assert store.list_for_user(7) == []


def test_shouldNotReportTheSamePreferenceTwice():
    """判据 4：再说一遍，库里仍然一条，且**不再弹**「已记住」。

    每次都弹会变成噪音，用户会开始无视它——那比不显示更糟（§2.2.7）。
    """
    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    agent, _ = _preference_agent(store, [], answer=_answer_with_preferences(["不能吃辣"]))
    agent.ask(_ask_request(), user_id=7)

    second = agent.ask(_ask_request(), user_id=7)

    assert second.recorded_preferences is None
    assert len(store.list_for_user(7)) == 1


def test_shouldTakeAtMostFivePreferencesFromOneAnswer():
    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    agent, _ = _preference_agent(
        store, [], answer=_answer_with_preferences([f"偏好{i}" for i in range(6)]))

    response = agent.ask(_ask_request(), user_id=7)

    assert len(response.recorded_preferences) == 5
    assert len(store.list_for_user(7)) == 5


def test_shouldNotRecordAnythingWithoutAUserId():
    """没登录 / 没认出来人时不写。这条链不该凭一个空 id 往库里塞东西。"""
    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    agent, _ = _preference_agent(store, [], answer=_answer_with_preferences(["不能吃辣"]))

    response = agent.ask(_ask_request())

    assert response.recorded_preferences is None
    assert store.list_for_user(7) == []


def test_shouldStillWorkWhenThereIsNoPreferenceServiceAtAll():
    """老构造方式（四个参数）行为不变：不记也不注入。"""
    agent = _agent([], answer=_answer_with_preferences(["不能吃辣"]))

    response = agent.ask(_ask_request(), user_id=7)

    assert response.recorded_preferences is None
    assert response.answer == "好。"


# ---------- 注入 ----------


def test_shouldPutTheLongTermPreferencesAtTheVeryFrontAsASystemMessage():
    """§2.2.5 判据：偏好是一条 SystemMessage **顶在最前**。

    不塞历史（历史可以被忽略、而且有窗口会被裁掉）、也不改 create_agent 的
    system_prompt（那是固化的，按请求重建 agent 等于每轮重编译一遍图）。
    """
    from langchain_core.messages import SystemMessage

    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    store.add(7, "不能吃辣", "chat")
    agent, graph = _preference_agent(store, [])

    agent.ask(_ask_request(), user_id=7)

    assert isinstance(graph.seen[0], SystemMessage)
    assert "不能吃辣" in graph.seen[0].content
    # 顶在最前，历史与当前这句都排在它后面。
    assert isinstance(graph.seen[-1], HumanMessage)
    assert graph.seen[-1].content == "第二天为什么这么赶"


def test_shouldNotInjectAnythingForANewUser():
    """一条偏好都没有时不加那条系统消息——默认链路的输入要与从前逐字一致。"""
    from langchain_core.messages import SystemMessage

    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    agent, graph = _preference_agent(InMemoryPreferenceStore(), [])

    agent.ask(_ask_request(), user_id=7)

    assert not any(isinstance(message, SystemMessage) for message in graph.seen)
    assert len(graph.seen) == 1


def test_shouldNotInjectWhenThereIsNoUserId():
    from langchain_core.messages import SystemMessage

    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    store.add(7, "不能吃辣", "chat")
    agent, graph = _preference_agent(store, [])

    agent.ask(_ask_request())

    assert not any(isinstance(message, SystemMessage) for message in graph.seen)


def test_shouldKeepTheInjectedBlockAheadOfTheHistory():
    """有历史时，偏好仍在历史之前——它不能跟着历史一起被窗口裁掉。"""
    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    store = InMemoryPreferenceStore()
    store.add(7, "不能吃辣", "chat")
    agent, graph = _preference_agent(store, [])

    # history 给 **dict**（线上那种形状）。给 AskTurn 对象的话会被
    # _drop_malformed_turns 静默丢掉——它只认 dict，而丢掉的代价就是"少一点上下文"，
    # 测试里表现为"历史压根没进来"，很难看出是构造方式的问题。
    history = [{"role": turn.role, "content": turn.content} for turn in _turn_pairs(2)]
    agent.ask(AskRequest(question="那它几点关门", trip_plan=_trip_plan(),
                         request=_request(), history=history), user_id=7)

    assert "不能吃辣" in graph.seen[0].content
    assert isinstance(graph.seen[1], HumanMessage)
    assert graph.seen[1].content == "问0"


def test_shouldKeepWorkingWhenThePreferenceStoreBlowsUp():
    """读偏好炸了不该让整轮追问挂掉：记偏好是搭便车的乘客（§2.2.4）。"""

    class _BrokenStore:
        def list_for_user(self, user_id):
            raise RuntimeError("库连不上")

        def add(self, user_id, content, source):
            raise RuntimeError("库连不上")

    from travel_assistant.service.preference_service import PreferenceService

    agent = AskAgent(_StubLlmClient(True), _StubAttractionAgent(),
                     _StubWeatherAgent(), _StubHotelAgent(), PreferenceService(_BrokenStore()))
    agent._get_agent = lambda: _ScriptedGraph(
        agent._tools, [], _answer_with_preferences(["不能吃辣"]))

    response = agent.ask(_ask_request(), user_id=7)

    assert response.answer == "好。"
    assert response.recorded_preferences is None


# ---------- 第四档：场景（docs/ASK_LONGTERM_MEMORY.md §2.4）----------
#
# 与上面第二档那几条同一个分法：这里守的是**管道**（信封里那一格进不进库、
# 按城市取回来的有没有顶到提示词最前），模型判得对不对归探针。
# 判据 2/3（换城市 0 条、同城市 1 条）的单元面在「注入与留痕」那两条。

def _episode_agent(episode_store, script=None, answer=None):
    """只带场景记忆的 agent（偏好不接，免得两条链混在一起看不出是谁的功劳）。"""
    from travel_assistant.service.episode_service import EpisodeService

    agent = AskAgent(_StubLlmClient(True), _StubAttractionAgent(),
                     _StubWeatherAgent(), _StubHotelAgent(),
                     None, EpisodeService(episode_store))
    graph = _RecordingGraph(agent._tools, script or [], answer or _answer_json("答。"))
    agent._get_agent = lambda: graph
    return agent, graph


def _answer_with_episodes(episodes):
    import json
    return json.dumps({"intent": "answer", "answer": "好。", "episodes": episodes},
                      ensure_ascii=False)


def _ask_request_in_city(city: str, question: str = "第二天为什么这么赶") -> AskRequest:
    """**方案的城市**才是取场景的依据（§2.4.3），所以这里改的是 trip_plan.city，
    不是 request.city——两个都改只是为了让这份请求自洽。"""
    plan = _trip_plan().model_copy(update={"city": city})
    return AskRequest(question=question, trip_plan=plan, request=_request(city))


def test_shouldRecordTheEpisodesFromTheEnvelope():
    """判据 1 的单元面：信封里给了就落库，来源是 chat，城市原样存下。"""
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    agent, _ = _episode_agent(store, answer=_answer_with_episodes(
        [{"city": "杭州", "content": "上次那家酒店隔音差"}]))

    agent.ask(_ask_request(), user_id=7)

    assert [(row.city, row.content, row.source) for row in store.list_for_user(7)] == [
        ("杭州", "上次那家酒店隔音差", "chat")]


def test_shouldDropTheWholeEpisodeWhenTheCityIsMissing():
    """**没城市就整条丢**（§2.4.4）：按城市取，没城市永远捞不出来。"""
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    agent, _ = _episode_agent(store, answer=_answer_with_episodes([
        {"content": "上次那家酒店隔音差"},
        {"city": "", "content": "排队太久"},
        {"city": "杭州", "content": "上次那家酒店隔音差"},
    ]))

    agent.ask(_ask_request(), user_id=7)

    assert len(store.list_for_user(7)) == 1


def test_shouldNotRecordEpisodesWhenTheEnvelopeSaysSo():
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    agent, _ = _episode_agent(store, answer=_answer_with_episodes([]))

    agent.ask(_ask_request(question="你好"), user_id=7)

    assert store.list_for_user(7) == []


def test_shouldNotRecordEpisodesWithoutAUserId():
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    agent, _ = _episode_agent(store, answer=_answer_with_episodes(
        [{"city": "杭州", "content": "上次那家酒店隔音差"}]))

    agent.ask(_ask_request())

    assert store.list_for_user(7) == []


# ---------- 注入：按城市 ----------


def test_shouldInjectTheEpisodesOfTheCityWeAreIn():
    """判据 3 的单元面：方案是杭州，杭州那条就顶到最前。"""
    from langchain_core.messages import SystemMessage

    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    store.add(7, "杭州", "上次那家酒店隔音差", "chat")
    agent, graph = _episode_agent(store)

    agent.ask(_ask_request_in_city("杭州"), user_id=7)

    assert isinstance(graph.seen[0], SystemMessage)
    assert "上次那家酒店隔音差" in graph.seen[0].content


def test_shouldNotInjectTheEpisodesOfAnotherCity():
    """**判据 2 的单元面**：方案换成都，杭州那条**一个字都不该出现**。"""
    from langchain_core.messages import SystemMessage

    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    store.add(7, "杭州", "上次那家酒店隔音差", "chat")
    agent, graph = _episode_agent(store)

    agent.ask(_ask_request_in_city("成都"), user_id=7)

    assert not any(isinstance(message, SystemMessage) for message in graph.seen)
    assert not any("隔音差" in str(message.content) for message in graph.seen)


def test_shouldNotInjectAnythingForAUserWithNoEpisodes():
    """一条场景都没有时不加那条系统消息——没登录过的城市是常态。"""
    from langchain_core.messages import SystemMessage

    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    agent, graph = _episode_agent(InMemoryEpisodeStore())

    agent.ask(_ask_request_in_city("杭州"), user_id=7)

    assert not any(isinstance(message, SystemMessage) for message in graph.seen)


# ---------- 留痕：判据 2/3 唯一能自动化证伪 0 与 1 的地方 ----------


def test_shouldReportTheInjectedEpisodeCountInTheTrace():
    """**判据 3**：同城市 → 留痕里是 1 条。

    「注入了 N 条」是这一档唯一能自动化证明注入真的发生了的痕迹——不加这条，
    注入只能靠"答案看起来对"来判断，那不算判据（§2.2.9 判据 7 同一个理由）。
    """
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    store.add(7, "杭州", "上次那家酒店隔音差", "chat")
    agent, _ = _episode_agent(store)

    response = agent.ask(_ask_request_in_city("杭州"), user_id=7)

    trace = [t for t in response.traces if t.agent_name == "MemoryService"][0]
    assert "杭州 的场景：1 条" in trace.input_summary
    assert "上次那家酒店隔音差" in trace.output_summary


def test_shouldReportZeroInjectedEpisodesForAnotherCity():
    """**判据 2**：换城市 → 留痕里是 **0** 条。

    这一条与上面那条是一对：只验"存进去了"不算验，要验
    「该出现的时候出现、不该出现的时候不出现」（§2.4.7）。
    """
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    store = InMemoryEpisodeStore()
    store.add(7, "杭州", "上次那家酒店隔音差", "chat")
    agent, _ = _episode_agent(store)

    response = agent.ask(_ask_request_in_city("成都"), user_id=7)

    trace = [t for t in response.traces if t.agent_name == "MemoryService"][0]
    assert "成都 的场景：0 条" in trace.input_summary
    assert "隔音差" not in trace.output_summary


def test_shouldNotWriteAMemoryTraceWhenNobodyIsLoggedIn():
    """没登录时不写那条痕：那条链本来就碰不到记忆，多一条痕反而是噪音。"""
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore

    agent, _ = _episode_agent(InMemoryEpisodeStore())

    response = agent.ask(_ask_request_in_city("杭州"))

    assert [t for t in response.traces if t.agent_name == "MemoryService"] == []


def test_shouldKeepTheMemoryTraceOutOfTheStreamingEvents():
    """记忆留痕**不进 SSE**：注入不是一次工具调用，混进"工具调用进度条"里是噪音。
    它照旧进 AskResponse.traces（上一条用例验的就是它）。"""
    from travel_assistant.service.episode_service import EpisodeService
    from travel_assistant.clients.episode_store import InMemoryEpisodeStore
    from travel_assistant.service.preference_service import PreferenceService
    from travel_assistant.clients.preference_store import InMemoryPreferenceStore

    preference_store = InMemoryPreferenceStore()
    preference_store.add(7, "不能吃辣", "chat")
    episode_store = InMemoryEpisodeStore()
    episode_store.add(7, "杭州", "上次那家酒店隔音差", "chat")
    agent = AskAgent(_StubLlmClient(True), _StubAttractionAgent(),
                     _StubWeatherAgent(), _StubHotelAgent(),
                     PreferenceService(preference_store), EpisodeService(episode_store))
    agent._get_agent = lambda: _ScriptedGraph(agent._tools, [], _answer_json("答。"))

    events = []
    response = agent.ask(_ask_request_in_city("杭州"),
                         lambda kind, payload: events.append((kind, payload)), user_id=7)

    pushed = [name for name, _ in events]
    assert "trace" not in pushed          # 记忆不留痕进流
    assert [t.agent_name for t in response.traces if t.agent_name == "MemoryService"]
