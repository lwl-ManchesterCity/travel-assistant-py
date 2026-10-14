"""长期偏好的清洗、去重与上限。全跑在 InMemoryPreferenceStore 上，**不碰数据库**。

真 SQL（unique 约束、on conflict do nothing）由 test_preference_store_postgres.py 守。
分工的理由见 clients/preference_store.py 与 docs/ASK_LONGTERM_MEMORY.md §2.2.8。
"""

from travel_assistant.clients.preference_store import (
    SOURCE_CHAT,
    SOURCE_FORM,
    InMemoryPreferenceStore,
)
from travel_assistant.service.preference_service import (
    MAX_CONTENT_CHARS,
    MAX_PER_CALL,
    MAX_PER_USER,
    PreferenceService,
)


def _service() -> PreferenceService:
    return PreferenceService(InMemoryPreferenceStore())


def _contents(user_id=1) -> list[str]:
    return [row.content for row in _service()._store.list_for_user(user_id)]


# ---------- 基本：存得进、读得出 ----------


def test_should_store_and_readBackTheFullList():
    service = _service()
    result = service.record(1, ["不能吃辣", "不吃香菜"], SOURCE_CHAT)

    assert result.added == ["不能吃辣", "不吃香菜"]
    assert service.list_for_injection(1) == ["不能吃辣", "不吃香菜"]


def test_shouldKeepTheSourceSoWeCanTellWhereItCameFrom():
    store = InMemoryPreferenceStore()
    service = PreferenceService(store)
    service.record(1, ["不能吃辣"], SOURCE_CHAT)
    service.record(1, ["对海鲜过敏"], SOURCE_FORM)

    assert [(row.content, row.source) for row in store.list_for_user(1)] == [
        ("不能吃辣", SOURCE_CHAT), ("对海鲜过敏", SOURCE_FORM)]


def test_shouldNotMixUpTwoUsers():
    service = _service()
    service.record(1, ["不能吃辣"], SOURCE_CHAT)
    service.record(2, ["不吃香菜"], SOURCE_CHAT)

    assert service.list_for_injection(1) == ["不能吃辣"]
    assert service.list_for_injection(2) == ["不吃香菜"]


# ---------- 判据 4：说两遍还是一条 ----------


def test_shouldNotStoreTheSameThingTwice():
    service = _service()
    service.record(1, ["不能吃辣"], SOURCE_CHAT)
    second = service.record(1, ["不能吃辣"], SOURCE_CHAT)

    # 第二次**什么都不返回**——前端那行「已记住」只报新增的，重复说不该再弹一次。
    assert second.added == []
    assert service.list_for_injection(1) == ["不能吃辣"]


def test_shouldDedupeWithinASingleBatch():
    """模型把「我不吃辣也不吃辣」拆出两条一样的，只该存一条。"""
    service = _service()
    result = service.record(1, ["不吃辣", "不吃辣"], SOURCE_CHAT)

    assert result.added == ["不吃辣"]


def test_shouldTreatTheSameWordsFromTwoSourcesAsOneRow():
    """表单里写过、聊天里又说一遍，还是同一条——去重按内容，不按来源。"""
    service = _service()
    service.record(1, ["对海鲜过敏"], SOURCE_FORM)
    again = service.record(1, ["对海鲜过敏"], SOURCE_CHAT)

    assert again.added == []
    assert service.list_for_injection(1) == ["对海鲜过敏"]


# ---------- 规范化 ----------


def test_shouldTrimWhitespace():
    service = _service()
    result = service.record(1, ["  不能吃辣  "], SOURCE_CHAT)

    assert result.added == ["不能吃辣"]


def test_shouldStripTrailingPunctuationSoItDoesNotForkTheRow():
    """模型吐「不吃辣。」是常态；带标点的话它和「不吃辣」会变成两条。"""
    service = _service()
    service.record(1, ["不吃辣"], SOURCE_CHAT)
    result = service.record(1, ["不吃辣。", "怕吵！！"], SOURCE_CHAT)

    assert result.added == ["怕吵"]
    assert service.list_for_injection(1) == ["不吃辣", "怕吵"]


def test_shouldDropEntriesThatAreTooLong():
    """40 字符那道闸门：模型偶尔会把用户整句话当偏好吐出来，
    那不是偏好，是一条被截断的行程要求，存下来每趟都会被带上。"""
    service = _service()
    # 数中文字符很容易数错，所以下面那句断言不能省——第一版就是栽在这上面：
    # 手写的"很长的一句"其实只有 35 个字，闸门根本没被触发，测试却是绿的。
    too_long = "我希望这次去杭州的时候能顺便看看西湖边上那个新开的书店，另外还想留一天去灵隐寺慢慢逛"
    assert len(too_long) > MAX_CONTENT_CHARS
    result = service.record(1, [too_long, "不吃辣"], SOURCE_CHAT)

    assert result.added == ["不吃辣"]
    assert service.list_for_injection(1) == ["不吃辣"]


def test_shouldKeepAnEntryRightAtTheLengthLimit():
    service = _service()
    exact = "辣" * MAX_CONTENT_CHARS

    assert service.record(1, [exact], SOURCE_CHAT).added == [exact]


def test_shouldDropBlanksAndNonStrings():
    service = _service()
    result = service.record(1, ["", "   ", "  。 ", None, 42, "不吃辣"], SOURCE_CHAT)

    assert result.added == ["不吃辣"]


def test_shouldDoNothingWhenThereIsNothingToStore():
    service = _service()

    assert service.record(1, [], SOURCE_CHAT).added == []
    assert service.record(1, None, SOURCE_CHAT).added == []
    assert service.list_for_injection(1) == []


def test_shouldDoNothingWithoutAUser():
    """没登录（user_id 是 None）时不写——这条链不该凭一个空 id 往库里塞东西。"""
    service = _service()

    assert service.record(None, ["不能吃辣"], SOURCE_CHAT).added == []


# ---------- 上限 ----------


def test_shouldTakeAtMostFiveEntriesFromOneBatch():
    service = _service()
    result = service.record(1, [f"偏好{i}" for i in range(8)], SOURCE_CHAT)

    assert len(result.added) == MAX_PER_CALL
    assert service.list_for_injection(1) == [f"偏好{i}" for i in range(MAX_PER_CALL)]


def test_shouldCountTheFiveLimitAfterCleaningNotEmptyBefore():
    """先截 5 条再清洗的话，前 5 条里有重复就实际只记了 4 条。顺序不能反。"""
    service = _service()
    result = service.record(1, ["不吃辣", "不吃辣", "不吃辣", "不吃辣", "不吃辣", "怕吵"],
                            SOURCE_CHAT)

    assert result.added == ["不吃辣", "怕吵"]


def test_shouldStopAtFiftyAndReportHowManyWereDropped():
    service = _service()
    service.record(1, [f"偏好{i}" for i in range(MAX_PER_CALL)], SOURCE_CHAT)
    for batch in range(1, MAX_PER_USER // MAX_PER_CALL):
        service.record(1, [f"偏好{batch * MAX_PER_CALL + i}" for i in range(MAX_PER_CALL)],
                       SOURCE_CHAT)
    assert len(service.list_for_injection(1)) == MAX_PER_USER

    overflow = service.record(1, ["再来一条", "还有一条"], SOURCE_CHAT)

    assert overflow.added == []
    assert overflow.dropped_for_cap == 2
    assert overflow.overflowed is True
    assert len(service.list_for_injection(1)) == MAX_PER_USER


def test_shouldStillDedupeWhenTheStoreIsFull():
    """满了之后重复说一条已经有的，是"重复"不是"溢出"——两条都不该报成溢出。"""
    service = _service()
    for batch in range(MAX_PER_USER // MAX_PER_CALL):
        service.record(1, [f"偏好{batch * MAX_PER_CALL + i}" for i in range(MAX_PER_CALL)],
                       SOURCE_CHAT)

    result = service.record(1, ["偏好0"], SOURCE_CHAT)

    assert result.added == []
    assert result.dropped_for_cap == 0


# ---------- 注入文本 ----------


def test_shouldBuildAnInjectionBlockThatCarriesBothRules():
    from travel_assistant.service.preference_service import build_injection_block

    block = build_injection_block(["不能吃辣", "对海鲜过敏"])

    assert "不能吃辣" in block and "对海鲜过敏" in block
    # §2.2.6：表单优先；§2.2.5：冲突要说出来、不许顺着方案讲。
    assert "本次" in block
    assert "冲突" in block


def test_shouldReturnAnEmptyBlockWhenThereIsNothingToInject():
    """一条都没有时返回空串，调用方据此跳过——不要拼一段"暂无偏好"塞进去。"""
    from travel_assistant.service.preference_service import build_injection_block

    assert build_injection_block([]) == ""
    assert build_injection_block(None) == ""
