"""对应 Java agent/TripAdjustmentAgent.java + service/TripPlanningService.adjustTrip 的行为断言。

Java 侧没有 adjust 相关测试类，期望文案全部取自源码：规则解析六个分支的 appliedChanges 文案、
LLM 解析各字段的 "LLM 已…" 前缀文案、解析失败时的 "LLM 解析失败，已自动回退到规则解析" 与
adjustmentSource=rule-fallback，以及 adjustTrip 追加的两句改计划备注。
"""

from travel_assistant.agent.trip_adjustment_agent import TripAdjustmentAgent
from travel_assistant.clients.llm import LlmClient
from travel_assistant.models.trip_adjustment_request import TripAdjustmentRequest
from travel_assistant.models.trip_plan import TripPlan
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.service.request_optimization_strategy_service import (
    RequestOptimizationStrategyService,
)
from travel_assistant.service.trip_planning_service import TripPlanningService


class _StubLlmClient(LlmClient):

    def __init__(self, available: bool, response: str = ""):
        self._available = available
        self._response = response
        self.prompts: list[tuple[str, str]] = []

    def is_available(self) -> bool:
        return self._available

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append((system_prompt, user_prompt))
        return self._response


def _agent(llm_client: LlmClient) -> TripAdjustmentAgent:
    return TripAdjustmentAgent(llm_client, RequestOptimizationStrategyService())


def _request(**overrides) -> TripPlanRequest:
    values = dict(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        budget=3000,
        preferences=["美食", "自然风景"],
        transportation="公共交通",
        accommodation="舒适型酒店",
    )
    values.update(overrides)
    return TripPlanRequest(**values)


def test_shouldParseBudgetAccommodationAndPreferenceByRulesWhenLlmUnavailable():
    agent = _agent(_StubLlmClient(available=False))

    decision = agent.adjust(_request(), "预算改成5000，不住舒适型酒店了，改经济型，不要自然风景")

    assert decision.adjustment_source == "rule"
    assert decision.applied_changes == [
        "已将预算调整为 5000 元",
        "已切换为经济型酒店偏好",
        "已移除偏好：自然风景",
    ]
    assert decision.updated_request.budget == 5000
    assert decision.updated_request.accommodation == "经济型酒店"
    assert decision.updated_request.preferences == ["美食"]
    assert decision.updated_request.transportation == "公共交通"


def test_shouldApplyCompressRouteStrategyWhenPromptMentionsCompactRoute():
    agent = _agent(_StubLlmClient(available=False))

    decision = agent.adjust(_request(), "改为打车出行，住民宿，路线紧凑一点")

    assert decision.applied_changes == [
        "已切换为打车为主",
        "已切换为民宿偏好",
        "已应用统一压缩路线策略",
        "已加入压缩路线约束",
    ]
    assert decision.updated_request.transportation == "打车为主"
    assert decision.updated_request.accommodation == "民宿"
    assert decision.updated_request.extra_requirements == (
        "尽量压缩跨区通勤，优先安排同片区景点；减少折返路线"
    )


def test_shouldKeepResidualRequirementWhenPromptIsNotAnAdjustmentInstruction():
    agent = _agent(_StubLlmClient(available=False))

    decision = agent.adjust(_request(), "祝旅途愉快")

    assert decision.applied_changes == ["已记录新的自然语言调整要求"]
    assert decision.updated_request.budget == 3000
    assert decision.updated_request.extra_requirements == "祝旅途愉快"


def test_shouldAppendFallbackNoteWhenPromptIsBlank():
    agent = _agent(_StubLlmClient(available=False))

    decision = agent.adjust(_request(), "   ")

    assert decision.adjustment_source == "rule"
    assert decision.applied_changes == ["已保留原始规划条件，并记录新的调整要求"]
    assert decision.updated_request == _request()


def test_shouldNotRecordBudgetChangeWhenParsedBudgetEqualsOriginal():
    agent = _agent(_StubLlmClient(available=False))

    decision = agent.adjust(_request(), "预算保持3000")

    assert decision.applied_changes == ["已保留原始规划条件，并记录新的调整要求"]
    assert decision.updated_request.budget == 3000


def test_shouldUseRuleBranchWhenPromptIsNull():
    agent = _agent(_StubLlmClient(available=False))

    decision = agent.adjust(_request(), None)

    assert decision.adjustment_source == "rule"
    assert decision.applied_changes == ["已保留原始规划条件，并记录新的调整要求"]


def test_shouldParseStructuredJsonWhenLlmAvailable():
    llm_client = _StubLlmClient(available=True, response=(
        '{"budget": 4200, "transportation": "智能推荐", "accommodation": "高端酒店",'
        ' "preferencesToAdd": ["人文历史", "美食"], "preferencesToRemove": ["自然风景"],'
        ' "extraRequirements": ["不吃海鲜", "改成公共交通"]}'
    ))
    agent = _agent(llm_client)

    decision = agent.adjust(
        _request(),
        "帮我把预算提到 4200，交通你来安排，住宿上高端，加人文历史去掉自然风景，另外不吃海鲜",
    )

    assert decision.adjustment_source == "llm"
    assert decision.applied_changes == [
        "LLM 已将预算调整为 4200 元",
        "LLM 已改为交通方式智能推荐",
        "LLM 已调整住宿偏好为 高端酒店",
        "LLM 已增加偏好：人文历史",
        "LLM 已移除偏好：自然风景",
        "LLM 已记录新的自然语言调整要求",
    ]
    assert decision.updated_request.budget == 4200
    assert decision.updated_request.transportation is None
    assert decision.updated_request.accommodation == "高端酒店"
    assert decision.updated_request.preferences == ["美食", "人文历史"]
    assert decision.updated_request.extra_requirements == "不吃海鲜"

    system_prompt, user_prompt = llm_client.prompts[0]
    assert system_prompt.startswith("你是旅行计划修改助手。")
    assert system_prompt.endswith('5. extraRequirements 用于保留补充限制，比如“不吃海鲜”“别太赶”“靠近地铁”\n')
    assert user_prompt.startswith("原始请求：\n")
    assert '"accommodation": "舒适型酒店"' in user_prompt
    assert user_prompt.endswith("帮我把预算提到 4200，交通你来安排，住宿上高端，加人文历史去掉自然风景，另外不吃海鲜")


def test_shouldFollowJacksonTextSemanticsForMalformedFields():
    llm_client = _StubLlmClient(available=True, response=(
        '{"budget": "3800", "transportation": null, "accommodation": "null",'
        ' "preferencesToAdd": [{"a": 1}, ["x"], "  "]}'
    ))
    agent = _agent(llm_client)

    decision = agent.adjust(_request(), "顺便带把伞")

    assert decision.adjustment_source == "llm"
    assert decision.applied_changes == [
        "LLM 已将预算调整为 3800 元",
        "LLM 已记录新的自然语言调整要求",
    ]
    assert decision.updated_request.budget == 3800
    assert decision.updated_request.transportation == "公共交通"
    assert decision.updated_request.accommodation == "舒适型酒店"
    assert decision.updated_request.preferences == ["美食", "自然风景"]


def test_shouldFallbackToRulesAndMarkSourceWhenLlmResponseIsNotJson():
    llm_client = _StubLlmClient(available=True, response="这不是 JSON")
    agent = _agent(llm_client)

    decision = agent.adjust(_request(), "预算改成5000，住经济型")

    assert decision.adjustment_source == "rule-fallback"
    assert decision.applied_changes == [
        "已将预算调整为 5000 元",
        "已切换为经济型酒店偏好",
        "LLM 解析失败，已自动回退到规则解析",
    ]


class _StubLoopPlanningAgent:

    def __init__(self):
        self.calls: list[TripPlanRequest] = []

    def plan(self, request: TripPlanRequest) -> TripPlan:
        self.calls.append(request)
        return TripPlan(city=request.city, planning_notes=["LoopPlanningAgent 已完成生成-评估-反思-修正闭环"])


class _StubAdjustmentAgent:

    def __init__(self):
        self.calls: list[tuple[TripPlanRequest, str | None]] = []

    def adjust(self, original_request: TripPlanRequest, adjustment_prompt: str | None):
        self.calls.append((original_request, adjustment_prompt))
        updated = original_request.model_copy(update={"budget": 5000})
        return TripAdjustmentAgent.TripAdjustmentDecision(
            updated, ["已将预算调整为 5000 元", "已切换为经济型酒店偏好"], "rule"
        )


def test_shouldReplanThroughLoopEntryAndAppendAdjustmentNotes():
    loop_agent = _StubLoopPlanningAgent()
    adjustment_agent = _StubAdjustmentAgent()
    service = TripPlanningService(adjustment_agent, loop_agent)
    original = _request()

    response = service.adjust_trip(TripAdjustmentRequest(
        original_request=original,
        adjustment_prompt="预算改成5000，改经济型酒店",
    ))

    assert adjustment_agent.calls == [(original, "预算改成5000，改经济型酒店")]
    # 改完的请求交给第 1 轮搬好的规划入口，adjust 里没有第二份规划逻辑
    assert loop_agent.calls == [response.updated_request]
    assert response.updated_request.budget == 5000
    assert response.applied_changes == ["已将预算调整为 5000 元", "已切换为经济型酒店偏好"]
    assert response.trip_plan.planning_notes == [
        "LoopPlanningAgent 已完成生成-评估-反思-修正闭环",
        "改计划解析来源：rule",
        "本次改计划要求：预算改成5000，改经济型酒店",
        "已将预算调整为 5000 元",
        "已切换为经济型酒店偏好",
    ]
