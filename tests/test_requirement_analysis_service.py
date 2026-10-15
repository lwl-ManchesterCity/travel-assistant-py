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


# ---------- 第四档：场景那一格（docs/ASK_LONGTERM_MEMORY.md §2.4）----------


def test_shouldReadTheExtractedEpisodes():
    llm = _StubLlmClient({"extractedEpisodes": [{"city": "杭州", "content": "上次那家酒店隔音差"}]})
    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.extracted_episodes == [{"city": "杭州", "content": "上次那家酒店隔音差"}]


def test_shouldDefaultToNoEpisodesWhenTheModelOmitsTheField():
    """老模型 / 老提示词不吐这一格时，是"没有场景"而不是报错。"""
    constraints = RequirementAnalysisService(_StubLlmClient({})).analyze(_request())

    assert not constraints.extracted_episodes


def test_shouldDropTheWholeEpisodeWhenTheCityIsMissing():
    """**city 缺了就整条丢**（§2.4.4）：按城市取，没城市永远捞不出来。
    这一道闸门与 ask_agent 那条是**同一个判据的两处写法**——两条写入点都要挡。"""
    llm = _StubLlmClient({"extractedEpisodes": [
        {"content": "上次那家酒店隔音差"},          # 没有 city 这一格
        {"city": "", "content": "排队太久"},        # 空的
        {"city": "杭州"},                          # 没有 content
        "杭州：上次那家酒店隔音差",                  # 模型偶尔吐成整句
        {"city": "成都", "content": "上回第二天排太满"},
    ]})
    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.extracted_episodes == [{"city": "成都", "content": "上回第二天排太满"}]


def test_shouldStripWhitespaceButLeaveThePunctuationToTheService():
    """这一层只 strip。**尾标点怎么削归 EpisodeService**（clean_content），
    与偏好那条的分工一字不差——同一处规矩不许有两份实现（§2.2.11 那条教训）。

    所以这里期望值**带着那个「。」**是故意的：它到不了库里，
    但它是"谁负责哪一段"的证据。削标点由 test_episode_service.py 守。
    """
    llm = _StubLlmClient({"extractedEpisodes": [
        {"city": "  杭州 ", "content": " 上次那家酒店隔音差。 "}]})
    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.extracted_episodes == [
        {"city": "杭州", "content": "上次那家酒店隔音差。"}]


# ---------- 判据 5：两个桶不许同时收下同一句 ----------


def test_shouldNotLetTheSameSentenceIntoBothBaskets():
    """§2.4.4 要求两个桶**互斥**。提示词里写死了，但模型仍可能两边都填——
    规则拦不干净，而漏了的表现是**同一句话被注入两遍**，
    且它明明是"上次发生的事"，却挂在了"他这个人"名下、跟着他每一趟。

    代码这道闸门与挡表单勾选框回声是同一个手法：**精确相等**判据。
    """
    sentence = "上次杭州那家酒店隔音差"
    llm = _StubLlmClient({
        "extractedPreferences": [sentence, "不吃辣"],
        "extractedEpisodes": [{"city": "杭州", "content": sentence}],
    })
    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.extracted_episodes == [{"city": "杭州", "content": sentence}]
    # 场景那一桶收下之后，偏好这一桶里就不该再有它；同批的其它偏好不受影响。
    assert constraints.extracted_preferences == ["不吃辣"]


def test_shouldStillKeepPreferencesThatOnlyLookSimilar():
    """只挡**精确相等**的。场景是「上次那家酒店隔音差」、偏好是「怕吵」——
    两句说的是一件事，但文本不同，两个桶各留各的（代词义相近不该用字符串判）。
    """
    llm = _StubLlmClient({
        "extractedPreferences": ["怕吵"],
        "extractedEpisodes": [{"city": "杭州", "content": "上次那家酒店隔音差"}],
    })
    constraints = RequirementAnalysisService(llm).analyze(_request())

    assert constraints.extracted_preferences == ["怕吵"]
    assert constraints.extracted_episodes == [{"city": "杭州", "content": "上次那家酒店隔音差"}]


# ---------- 注入 ----------


def test_shouldPutTheEpisodesIntoThePromptWithTheirCity():
    llm = _StubLlmClient({})

    RequirementAnalysisService(llm).analyze(_request(), [], ["上次那家酒店隔音差"])

    assert "上次那家酒店隔音差" in llm.user_prompt
    # 城市标签取的是本趟目的地——调用方已经**按它筛过**了（§2.4.3）。
    assert "杭州" in llm.user_prompt
    # 与偏好那条并列写在一处，让总览文案分得清"上次发生过的"和"这次要办的"。
    assert "已经发生过" in llm.user_prompt


def test_shouldNotMentionEpisodesWhenThereAreNone():
    """一条场景都没有时不拼那一段。**换个城市就是这条**（§2.4.7 判据 2 的根）。"""
    llm = _StubLlmClient({})

    RequirementAnalysisService(llm).analyze(_request(), [], [])

    assert "他去过的地方" not in llm.user_prompt


def test_shouldStillWorkWithoutTheEpisodesArgument():
    """老调用方（只传两个参数）行为不变。"""
    llm = _StubLlmClient({"relaxedPace": True})

    constraints = RequirementAnalysisService(llm).analyze(_request(), ["不能吃辣"])

    assert constraints.relaxed_pace is True
    assert "他去过的地方" not in llm.user_prompt
