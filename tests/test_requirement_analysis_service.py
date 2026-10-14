"""需求理解：多出来那一格 extractedPreferences，以及**策略句必须被剔掉**。

第二档补上这个文件（docs/ASK_LONGTERM_MEMORY.md §2.2.8）。它守的是"喂给模型的输入
是干净的"——判据 6（系统自己加的不记）的根就在这里，单元测试能验的部分远比
端到端探针稳。
"""

import json

from travel_assistant.models.trip_plan_request import TripPlanRequest
from travel_assistant.service.request_optimization_strategy_service import (
    REQ_COMPRESS_COMMUTE,
    REQ_FEWER_DETOURS,
    REQ_REDUCE_BUDGET,
    RequestOptimizationStrategyService,
    strip_system_requirements,
)
from travel_assistant.service.requirement_analysis_service import RequirementAnalysisService


class _StubLlmClient:
    """按脚本回一段 JSON，并把两次 prompt 原样留下给断言看。"""

    def __init__(self, payload: dict | None = None, available: bool = True, error=None):
        self._payload = payload if payload is not None else {}
        self._available = available
        self._error = error
        self.system_prompt: str | None = None
        self.user_prompt: str | None = None

    def is_available(self) -> bool:
        return self._available

    def chat(self, system_prompt: str, user_prompt: str) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        if self._error is not None:
            raise self._error
        return json.dumps(self._payload, ensure_ascii=False)


def _request(**overrides) -> TripPlanRequest:
    payload = {
        "city": "杭州",
        "startDate": "2026-10-01",
        "endDate": "2026-10-03",
        "budget": 3000,
        "preferences": ["自然风景"],
        "transportation": "公共交通",
        "accommodation": "舒适型酒店",
    }
    payload.update(overrides)
    return TripPlanRequest(**payload)


# ---------- 多出来那一格 ----------


def test_shouldReadTheExtractedPreferences():
    llm = _StubLlmClient({"extractedPreferences": ["对海鲜过敏", "不吃辣"]})
    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.extracted_preferences == ["对海鲜过敏", "不吃辣"]


def test_shouldDefaultToNoPreferencesWhenTheModelOmitsTheField():
    """老模型 / 老提示词不吐这一格时，是"没有偏好"而不是报错。"""
    constraints = RequirementAnalysisService(_StubLlmClient({})).analyze(_request())

    assert not constraints.extracted_preferences


def test_shouldNotEchoTheFormCheckboxesBackAsPreferences():
    """用户提示词里那行「偏好：自然风景」是**这一趟**勾的。

    模型有可能顺手把它当成"关于这个人的偏好"吐回来。那不是长期偏好——
    用户明确纠正过写入点只有备注和助手输入两处（§1.1）。精确相等就该挡掉。
    """
    llm = _StubLlmClient({"extractedPreferences": ["自然风景", "不吃辣"]})
    constraints = RequirementAnalysisService(llm).analyze(_request(preferences=["自然风景"]))

    assert constraints.extracted_preferences == ["不吃辣"]


def test_shouldKeepAMentionedPreferenceThatHappensToLookLikeACheckboxWord():
    """只在**勾了**的时候才挡。用户勾的是「人文历史」，备注里写的是「自然风景」——
    那是他自己说的，照收。（preferences 不能传空表，模型上就是"不许一个都不勾"。）"""
    llm = _StubLlmClient({"extractedPreferences": ["自然风景"]})
    constraints = RequirementAnalysisService(llm).analyze(_request(preferences=["人文历史"]))

    assert constraints.extracted_preferences == ["自然风景"]


def test_shouldNotFailWhenPreferencesComeBackAsAString():
    llm = _StubLlmClient({"extractedPreferences": "不吃辣"})
    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.extracted_preferences == []


# ---------- 判据 6 的根：策略句不能进抽取输入 ----------


def test_shouldStripTheSystemGeneratedRequirementsBeforeTheyReachTheModel():
    """改方案走 plan_trip(decision.updated_request)，策略句**必然**出现在备注里。

    照原样喂给模型，它会把「整体控制预算」抽成偏好存下来——从此这个人每一趟
    都被强制压预算，而他根本不知道（§2.2.4）。
    """
    request = _request(extraRequirements=f"对海鲜过敏；{REQ_REDUCE_BUDGET}")
    llm = _StubLlmClient({"extractedPreferences": ["对海鲜过敏"]})

    RequirementAnalysisService(llm).analyze(request)

    assert "对海鲜过敏" in llm.user_prompt
    assert REQ_REDUCE_BUDGET not in llm.user_prompt


def test_shouldStripEverySentenceTheStrategyServiceCanAdd():
    """把策略服务真加的句子原样走一遍——**不是**手抄一份到测试里。

    抄一份的话，将来加了新句子而忘了加进 SYSTEM_GENERATED_REQUIREMENTS，
    这条测试照样是绿的。真的一句一句 apply 出来，漏一条就红。
    """
    strategy = RequestOptimizationStrategyService()
    request = _request(extraRequirements="对海鲜过敏")
    for action in ("REDUCE_BUDGET", "COMPRESS_ROUTE",
                   "REPLACE_OUTDOOR_WITH_INDOOR", "UPGRADE_EXPERIENCE"):
        strategy.apply_action(request, None, action, [])

    cleaned = strip_system_requirements(request.extra_requirements)

    # 用户原话留着，四轮策略加进去的句子一条不剩。
    assert cleaned == "对海鲜过敏"
    for sentence in (REQ_REDUCE_BUDGET, REQ_COMPRESS_COMMUTE, REQ_FEWER_DETOURS):
        assert sentence in request.extra_requirements
        assert sentence not in cleaned


def test_shouldReturnNoneWhenNothingButSystemSentencesIsLeft():
    request = _request(extraRequirements=None)
    RequestOptimizationStrategyService().apply_action(request, None, "REDUCE_BUDGET", [])

    assert strip_system_requirements(request.extra_requirements) is None


def test_shouldSplitOnAllThreeSeparators():
    """拼进去用的是 `；`，但用户手打的可能是 `;` 或换行。"""
    assert strip_system_requirements(
        f"对海鲜过敏;{REQ_REDUCE_BUDGET}\n不吃辣") == "对海鲜过敏；不吃辣"


# ---------- 注入 ----------


def test_shouldPutTheLongTermPreferencesIntoThePrompt():
    llm = _StubLlmClient({})

    RequirementAnalysisService(llm).analyze(_request(), ["不能吃辣", "对海鲜过敏"])

    assert "不能吃辣" in llm.user_prompt
    assert "对海鲜过敏" in llm.user_prompt


def test_shouldNotMentionLongTermPreferencesWhenThereAreNone():
    """一条都没有时不拼那一段——不要留一句"暂无长期偏好"提醒模型这里本该有东西。"""
    llm = _StubLlmClient({})

    RequirementAnalysisService(llm).analyze(_request(), [])

    assert "长期偏好" not in llm.user_prompt


def test_shouldCarryTheFormWinsRuleInTheInjection():
    """§2.2.6：表单优先**没法用代码判**，只能是一条给模型的指令。"""
    llm = _StubLlmClient({})

    RequirementAnalysisService(llm).analyze(_request(), ["不吃海鲜"])

    assert "本次" in llm.user_prompt


def test_shouldStillWorkWithoutAnyLongTermPreferencesArgument():
    """老调用方（不传第二个参数）行为不变。"""
    llm = _StubLlmClient({"relaxedPace": True})

    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.relaxed_pace is True


# ---------- 兜底 ----------


def test_shouldFallBackToRulesWhenTheModelOutputIsNotJson():
    llm = _StubLlmClient(error=ValueError("不是 JSON"))
    constraints = RequirementAnalysisService(llm).analyze(
        _request(extraRequirements="不吃海鲜"))

    assert constraints.analysis_source == "rule-fallback"
    assert constraints.avoid_seafood is True
    # 退回规则时**没有**抽取结果：宁可不记，也不拿规则猜出来的东西当偏好存。
    assert not constraints.extracted_preferences


def test_shouldFallBackToRulesWhenTheModelIsUnavailable():
    constraints = RequirementAnalysisService(_StubLlmClient(available=False)).analyze(
        _request(extraRequirements="不吃海鲜"))

    assert constraints.analysis_source == "rule"
    assert constraints.avoid_seafood is True
