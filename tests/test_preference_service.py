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
    assert service.list_belonging(1) == ["不能吃辣", "不吃香菜"]


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

    assert service.list_belonging(1) == ["不能吃辣"]
    assert service.list_belonging(2) == ["不吃香菜"]


# ---------- 判据 4：说两遍还是一条 ----------


def test_shouldNotStoreTheSameThingTwice():
    service = _service()
    service.record(1, ["不能吃辣"], SOURCE_CHAT)
    second = service.record(1, ["不能吃辣"], SOURCE_CHAT)

    # 第二次**什么都不返回**——前端那行「已记住」只报新增的，重复说不该再弹一次。
    assert second.added == []
    assert service.list_belonging(1) == ["不能吃辣"]


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
    assert service.list_belonging(1) == ["对海鲜过敏"]


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
    assert service.list_belonging(1) == ["不吃辣", "怕吵"]


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
    assert service.list_belonging(1) == ["不吃辣"]


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
    assert service.list_belonging(1) == []


def test_shouldDoNothingWithoutAUser():
    """没登录（user_id 是 None）时不写——这条链不该凭一个空 id 往库里塞东西。"""
    service = _service()

    assert service.record(None, ["不能吃辣"], SOURCE_CHAT).added == []


# ---------- 上限 ----------


def test_shouldTakeAtMostFiveEntriesFromOneBatch():
    service = _service()
    result = service.record(1, [f"偏好{i}" for i in range(8)], SOURCE_CHAT)

    assert len(result.added) == MAX_PER_CALL
    assert service.list_belonging(1) == [f"偏好{i}" for i in range(MAX_PER_CALL)]


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
    assert len(service.list_belonging(1)) == MAX_PER_USER

    overflow = service.record(1, ["再来一条", "还有一条"], SOURCE_CHAT)

    assert overflow.added == []
    assert overflow.dropped_for_cap == 2
    assert overflow.overflowed is True
    assert len(service.list_belonging(1)) == MAX_PER_USER


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


# ---------- 第五档：角色（docs/ASK_LONGTERM_MEMORY.md §2.5）----------
#
# 这一档撤掉了「别人的整条不记」，改成**记下来并标出是谁**（§2.5.3）。
# 这里守三件事：subject 参与去重、两条路都不丢、注入时分得开。

def _item(subject: str | None, content: str) -> dict:
    return {"subject": subject, "content": content} if subject else {"content": content}


def test_shouldKeepTheSameWordsUnderTwoSubjectsApart():
    """**本档最隐蔽的一处**（§2.5.2）：去重键是 (user_id, subject, content) 三列。
    两列键之下「本人不吃辣」和「老婆不吃辣」会撞，后说的被 on conflict do nothing
    **静默吞掉**，而界面上看起来"记了"。"""
    service = _service()
    first = service.record(1, [_item("本人", "不吃辣")], SOURCE_CHAT)
    result = service.record(1, [_item("老婆", "不吃辣")], SOURCE_CHAT)

    # 回执（added，见 §2.5.6 补的那段）也得跟存储同一个长相。**同一条内容两个角色、
    # 两条回执必须分得开**——裸给内容的话这两行一模一样，用户会以为记重了。
    assert first.added == ["不吃辣"]
    assert result.added == ["老婆不吃辣"]
    assert service.list_belonging(1) == ["不吃辣"]
    assert service.list_companions(1) == ["老婆不吃辣"]


def test_shouldStillDedupeTheSameWordsUnderTheSameSubject():
    """同一个角色的同一句话，还是只存一条。"""
    service = _service()
    service.record(1, [_item("老婆", "不能吃辣")], SOURCE_CHAT)
    again = service.record(1, [_item("老婆", "不能吃辣")], SOURCE_CHAT)

    assert again.added == []
    assert len(service._store.list_for_user(1)) == 1


def test_shouldDefaultToTheUserHimselfWhenSubjectIsMissing():
    """**缺 subject 的按「本人」兜**（§2.5.5）。宁可当成他本人的——第五档之前
    所有条目本来也都是本人的——也不要凭空造一个同行的人出来，
    那会让这条偏好变成"有条件才生效"。"""
    service = _service()
    service.record(1, [{"content": "不吃辣"}, "怕吵"], SOURCE_CHAT)

    assert service.list_belonging(1) == ["不吃辣", "怕吵"]
    assert service.list_companions(1) == []


def test_shouldDefaultToTheUserHimselfWhenSubjectIsBlank():
    service = _service()
    service.record(1, [_item("   ", "不吃辣")], SOURCE_CHAT)

    assert service.list_belonging(1) == ["不吃辣"]


def test_shouldDropEntriesWithAnAbsurdlyLongSubject():
    """角色那一格也卡长度：被污染的话，这条会被归到一段根本不存在的"同行的人"里，
    从而**有条件地不生效**——而它可能是用户自己的偏好。"""
    from travel_assistant.service.preference_service import MAX_SUBJECT_CHARS

    too_long = "我" * (MAX_SUBJECT_CHARS + 1)
    service = _service()
    service.record(1, [{"subject": too_long, "content": "不吃辣"}], SOURCE_CHAT)

    # 被削到空 → 兜成本人，而不是变成一个人名古怪的同行人。
    assert service.list_belonging(1) == ["不吃辣"]
    assert service.list_companions(1) == []


def test_shouldSplitBelongingAndCompanions():
    service = _service()
    service.record(1, [
        _item("本人", "不吃辣"),
        _item("老婆", "不能吃辣"),
        _item("你朋友", "对海鲜过敏"),
    ], SOURCE_CHAT)

    assert service.list_belonging(1) == ["不吃辣"]
    assert service.list_companions(1) == ["老婆不能吃辣", "你朋友对海鲜过敏"]


def test_shouldNotLeakAnotherUsersCompanions():
    service = _service()
    service.record(1, [_item("老婆", "不能吃辣")], SOURCE_CHAT)

    assert service.list_companions(2) == []


# ---------- 注入块：两段分得开 ----------


def test_shouldBuildTwoSegmentsForBelongingAndCompanions():
    """§2.5.4：本人与同行的人**分两段写**。混在一段里，模型就分不出哪条是谁的——
    而"这条到底是不是他的"正是这一档要修的东西。"""
    from travel_assistant.service.preference_service import build_injection_block

    block = build_injection_block(["不吃辣"], ["老婆不能吃辣"])

    assert "【这个人的长期偏好】不吃辣" in block
    assert "【同行的人的偏好】老婆不能吃辣" in block
    # 这一段里明说"不是他的"，并带上那条判不了的规矩（没有代码兜底，只能写给模型）。
    assert "不是他的" in block
    assert "本趟确实有这个人" in block


def test_shouldOnlyRenderTheCompanionSegmentWhenThereAreCompanions():
    """没有同行的人时**不出现那一段**——与"没有就不拼"同一条规矩。"""
    from travel_assistant.service.preference_service import build_injection_block

    block = build_injection_block(["不吃辣"], [])

    assert "同行的人的偏好" not in block


def test_shouldReturnAnEmptyBlockWhenThereIsOnlyAnEmptyCompanionList():
    from travel_assistant.service.preference_service import build_injection_block

    assert build_injection_block([], []) == ""
    assert build_injection_block(None, None) == ""
