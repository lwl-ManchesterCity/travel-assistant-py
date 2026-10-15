"""PostgresEpisodeStore 的真 SQL。**连不上库就整份 skip**，不拖累默认的 pytest。

分工：单元测试跑 InMemoryEpisodeStore（test_episode_service.py），这里专门验真
SQL 对不对——`on conflict (user_id, city, content) do nothing` 到底认不认这个**三列**键、
`where user_id and city` 捞得准不准、级联删除，这些内存实现里没有对应物。

它往真库里写 users / episodes，所以用户名统一带前缀，且**前后各清一次**
（前清是防上一次跑挂了留下脏数据把主键撞掉）。表由 db.get_pool() 幂等建出来，
不用先跑迁移。
"""

import itertools

import pytest

from travel_assistant import db
from travel_assistant.clients.episode_store import SOURCE_CHAT, SOURCE_FORM
from travel_assistant.clients.postgres_episode_store import PostgresEpisodeStore

_PREFIX = "pytest_ep_"

# 用户名要每条用例各不相同。**别用 id(object())**：CPython 会复用刚释放的地址，
# 前一条用例的对象回收后，后一条拿到的 id 可能一模一样（撞过一次）。
_SEQ = itertools.count()


def _purge() -> None:
    with db.connection() as conn:
        # episodes 不在名单里：users 上的 on delete cascade 会把它们一起带走。
        conn.execute("delete from users where username like %s", (_PREFIX + "%",))


@pytest.fixture(scope="module")
def store():
    try:
        db.get_pool()
    except Exception as error:
        # psycopg 连不上时的异常有好几种（OperationalError / PoolTimeout …），
        # 这里一律当"库不在"，不做类型区分。
        pytest.skip(f"连不上 Postgres，跳过真 SQL 用例：{type(error).__name__}")
    _purge()
    yield PostgresEpisodeStore()
    _purge()


@pytest.fixture()
def user_id(store):
    """每条用例一个新用户：场景是按 user 隔离的，共用一个会互相看见对方的行。"""
    with db.connection() as conn:
        row = conn.execute(
            "insert into users (username, password_hash) values (%s, %s) returning id",
            (f"{_PREFIX}{next(_SEQ)}", "x" * 60),
        ).fetchone()
    return row[0]


def _new_user() -> int:
    with db.connection() as conn:
        return conn.execute(
            "insert into users (username, password_hash) values (%s, %s) returning id",
            (f"{_PREFIX}{next(_SEQ)}", "x" * 60),
        ).fetchone()[0]


# ---------- add ----------


def test_shouldInsertAndReadItBack(store, user_id):
    assert store.add(user_id, "杭州", "上次那家酒店隔音差", SOURCE_CHAT) is True

    rows = store.list_for_city(user_id, "杭州")
    assert [(row.city, row.content, row.source) for row in rows] == [
        ("杭州", "上次那家酒店隔音差", SOURCE_CHAT)]
    assert rows[0].created_at is not None


def test_shouldReportFalseWhenTheSameThingComesAgain(store, user_id):
    """靠 unique (user_id, city, content) 去重，第二次一行都不返回。"""
    assert store.add(user_id, "杭州", "上次那家酒店隔音差", SOURCE_CHAT) is True
    assert store.add(user_id, "杭州", "上次那家酒店隔音差", SOURCE_FORM) is False

    assert len(store.list_for_city(user_id, "杭州")) == 1


def test_shouldTreatTheSameWordsInTwoCitiesAsTwoRows(store, user_id):
    """**这一条是本档的真 SQL 要害**：唯一键是三列，不是 (user_id, content)。
    要是建成两列，「上次成都那家隔音差」会被杭州那条挡掉——用户在成都说什么都记不上。"""
    assert store.add(user_id, "杭州", "那家酒店隔音差", SOURCE_CHAT) is True
    assert store.add(user_id, "成都", "那家酒店隔音差", SOURCE_CHAT) is True

    assert len(store.list_for_user(user_id)) == 2
    assert len(store.list_for_city(user_id, "杭州")) == 1
    assert len(store.list_for_city(user_id, "成都")) == 1


def test_shouldTreatDifferentUsersAsDifferentRows(store):
    first, second = _new_user(), _new_user()

    assert store.add(first, "杭州", "上次那家酒店隔音差", SOURCE_CHAT) is True
    assert store.add(second, "杭州", "上次那家酒店隔音差", SOURCE_CHAT) is True

    assert len(store.list_for_city(first, "杭州")) == 1
    assert len(store.list_for_city(second, "杭州")) == 1


# ---------- list_for_city / list_for_user ----------


def test_shouldReturnEmptyListsRatherThanNone(store, user_id):
    assert store.list_for_user(user_id) == []
    assert store.list_for_city(user_id, "杭州") == []


def test_shouldOnlyReturnTheCityAskedFor(store, user_id):
    """判据 2 的 SQL 面：换个城市就该捞不到。"""
    store.add(user_id, "杭州", "上次那家酒店隔音差", SOURCE_CHAT)

    assert [row.content for row in store.list_for_city(user_id, "杭州")] == ["上次那家酒店隔音差"]
    assert store.list_for_city(user_id, "成都") == []


def test_shouldNotHandThisUsersEpisodesToSomeoneElse(store, user_id):
    store.add(user_id, "杭州", "上次那家酒店隔音差", SOURCE_CHAT)

    assert store.list_for_city(_new_user(), "杭州") == []
    assert store.list_for_user(_new_user()) == []


def test_shouldKeepInsertionOrder(store, user_id):
    """按 id 排，不按 created_at：同一毫秒内写的两条靠时间戳分不出先后，
    而这两条的**顺序会进提示词**，顺序一变模型看到的东西就变了。"""
    for content in ["第一件", "第二件", "第三件"]:
        store.add(user_id, "杭州", content, SOURCE_CHAT)

    assert [row.content for row in store.list_for_city(user_id, "杭州")] == ["第一件", "第二件", "第三件"]


# ---------- 级联删除 ----------


def test_shouldDeleteEpisodesWhenTheUserIsGone(store):
    """on delete cascade：注销账号不该留下孤儿场景，否则下次有人拿到同一个 id 就看见了。"""
    victim = _new_user()
    store.add(victim, "杭州", "上次那家酒店隔音差", SOURCE_CHAT)

    with db.connection() as conn:
        conn.execute("delete from users where id = %s", (victim,))
        left = conn.execute(
            "select count(*) from episodes where user_id = %s", (victim,)).fetchone()[0]

    assert left == 0


def test_shouldSurviveContentWithPunctuationAndNewlines(store, user_id):
    """场景原文进的是 text，标点、换行都不该被拦。"""
    content = "上次那家酒店隔音差（半夜还有人拖行李）"
    assert store.add(user_id, "杭州", content, SOURCE_CHAT) is True

    assert store.list_for_city(user_id, "杭州")[0].content == content
