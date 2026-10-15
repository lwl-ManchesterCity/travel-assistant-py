"""场景（情节记忆）的清洗、去重与上限。全跑在 InMemoryEpisodeStore 上，**不碰数据库**。

真 SQL（三列唯一键、on conflict do nothing）由 test_episode_store_postgres.py 守。
设计见 docs/ASK_LONGTERM_MEMORY.md §2.4.2。

**这一份与 test_preference_service.py 是两套用例，不合并**：场景多一个城市、
闸门也不同（60 对 40），合并之后每条用例都要先想"我现在测的是哪一个"。
"""

from travel_assistant.clients.episode_store import InMemoryEpisodeStore
from travel_assistant.clients.preference_store import SOURCE_CHAT, SOURCE_FORM
from travel_assistant.service.episode_service import (
    MAX_CONTENT_CHARS,
    MAX_PER_CALL,
    MAX_PER_USER,
    EpisodeService,
)


def _service() -> EpisodeService:
    return EpisodeService(InMemoryEpisodeStore())


def _episode(city: str, content: str) -> dict:
    return {"city": city, "content": content}


# ---------- 基本：存得进、读得出 ----------


def test_shouldStoreAndReadItBackByCity():
    service = _service()
    result = service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)

    assert result.added == [("杭州", "上次那家酒店隔音差")]
    assert service.list_for_city(1, "杭州") == ["上次那家酒店隔音差"]


def test_shouldKeepTheSourceSoWeCanTellWhereItCameFrom():
    store = InMemoryEpisodeStore()
    service = EpisodeService(store)
    service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)
    service.record(1, [_episode("成都", "上回第二天排太满")], SOURCE_FORM)

    assert [(row.city, row.content, row.source) for row in store.list_for_user(1)] == [
        ("杭州", "上次那家酒店隔音差", SOURCE_CHAT),
        ("成都", "上回第二天排太满", SOURCE_FORM)]


def test_shouldNotMixUpTwoUsers():
    service = _service()
    service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)
    service.record(2, [_episode("杭州", "上回第二天排太满")], SOURCE_CHAT)

    assert service.list_for_city(1, "杭州") == ["上次那家酒店隔音差"]
    assert service.list_for_city(2, "杭州") == ["上回第二天排太满"]


# ---------- 按城市取：这一档的要害 ----------


def test_shouldOnlyReturnTheCityAskedFor():
    """**取的时候按城市捞**（§2.4.3）。判据 2 的单元面：换个城市就该是 0 条。"""
    service = _service()
    service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)

    assert service.list_for_city(1, "杭州") == ["上次那家酒店隔音差"]
    assert service.list_for_city(1, "成都") == []


def test_shouldReturnNothingForAMissingCity():
    """没城市时**不许"把全部场景都拿出来"**——那等于诱导模型认错地方。"""
    service = _service()
    service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)

    assert service.list_for_city(1, None) == []
    assert service.list_for_city(1, "") == []
    assert service.list_for_city(None, "杭州") == []


# ---------- 去重：键是三列 ----------


def test_shouldNotStoreTheSameThingTwiceInTheSameCity():
    service = _service()
    service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)
    second = service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)

    assert second.added == []
    assert service.list_for_city(1, "杭州") == ["上次那家酒店隔音差"]


def test_shouldKeepTheSameWordsInTwoCitiesApart():
    """**去重键是三列**：同一个"那家酒店"在杭州和成都是两条，不能撞（§2.4.1）。"""
    service = _service()
    service.record(1, [_episode("杭州", "那家酒店隔音差")], SOURCE_CHAT)
    result = service.record(1, [_episode("成都", "那家酒店隔音差")], SOURCE_CHAT)

    assert result.added == [("成都", "那家酒店隔音差")]
    assert service.list_for_city(1, "杭州") == ["那家酒店隔音差"]
    assert service.list_for_city(1, "成都") == ["那家酒店隔音差"]


def test_shouldDedupeWithinASingleBatch():
    service = _service()
    result = service.record(1, [_episode("杭州", "隔音差"), _episode("杭州", "隔音差")], SOURCE_CHAT)

    assert result.added == [("杭州", "隔音差")]


def test_shouldTreatTheSameWordsFromTwoSourcesAsOneRow():
    service = _service()
    service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_FORM)
    again = service.record(1, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT)

    assert again.added == []
    assert service.list_for_city(1, "杭州") == ["上次那家酒店隔音差"]


# ---------- 城市那道闸门（写入端） ----------


def test_shouldDropTheWholeEpisodeWhenTheCityIsMissing():
    """**没地点就整条丢**（§2.4.1）：按城市取，没城市就永远捞不出来，
    存了等于没存——而且它还占着 50 条上限，用户却以为记下了。"""
    service = _service()
    result = service.record(1, [
        {"content": "上次那家酒店隔音差"},          # 压根没有 city 这一格
        {"city": "", "content": "上回第二天排太满"},  # 空的
        {"city": "   ", "content": "排队太久"},      # 空白
        _episode("杭州", "上次那家酒店隔音差"),
    ], SOURCE_CHAT)

    assert result.added == [("杭州", "上次那家酒店隔音差")]
    assert len(service._store.list_for_user(1)) == 1


def test_shouldDropEntriesWithoutContentOrThatAreNotDicts():
    service = _service()
    result = service.record(1, [
        {"city": "杭州"},                     # 没 content
        {"city": "杭州", "content": ""},
        "杭州：上次那家酒店隔音差",              # 模型偶尔会吐成字符串
        _episode("杭州", "上次那家酒店隔音差"),
    ], SOURCE_CHAT)

    assert result.added == [("杭州", "上次那家酒店隔音差")]


# ---------- 规范化 ----------


def test_shouldTrimWhitespaceOnBothHalves():
    service = _service()
    result = service.record(1, [_episode("  杭州  ", "  上次那家酒店隔音差  ")], SOURCE_CHAT)

    assert result.added == [("杭州", "上次那家酒店隔音差")]
    assert service.list_for_city(1, "杭州") == ["上次那家酒店隔音差"]


def test_shouldStripTrailingPunctuationSoItDoesNotForkTheRow():
    service = _service()
    service.record(1, [_episode("杭州", "隔音差")], SOURCE_CHAT)
    result = service.record(1, [_episode("成都", "排队太久！！")], SOURCE_CHAT)

    assert result.added == [("成都", "排队太久")]
    assert service.list_for_city(1, "成都") == ["排队太久"]


def test_shouldDropEpisodesThatAreTooLong():
    """60 字符那道闸门。**比偏好的 40 宽**：场景是一句话，不是短语。"""
    service = _service()
    too_long = "上回在成都的时候第二天行程排得太满了，从早到晚一直在赶路，中间连坐下来喝口茶的时间都没有，最后一天还硬塞了三个景点进去，回来累了好几天"
    # 手写的"很长的一句"很容易其实没到线（test_preference_service 栽过），所以顺带钉一下。
    assert len(too_long) > MAX_CONTENT_CHARS
    result = service.record(1, [
        _episode("成都", too_long),
        _episode("杭州", "上次那家酒店隔音差"),
    ], SOURCE_CHAT)

    assert result.added == [("杭州", "上次那家酒店隔音差")]


def test_shouldKeepAnEntryRightAtTheLengthLimit():
    service = _service()
    exact = "长" * MAX_CONTENT_CHARS

    assert service.record(1, [_episode("成都", exact)], SOURCE_CHAT).added == [("成都", exact)]


def test_shouldDoNothingWhenThereIsNothingToStore():
    service = _service()

    assert service.record(1, [], SOURCE_CHAT).added == []
    assert service.record(1, None, SOURCE_CHAT).added == []
    assert service.record(1, {"city": "杭州"}, SOURCE_CHAT).added == []


def test_shouldDoNothingWithoutAUser():
    service = _service()

    assert service.record(None, [_episode("杭州", "上次那家酒店隔音差")], SOURCE_CHAT).added == []


# ---------- 上限 ----------


def test_shouldTakeAtMostFiveEpisodesFromOneBatch():
    service = _service()
    result = service.record(1, [_episode("杭州", f"第{i}件事") for i in range(8)], SOURCE_CHAT)

    assert len(result.added) == MAX_PER_CALL


def test_shouldCountTheFiveLimitAfterCleaningNotEmptyBefore():
    service = _service()
    result = service.record(1, [_episode("杭州", "隔音差")] * 5 + [_episode("杭州", "排队久")],
                            SOURCE_CHAT)

    assert result.added == [("杭州", "隔音差"), ("杭州", "排队久")]


def test_shouldCountTheFiftyLimitAcrossCitiesNotPerCity():
    """**上限是按人算的，不是按城市算的**（§2.4.2）。只数本城市的话，
    跑十个城市就能存下 500 条——而它们每一个都会在某些规划里进 prompt。"""
    service = _service()
    cities = ["杭州", "成都", "北京", "上海", "广州"]
    for index in range(MAX_PER_USER):
        city = cities[index % len(cities)]
        service.record(1, [_episode(city, f"第{index}件事")], SOURCE_CHAT)
    assert len(service._store.list_for_user(1)) == MAX_PER_USER

    overflow = service.record(1, [_episode("杭州", "再来一件"), _episode("成都", "还有一件")],
                              SOURCE_CHAT)

    assert overflow.added == []
    assert overflow.dropped_for_cap == 2
    assert overflow.overflowed is True
    assert len(service._store.list_for_user(1)) == MAX_PER_USER


def test_shouldStillDedupeWhenTheStoreIsFull():
    """满了之后重复说一条已经有的，是"重复"不是"溢出"（§2.2.11 记过的那次教训）。"""
    service = _service()
    for index in range(MAX_PER_USER):
        service.record(1, [_episode("杭州", f"第{index}件事")], SOURCE_CHAT)

    result = service.record(1, [_episode("杭州", "第0件事")], SOURCE_CHAT)

    assert result.added == []
    assert result.dropped_for_cap == 0
