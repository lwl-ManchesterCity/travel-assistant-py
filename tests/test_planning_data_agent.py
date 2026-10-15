"""取数链路两条路径的断言：有模型走 create_agent 自选工具，没模型退回固定顺序。

没模型时走的是 attraction -> weather -> hotel 的直白顺序调用，任一 agent 抛异常就直接
逃出这一串调用。模型可用时改成模型自选工具，模型不可用时必须退回那个顺序、
并且保持同样的失败即停语义，这里逐条钉住。
"""

import pytest

from travel_assistant.agent.data_tools import build_planning_tools, raise_planning_errors
from travel_assistant.agent.orchestration import PlanningDataAgent
from travel_assistant.agent.planning_context import PlanningContext
from travel_assistant.exceptions import TripPlanningException
from travel_assistant.models.trip_plan_request import TripPlanRequest


class _StubLlmClient:

    def __init__(self, available: bool):
        self._available = available

    def is_available(self) -> bool:
        return self._available


class _RecordingAgent:
    """三个数据 Agent 的替身：只记调用顺序，并返回带 summary 的结果对象。"""

    def __init__(self, name: str, calls: list[str], result, error: Exception | None = None):
        self.name = name
        self.calls = calls
        self._result = result
        self._error = error

    def run(self, request: TripPlanRequest):
        self.calls.append(self.name)
        if self._error is not None:
            raise self._error
        return self._result


class _StubResult:

    def __init__(self, summary: str):
        self.summary = summary


def _request() -> TripPlanRequest:
    return TripPlanRequest(
        city="杭州",
        start_date="2026-09-30",
        end_date="2026-10-02",
        budget=3000,
        preferences=["自然风景"],
        accommodation="舒适型酒店",
    )


def _agent(available: bool, calls: list[str], attraction_error: Exception | None = None) -> PlanningDataAgent:
    attraction = _RecordingAgent("attraction", calls, _StubResult("景点摘要"), attraction_error)
    weather = _RecordingAgent("weather", calls, _StubResult("天气摘要"))
    hotel = _RecordingAgent("hotel", calls, _StubResult("酒店摘要"))
    attraction.search = attraction.run
    weather.query = weather.run
    hotel.recommend = hotel.run
    return PlanningDataAgent(_StubLlmClient(available), build_planning_tools(attraction, weather, hotel))


def test_shouldCollectInFixedOrderWhenLlmIsUnavailable():
    calls: list[str] = []
    agent = _agent(available=False, calls=calls)
    context = PlanningContext(request=_request())

    agent.collect(context)

    assert calls == ["attraction", "weather", "hotel"]
    assert [context.attraction_result.summary, context.weather_result.summary, context.hotel_result.summary] == [
        "景点摘要", "天气摘要", "酒店摘要",
    ]
    assert context.errors == []


def test_shouldStopAtFirstFailureAndRaiseSameMessageWhenAttractionFails():
    calls: list[str] = []
    agent = _agent(
        available=False,
        calls=calls,
        attraction_error=TripPlanningException("当前条件下没有找到可用景点，建议更换目的地或调整偏好"),
    )
    context = PlanningContext(request=_request())

    agent.collect(context)

    assert calls == ["attraction"]
    with pytest.raises(TripPlanningException) as error:
        raise_planning_errors(context)
    assert str(error.value) == "当前条件下没有找到可用景点，建议更换目的地或调整偏好"


def test_shouldKeepUsingCreateAgentWhenLlmIsAvailable():
    calls: list[str] = []
    agent = _agent(available=True, calls=calls)
    invoked: list[dict] = []

    class _StubGraph:

        def invoke(self, payload: dict) -> dict:
            invoked.append(payload)
            return payload

    agent._get_agent = lambda: _StubGraph()
    context = PlanningContext(request=_request())

    agent.collect(context)

    assert calls == []
    assert len(invoked) == 1
    assert "杭州" in invoked[0]["messages"][0].content
