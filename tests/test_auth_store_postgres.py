"""PostgresAuthStore 的真 SQL。**连不上库就整份 skip**，不拖累默认的 pytest。

分工：单元测试跑 InMemoryAuthStore（test_auth_service.py），这里专门验真 SQL 对不对——
`on conflict do nothing returning`、join、过期条件、级联删除，这些内存实现里没有对应物。

它往真库里写 users / sessions，所以用户名统一带前缀，且**前后各清一次**
（前清是防上一次跑挂了留下脏数据把主键撞掉）。表由 db.get_pool() 幂等建出来，不用先跑迁移。
"""

from datetime import datetime, timedelta, timezone

import pytest

from travel_assistant import db
from travel_assistant.clients.postgres_auth_store import PostgresAuthStore
from travel_assistant.exceptions import UsernameTakenException

_PREFIX = "pytest_auth_"


def _purge() -> None:
    with db.connection() as conn:
        # sessions 不在名单里：users 上的 on delete cascade 会把它们一起带走。
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
    yield PostgresAuthStore()
    _purge()


def _expiry(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


# ---------- users ----------


def test_shouldRoundTripAUser(store):
    name = _PREFIX + "roundtrip"

    created = store.create_user(name, "hash-value")

    assert created.id > 0
    assert created.username == name
    assert created.created_at is not None
    found = store.find_user_by_name(name)
    assert found.id == created.id
    assert found.password_hash == "hash-value"


def test_shouldReturnNoneForAnUnknownUsername(store):
    assert store.find_user_by_name(_PREFIX + "nobody") is None


def test_shouldRaiseOnADuplicateUsername(store):
    """靠 unique 约束去重（`on conflict do nothing` 一行都不返回），
    而不是"先 select 再 insert"——那样两个请求同时注册同一个名字会双写。"""
    name = _PREFIX + "dupe"
    store.create_user(name, "h")

    with pytest.raises(UsernameTakenException):
        store.create_user(name, "h")


def test_shouldKeepBothRowsWhenUsernamesDifferOnlyByCase(store):
    """用户名去重是**大小写敏感**的（text 比较）。这一条是把现状钉下来：
    将来想改成不敏感，得同时动 users 的约束和登录查询，别只改一边。"""
    store.create_user(_PREFIX + "Case", "h")

    assert store.find_user_by_name(_PREFIX + "case") is None


# ---------- sessions ----------


def test_shouldResolveALiveSession(store):
    user = store.create_user(_PREFIX + "live", "h")
    store.create_session(_PREFIX + "tok-live", user.id, _expiry(3600))

    resolved = store.resolve_session(_PREFIX + "tok-live", datetime.now(timezone.utc))

    assert resolved is not None
    assert resolved.id == user.id
    assert resolved.username == user.username


def test_shouldNotResolveAnExpiredSession(store):
    """过期判断写在 where 里。这一条守它别被改成"取回来再比"时漏掉。"""
    user = store.create_user(_PREFIX + "stale", "h")
    store.create_session(_PREFIX + "tok-stale", user.id, _expiry(-1))

    assert store.resolve_session(_PREFIX + "tok-stale", datetime.now(timezone.utc)) is None


def test_shouldNotResolveAfterDelete(store):
    user = store.create_user(_PREFIX + "bye", "h")
    store.create_session(_PREFIX + "tok-bye", user.id, _expiry(3600))

    store.delete_session(_PREFIX + "tok-bye")

    assert store.resolve_session(_PREFIX + "tok-bye", datetime.now(timezone.utc)) is None


def test_shouldSweepThisUsersExpiredSessionsOnNextLogin(store):
    """过期行不是靠定时任务清的，是**这个人下次登录时顺手清**。
    清了就不会为"养一个调度器"付代价；代价是别人过期的行会留到他自己下次登录。"""
    user = store.create_user(_PREFIX + "sweep", "h")
    store.create_session(_PREFIX + "tok-old", user.id, _expiry(-1))

    store.create_session(_PREFIX + "tok-new", user.id, _expiry(3600))

    with db.connection() as conn:
        alive = conn.execute(
            "select count(*) from sessions where user_id = %s", (user.id,)
        ).fetchone()[0]
    assert alive == 1


def test_shouldCascadeDeleteSessionsWithTheUser(store):
    user = store.create_user(_PREFIX + "cascade", "h")
    store.create_session(_PREFIX + "tok-cascade", user.id, _expiry(3600))

    with db.connection() as conn:
        conn.execute("delete from users where id = %s", (user.id,))
        left = conn.execute(
            "select count(*) from sessions where user_id = %s", (user.id,)
        ).fetchone()[0]

    assert left == 0
