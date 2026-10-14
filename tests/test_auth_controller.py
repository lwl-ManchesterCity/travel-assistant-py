"""登录这道门在 HTTP 上的样子：401 信封、cookie 属性、注册/登出。

**本文件故意不挂 `without_login_gate`**（tests/conftest.py）——它就是来测那道门的。
auth_service 换成 InMemoryAuthStore，所以不碰数据库。
"""

import pytest
from fastapi.testclient import TestClient

from travel_assistant.clients.auth_store import InMemoryAuthStore
from travel_assistant.config.container import get_auth_service
from travel_assistant.main import app
from travel_assistant.service.auth_service import AuthService

_PASSWORD = "hunter2xyz"
_PLAN_PAYLOAD = {"city": "杭州", "startDate": "2026-09-30", "endDate": "2026-10-01", "budget": 3000}


@pytest.fixture
def client():
    # 每次用例一个干净的 store：用户名不串场，会话也不互相看见。
    # **service 要在夹具里建好再被 lambda 返回**——写成 `lambda: AuthService(InMemoryAuthStore())`
    # 的话 FastAPI 每次请求都调一遍这个 lambda，等于每条请求一个新 store：
    # 注册完的那条用户，下一条请求就查不到了。
    service = AuthService(InMemoryAuthStore())
    app.dependency_overrides[get_auth_service] = lambda: service
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def _register(client: TestClient, username: str = "amy", password: str = _PASSWORD):
    return client.post("/api/auth/register", json={"username": username, "password": password})


# ---------- 门禁本身 ----------


def test_shouldRefuseToPlanWithoutBeingLoggedIn(client):
    response = client.post("/api/trips/plan", json=_PLAN_PAYLOAD)

    assert response.status_code == 401


def test_shouldRefuseToAskWithoutBeingLoggedIn(client):
    """追问那条链一样要挡——它每次都会真调模型和工具，是有成本的。"""
    assert client.post("/api/trips/ask", json={"question": "有门票吗"}).status_code == 401


def test_shouldReturnTheProjectErrorEnvelopeOn401(client):
    """不能是 FastAPI 默认的 {"detail": ...}——前端只该写一套错误解析。"""
    body = client.post("/api/trips/plan", json=_PLAN_PAYLOAD).json()

    assert body["code"] == "UNAUTHORIZED"
    assert body["message"]
    assert isinstance(body["details"], list)
    assert body["timestamp"]
    assert "detail" not in body


def test_shouldLeaveMapConfigOpen(client):
    """它吐的是本来就要发到浏览器里的高德前端 JS key，挡它没有意义。
    挡的判据是"会产生成本、会读写数据"，不是"是个 API"。"""
    assert client.get("/api/map/config").status_code == 200


def test_shouldRefuseAForgedCookie(client):
    client.cookies.set("travel_session", "forged-token")

    assert client.get("/api/auth/me").status_code == 401


# ---------- 注册 ----------


def test_shouldRegisterAndBeLoggedInImmediately(client):
    response = _register(client)

    assert response.status_code == 200
    assert response.json()["username"] == "amy"
    # 注册即登录：不用再输一次
    assert client.get("/api/auth/me").json()["username"] == "amy"


def test_shouldNotLeakThePasswordHash(client):
    assert "password" not in _register(client).json()
    assert "passwordHash" not in client.get("/api/auth/me").json()


def test_shouldRefuseADuplicateUsernameWith409(client):
    _register(client)

    response = _register(client)

    assert response.status_code == 409
    assert response.json()["code"] == "USERNAME_TAKEN"


def test_shouldReportAValidationFailureInTheEnvelopeShape(client):
    response = client.post("/api/auth/register", json={"username": "amy", "password": "12345"})

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_ERROR"
    # 真正有用的那句话在 details 里，message 只是概括
    assert any("密码" in detail for detail in response.json()["details"])


# ---------- cookie ----------


def test_shouldSetAnHttpOnlyCookieOnRegister(client):
    cookie = _register(client).headers["set-cookie"]

    assert "travel_session=" in cookie
    # httpOnly：JS 读不到，XSS 偷不走。这一条是选 cookie 而不是 localStorage JWT 的全部理由。
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie.lower() or "samesite=lax" in cookie.lower()


def test_shouldNotSetSecureOnAHttpDevServer(client):
    """本机是 http，加了 Secure 浏览器**直接不收这个 cookie**，登录会静默失效。
    上线换 https 时必须加回来——那条记在 docs/ASK_LONGTERM_MEMORY.md §2.1.4。"""
    assert "Secure" not in _register(client).headers["set-cookie"]


# ---------- 登录 ----------


def test_shouldLogInWithTheRightPassword(client):
    _register(client)
    client.cookies.clear()

    response = client.post("/api/auth/login", json={"username": "amy", "password": _PASSWORD})

    assert response.status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "amy"


@pytest.mark.parametrize(
    "username,password",
    [("amy", "not-the-password"), ("nobody", _PASSWORD)],
)
def test_shouldAnswerTheSameWayForAWrongPasswordAndAnUnknownUser(client, username, password):
    _register(client)
    client.cookies.clear()

    response = client.post("/api/auth/login", json={"username": username, "password": password})

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_shouldGiveBothFailuresTheSameMessage(client):
    _register(client)
    client.cookies.clear()

    wrong = client.post("/api/auth/login", json={"username": "amy", "password": "nope1234"}).json()
    unknown = client.post("/api/auth/login", json={"username": "nobody", "password": _PASSWORD}).json()

    assert wrong["message"] == unknown["message"]


# ---------- 登出 ----------


def test_shouldLogOutAndBeRefusedAgain(client):
    _register(client)

    logout = client.post("/api/auth/logout")

    assert logout.status_code == 200
    assert client.get("/api/auth/me").status_code == 401
    assert client.post("/api/trips/plan", json=_PLAN_PAYLOAD).status_code == 401


def test_shouldClearTheCookieOnLogout(client):
    _register(client)

    cookie = client.post("/api/auth/logout").headers["set-cookie"]

    assert "travel_session=" in cookie
    assert "Max-Age=0" in cookie


def test_shouldLetLogoutBeCalledWithoutBeingLoggedIn(client):
    """登出是幂等的。为它报错只会让前端多写一个分支。"""
    assert client.post("/api/auth/logout").status_code == 200
