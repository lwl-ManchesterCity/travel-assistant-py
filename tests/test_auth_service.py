"""注册 / 登录 / 会话的行为，全跑在 InMemoryAuthStore 上，**不碰数据库**。

为什么不用真库：整套测试是纯单元测试，不能让 `pytest` 变成"必须先起 Postgres"。
真 SQL 由 tests/test_auth_store_postgres.py 守（连不上就 skip），
HTTP 那一层由 tests/test_auth_controller.py 守。
"""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from travel_assistant.clients.auth_store import InMemoryAuthStore
from travel_assistant.exceptions import UnauthorizedException, UsernameTakenException
from travel_assistant.models.auth import PASSWORD_MAX_BYTES, AuthCredentials
from travel_assistant.service.auth_service import AuthService

_PASSWORD = "hunter2xyz"


def _service(session_days: int = 30) -> AuthService:
    return AuthService(InMemoryAuthStore(), session_days=session_days)


# ---------- 注册 ----------


def test_shouldRecogniseTheUserRightAfterRegistering():
    service = _service()

    user, token = service.register("amy", _PASSWORD)

    assert user.username == "amy"
    assert service.current_user(token).username == "amy"


def test_shouldStoreAHashNotThePlainPassword():
    store = InMemoryAuthStore()
    service = AuthService(store)

    service.register("amy", _PASSWORD)

    stored = store.find_user_by_name("amy").password_hash
    assert stored != _PASSWORD
    assert _PASSWORD not in stored
    # bcrypt 的输出带算法版本前缀；这一条同时说明"用的是 bcrypt 而不是 md5 之类"
    assert stored.startswith("$2b$")


def test_shouldSaltEachPasswordSoTwoUsersWithTheSameOneDiffer():
    store = InMemoryAuthStore()
    service = AuthService(store)

    service.register("amy", _PASSWORD)
    service.register("bob", _PASSWORD)

    assert store.find_user_by_name("amy").password_hash != store.find_user_by_name("bob").password_hash


def test_shouldRejectADuplicateUsername():
    service = _service()
    service.register("amy", _PASSWORD)

    with pytest.raises(UsernameTakenException):
        service.register("amy", "another-password")


# ---------- 登录 ----------


def test_shouldLogInWithTheRightPassword():
    service = _service()
    service.register("amy", _PASSWORD)

    user, token = service.login("amy", _PASSWORD)

    assert user.username == "amy"
    assert service.current_user(token).username == "amy"


def test_shouldSayTheSameThingForAWrongPasswordAndAnUnknownUser():
    """分开说等于替攻击者把有效用户名筛出来。这一条是那个决定的守卫。"""
    service = _service()
    service.register("amy", _PASSWORD)

    with pytest.raises(UnauthorizedException) as wrong_password:
        service.login("amy", "not-the-password")
    with pytest.raises(UnauthorizedException) as unknown_user:
        service.login("nobody", _PASSWORD)

    assert str(wrong_password.value) == str(unknown_user.value)


def test_shouldTreatAnUnknownUserAsUnauthorizedNotAsAMissingRecord():
    service = _service()

    with pytest.raises(UnauthorizedException):
        service.login("nobody", _PASSWORD)


# ---------- 会话 ----------


def test_shouldForgetTheSessionAfterLogout():
    service = _service()
    _, token = service.register("amy", _PASSWORD)

    service.logout(token)

    assert service.current_user(token) is None


def test_shouldTreatAnExpiredSessionAsLoggedOut():
    store = InMemoryAuthStore()
    service = AuthService(store)
    user, _ = service.register("amy", _PASSWORD)
    stale = datetime.now(timezone.utc) - timedelta(seconds=1)

    store.create_session("stale-token", user.id, stale)

    assert service.current_user("stale-token") is None


def test_shouldAnswerNobodyForAMissingOrBlankToken():
    service = _service()

    assert service.current_user("") is None
    assert service.current_user(None) is None
    assert service.current_user("forged-token") is None


def test_shouldNotChokeWhenLoggingOutAnUnknownToken():
    """登出是幂等的：没登录时点它不该报错，前端也就不用为它多写一个分支。"""
    _service().logout("never-existed")


# ---------- 入参校验（models/auth.py）----------


def test_shouldAcceptAPasswordOfExactlyTheBcryptLimit():
    # 72 字节正好是上限，72 个 ASCII 字符 = 72 字节。
    credentials = AuthCredentials(username="amy", password="a" * PASSWORD_MAX_BYTES)

    assert len(credentials.password.encode("utf-8")) == PASSWORD_MAX_BYTES


def test_shouldRejectAPasswordPastTheBcryptLimit():
    """bcrypt 只看前 72 字节，多出来的等于不存在；bcrypt 5.x 更是直接抛 ValueError。
    所以这条校验不是保险起见——不加的话这个请求会变成 500 而不是 400。"""
    with pytest.raises(ValidationError) as error:
        AuthCredentials(username="amy", password="a" * (PASSWORD_MAX_BYTES + 1))

    assert "字节" in str(error.value)


def test_shouldCountTheLimitInBytesNotCharacters():
    """一个汉字 3 字节，所以 25 个汉字（75 字节）就超了——按字符数算的话它是"25 位"，看不出来。"""
    with pytest.raises(ValidationError):
        AuthCredentials(username="amy", password="密" * 25)


def test_shouldRejectAShortPassword():
    with pytest.raises(ValidationError):
        AuthCredentials(username="amy", password="12345")


def test_shouldTrimTheUsernameButNotThePassword():
    credentials = AuthCredentials(username="  amy  ", password="  spaced  ")

    assert credentials.username == "amy"
    # 密码首尾的空格是密码的一部分，strip 掉用户下次就登不进了
    assert credentials.password == "  spaced  "


def test_shouldRejectAUsernameWithSpacesInTheMiddle():
    with pytest.raises(ValidationError):
        AuthCredentials(username="a m y", password=_PASSWORD)
