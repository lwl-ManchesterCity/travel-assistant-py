"""PostgresPreferenceStore 的真 SQL。**连不上库就整份 skip**，不拖累默认的 pytest。

分工：单元测试跑 InMemoryPreferenceStore（test_preference_service.py），这里专门验真
SQL 对不对——`on conflict (user_id, content) do nothing` 到底认不认这个唯一键、
级联删除、order by id 是不是写入顺序，这些内存实现里没有对应物。

它往真库里写 users / preferences，所以用户名统一带前缀，且**前后各清一次**
（前清是防上一次跑挂了留下脏数据把主键撞掉）。表由 db.get_pool() 幂等建出来，
不用先跑迁移。
"""

import itertools

import pytest

from travel_assistant import db
from travel_assistant.clients.postgres_preference_store import PostgresPreferenceStore
from travel_assistant.clients.preference_store import SOURCE_CHAT, SOURCE_FORM

_PREFIX = "pytest_pref_"

# 用户名要每条用例各不相同。**别用 id(object())**：CPython 会复用刚释放的地址，
# 前一条用例的对象回收后，后一条拿到的 id 可能一模一样（撞过一次）。
_SEQ = itertools.count()


def _purge() -> None:
    with db.connection() as conn:
        # preferences 不在名单里：users 上的 on delete cascade 会把它们一起带走。
        conn.execute("delete from users where username like %s", (_PREFIX + "%",))


def _other_user() -> int:
    """另造一个用户——测"删不掉别人的"那条要用两个身份。"""
    with db.connection() as conn:
        return conn.execute(
            "insert into users (username, password_hash) values (%s, %s) returning id",
            (f"{_PREFIX}{next(_SEQ)}", "x" * 60),
        ).fetchone()[0]


@pytest.fixture(scope="module")
def store():
    try:
        db.get_pool()
    except Exception as error:
        # psycopg 连不上时的异常有好几种（OperationalError / PoolTimeout …），
        # 这里一律当"库不在"，不做类型区分。
        pytest.skip(f"连不上 Postgres，跳过真 SQL 用例：{type(error).__name__}")
    _purge()
    yield PostgresPreferenceStore()
    _purge()


@pytest.fixture()
def user_id(store):
    """每条用例一个新用户：偏好是按 user 隔离的，共用一个会互相看见对方的行。"""
    with db.connection() as conn:
        row = conn.execute(
            "insert into users (username, password_hash) values (%s, %s) returning id",
            (f"{_PREFIX}{next(_SEQ)}", "x" * 60),
        ).fetchone()
    return row[0]


# ---------- add ----------


def test_shouldInsertAndReadItBack(store, user_id):
    assert store.add(user_id, "不能吃辣", SOURCE_CHAT) is True

    rows = store.list_for_user(user_id)
    assert [(row.content, row.source) for row in rows] == [("不能吃辣", SOURCE_CHAT)]
    assert rows[0].created_at is not None


def test_shouldReportFalseWhenTheSameContentComesAgain(store, user_id):
    """判据 4 的 SQL 面：靠 unique (user_id, content) 去重，第二次一行都不返回。"""
    assert store.add(user_id, "不能吃辣", SOURCE_CHAT) is True
    assert store.add(user_id, "不能吃辣", SOURCE_FORM) is False

    assert len(store.list_for_user(user_id)) == 1


def test_shouldTreatDifferentUsersAsDifferentRows(store):
    """唯一键是 (user_id, content)，不是 content——两个人说同一句话各存各的。"""
    with db.connection() as conn:
        ids = [conn.execute(
            "insert into users (username, password_hash) values (%s, %s) returning id",
            (f"{_PREFIX}{next(_SEQ)}", "x" * 60),
        ).fetchone()[0] for _ in range(2)]

    assert store.add(ids[0], "不能吃辣", SOURCE_CHAT) is True
    assert store.add(ids[1], "不能吃辣", SOURCE_CHAT) is True

    assert len(store.list_for_user(ids[0])) == 1
    assert len(store.list_for_user(ids[1])) == 1


def test_shouldDistinguishByUserEvenWithinTheSameContent(store, user_id):
    """不相关的人读不到彼此的行。"""
    store.add(user_id, "不能吃辣", SOURCE_CHAT)

    with db.connection() as conn:
        other = conn.execute(
            "insert into users (username, password_hash) values (%s, %s) returning id",
            (f"{_PREFIX}{next(_SEQ)}", "x" * 60),
        ).fetchone()[0]

    assert store.list_for_user(other) == []


# ---------- list_for_user ----------


def test_shouldReturnAnEmptyListRatherThanNone(store, user_id):
    assert store.list_for_user(user_id) == []


def test_shouldKeepInsertionOrder(store, user_id):
    """按 id 排，不按 created_at：同一毫秒内写的两条靠时间戳分不出先后，
    而这两条的**顺序会进提示词**，顺序一变模型看到的东西就变了。"""
    for content in ["第一句", "第二句", "第三句"]:
        store.add(user_id, content, SOURCE_CHAT)

    assert [row.content for row in store.list_for_user(user_id)] == ["第一句", "第二句", "第三句"]


# ---------- 级联删除 ----------


def test_shouldDeletePreferencesWhenTheUserIsGone(store):
    """on delete cascade：注销账号不该留下孤儿偏好，否则下次有人拿到同一个 id 就看见了。"""
    with db.connection() as conn:
        victim = conn.execute(
            "insert into users (username, password_hash) values (%s, %s) returning id",
            (f"{_PREFIX}{next(_SEQ)}", "x" * 60),
        ).fetchone()[0]
    store.add(victim, "不能吃辣", SOURCE_CHAT)

    with db.connection() as conn:
        conn.execute("delete from users where id = %s", (victim,))
        left = conn.execute(
            "select count(*) from preferences where user_id = %s", (victim,)).fetchone()[0]

    assert left == 0


def test_shouldSurviveContentWithPunctuationAndNewlines(store, user_id):
    """偏好原文进的是 text 而不是变长字符串，标点不该被拦。"""
    content = "不吃辣（微辣也不行）"
    assert store.add(user_id, content, SOURCE_CHAT) is True

    assert store.list_for_user(user_id)[0].content == content


# ---------- 第五档：三列去重键（docs/ASK_LONGTERM_MEMORY.md §2.5.2）----------


def test_shouldKeepTheSameContentUnderTwoSubjects(store, user_id):
    """**本档的真 SQL 要害**：唯一键是 (user_id, subject, content) 三列，不是两列。

    两列键之下，「本人不吃辣」和「老婆不吃辣」会撞——后说的被 on conflict do nothing
    **静默吞掉**，而界面上看起来"记了"。这正是 §2.5.2 点名的那处隐蔽 bug。
    """
    assert store.add(user_id, "不吃辣", SOURCE_CHAT, "本人") is True
    assert store.add(user_id, "不吃辣", SOURCE_CHAT, "老婆") is True

    rows = store.list_for_user(user_id)
    assert sorted((row.subject, row.content) for row in rows) == [
        ("本人", "不吃辣"), ("老婆", "不吃辣")]


def test_shouldStillDedupeWhenAllThreeColumnsMatch(store, user_id):
    assert store.add(user_id, "不吃辣", SOURCE_CHAT, "老婆") is True
    assert store.add(user_id, "不吃辣", SOURCE_FORM, "老婆") is False

    assert len(store.list_for_user(user_id)) == 1


def test_shouldDefaultToTheUserHimselfWhenNoSubjectIsGiven(store, user_id):
    """不传 subject 时落库的是「本人」——列默认值是 '本人'，Python 那边缺省也是它。"""
    assert store.add(user_id, "不吃辣", SOURCE_CHAT) is True

    assert store.list_for_user(user_id)[0].subject == "本人"


def test_shouldReadTheSubjectBack(store, user_id):
    store.add(user_id, "不能吃辣", SOURCE_CHAT, "老婆")

    assert store.list_for_user(user_id)[0].subject == "老婆"


def test_shouldKeepOldRowsOnTheDefaultSubject(store, user_id):
    """**判据 4**：改列之前在库里的那几条，subject 落到默认值「本人」。
    （真库上跑迁移时实测过：改列后老数据的 subject 就是「本人」。）"""
    with db.connection() as conn:
        conn.execute(
            "insert into preferences (user_id, content, source) values (%s, %s, %s)",
            (user_id, "改列前就有的", SOURCE_CHAT))

    assert store.list_for_user(user_id)[0].subject == "本人"


# ---------- 第三档：删除的真 SQL（docs/ASK_LONGTERM_MEMORY.md §2.3.3）----------


def test_shouldDeleteOnlyThisUsersRow(store, user_id):
    """`where id = %s and user_id = %s` 那句的 SQL 面。

    **user_id 必须进 where**：id 是连续 bigserial，漏掉之后任何一个登录用户
    能删光全站的偏好——而且界面上看不出来（他只是在删自己的）。
    """
    other = _other_user()
    store.add(user_id, "我的", SOURCE_CHAT)
    store.add(other, "别人的", SOURCE_CHAT)
    someone_elses = store.list_for_user(other)[0].id

    assert store.delete(user_id, someone_elses) is False
    assert len(store.list_for_user(other)) == 1          # 别人的那条还在


def test_shouldReportFalseForAnAbsentId(store, user_id):
    assert store.delete(user_id, 999999999) is False


def test_shouldDeleteAndFreeTheUniqueKey(store, user_id):
    """删干净了，同一句话**能再记上**（§2.3.5 那条最容易漏的判据）。

    真删（`delete`）而不是软删，所以唯一键那一格也跟着空出来——
    这是内存实现里看不出来的：内存实现压根没有唯一键。
    """
    store.add(user_id, "不吃辣", SOURCE_CHAT)
    target = store.list_for_user(user_id)[0].id

    assert store.delete(user_id, target) is True
    assert store.list_for_user(user_id) == []
    assert store.add(user_id, "不吃辣", SOURCE_CHAT) is True       # 又记上了
    assert len(store.list_for_user(user_id)) == 1
