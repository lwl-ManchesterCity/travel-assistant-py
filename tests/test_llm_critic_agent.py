"""LlmCriticAgent 的行为断言。

没有可参照的既有测试，期望文案全部按实现源码推得：规则批评的六个分支 action/reason/expectedOutcome、
source 取值（llm / rule / rule-fallback）、失败回退时追加的备注，以及枚举白名单对未知 action 的兜底。
"""

from travel_assistant.agent.llm_critic_agent import LlmCriticAgent
from travel_assistant.clients.llm import LlmClient
from travel_assistant.models.budget import Budget
from travel_assistant.models.day_plan import DayPlan
from travel_assistant.models.route_plan import RoutePlan
from travel_assistant.models.trip_plan import TripPlan
from travel_assistant.models.trip_plan_reflection import TripPlanReflection
from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.models.weather_info import WeatherInfo


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


def _request(budget: int = 3000) -> TripPlanRequest:
    return TripPlanRequest(
        city="杭州",
        start_date="2026-07-01",
        end_date="2026-07-03",
        budget=budget,
        preferences=["美食"],
        transportation="公共交通",
        accommodation="舒适型酒店",
    )


def _plan(within_budget: bool, remaining_budget: int) -> TripPlan:
    return TripPlan(
        city="杭州",
        days=[DayPlan(date="2026-07-01", day_index=0, route_plan=RoutePlan(total_duration_minutes=60))],
        weather_info=[WeatherInfo(date="2026-07-01", day_weather="晴")],
        budget=Budget(total=3000 - remaining_budget, requested_budget=3000,
                      remaining_budget=remaining_budget, within_budget=within_budget),
    )


def _acceptable_reflection() -> TripPlanReflection:
    return TripPlanReflection(
        acceptable=True,
        overall_score=8,
        budget_severity=8,
        reflection_notes=["当前评分=8（预算=8，路线=0，天气=0，体验=0）"],
    )


def _reviewing_reflection() -> TripPlanReflection:
    return TripPlanReflection(
        acceptable=False,
        over_budget=True,
        total_budget_overflow_amount=600,
        budget_severity=40,
        overall_score=40,
        recommended_action="REDUCE_BUDGET",
        reflection_notes=["总预算超支 600 元"],
    )


def test_shouldUseRuleCritiqueWhenLlmIsUnavailable():
    agent = LlmCriticAgent(_StubLlmClient(available=False))

    critique = agent.critique(_request(), _plan(False, -600), _reviewing_reflection())

    assert critique.action == "REDUCE_BUDGET"
    assert critique.reason == "当前方案预算严重度最高，建议优先压缩成本后重规划"
    assert critique.expected_outcome == "通过降低住宿和交通成本，让总费用回到预算范围附近"
    assert critique.source == "rule"
    assert critique.is_should_replan() is True
    assert critique.notes == [
        "当前总预算超支 600 元",
        "评分摘要：overall=40，budget=40，route=0，weather=0，experience=0",
    ]


def test_shouldStopWhenRuleScoreIsAlreadyAcceptable():
    agent = LlmCriticAgent(_StubLlmClient(available=False))

    critique = agent.critique(_request(), _plan(True, 24), _acceptable_reflection())

    assert critique.action == "STOP"
    assert critique.reason == "规则评分已处于可接受范围，无需继续重规划"
    assert critique.expected_outcome == "保留当前方案作为最终结果"
    assert critique.is_should_replan() is False


def test_rule_critique_without_reflection_should_only_see_plan_facts():
    agent = LlmCriticAgent(_StubLlmClient(available=False))

    critique = agent.critique(_request(), _plan(False, -600), None)

    # 没有 reflection 时四个严重度全为 0，分支只能落到最后的 else
    assert critique.action == "STOP"
    assert critique.reason == "当前方案整体可接受，无需进入下一轮"
    assert critique.notes == ["当前总预算超支 600 元"]


def test_shouldUseLlmJsonResultAndNormalizeAction():
    llm = _StubLlmClient(
        available=True,
        response='{"action":"compress_route","reason":"路线太长","expectedOutcome":"少折返","notes":["同片区集中",""]}',
    )

    critique = LlmCriticAgent(llm).critique(_request(), _plan(True, 24), _acceptable_reflection())

    assert critique.action == "COMPRESS_ROUTE"
    assert critique.reason == "路线太长"
    assert critique.expected_outcome == "少折返"
    assert critique.source == "llm"
    assert critique.notes == ["同片区集中"]
    assert llm.prompts[0][1].startswith("用户请求：\n")
    assert "规则评估结果：\n" in llm.prompts[0][1]
    assert '"accommodation": "舒适型酒店"' in llm.prompts[0][1]
    assert '"overallScore": 8' in llm.prompts[0][1]
    assert '"expectedOutcome"' in llm.prompts[0][0]


def test_unknown_llm_action_should_fall_back_to_stop():
    llm = _StubLlmClient(available=True, response='{"action":"TELEPORT","reason":"越界动作"}')

    critique = LlmCriticAgent(llm).critique(_request(), _plan(True, 24), _acceptable_reflection())

    assert critique.action == "STOP"
    assert critique.reason == "越界动作"
    assert critique.expected_outcome == "维持当前方案"
    assert critique.notes == []


def test_should_fall_back_to_rules_when_llm_returns_invalid_json():
    llm = _StubLlmClient(available=True, response="这不是 JSON")

    critique = LlmCriticAgent(llm).critique(_request(), _plan(False, -600), _reviewing_reflection())

    assert critique.source == "rule-fallback"
    assert critique.action == "REDUCE_BUDGET"
    assert critique.notes[-1] == "LLM 批评阶段失败，已回退到规则批评"
    assert len(critique.notes) == 3


def test_should_fall_back_to_rules_when_llm_raises():
    class _RaisingLlmClient(_StubLlmClient):
        def chat(self, system_prompt: str, user_prompt: str) -> str:
            raise RuntimeError("LLM 请求失败: HTTP 429")

    critique = LlmCriticAgent(_RaisingLlmClient(available=True)).critique(
        _request(), _plan(True, 24), _acceptable_reflection()
    )

    assert critique.source == "rule-fallback"
    assert critique.action == "STOP"
    assert critique.notes[-1] == "LLM 批评阶段失败，已回退到规则批评"


def test_missing_llm_fields_should_use_defaults():
    llm = _StubLlmClient(available=True, response="{}")

    critique = LlmCriticAgent(llm).critique(_request(), _plan(True, 24), _acceptable_reflection())

    assert critique.action == "STOP"
    assert critique.reason == "LLM 未给出原因"
    assert critique.expected_outcome == "维持当前方案"
    assert critique.source == "llm"
    assert critique.notes == []


def test_non_object_llm_payload_should_use_all_defaults():
    # LLM 吐回的是数组不是对象：按名取字段全部落空，等价于字段缺失，全部走默认值
    llm = _StubLlmClient(available=True, response="[1, 2]")

    critique = LlmCriticAgent(llm).critique(_request(), _plan(True, 24), _acceptable_reflection())

    assert critique.action == "STOP"
    assert critique.reason == "LLM 未给出原因"
    assert critique.expected_outcome == "维持当前方案"
    assert critique.source == "llm"
    assert critique.notes == []
