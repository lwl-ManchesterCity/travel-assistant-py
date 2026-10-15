"""「我的偏好」两条端点在 HTTP 上的样子：列表、删除、越权、401。§2.3.5。

大部分用例挂 `without_login_gate`（tests/conftest.py 那个夹具）——它们测的是
**门后面的东西**（列表只回自己的、拿别人的 id 删不掉），不是门本身。

**门那一条单独写、不挂夹具**：夹具一挂门就摘了，那条用例会永远绿
（照 test_auth_controller.py 的做法）。

抓的是 InMemoryPreferenceStore，不碰库；真 SQL 由 test_preference_store_postgres.py 守。
"""

import pytest
from fastapi.testclient import TestClient

from travel_assistant.clients.preference_store import InMemoryPreferenceStore
from travel_assistant.config.container import get_preference_service
from travel_assistant.main import app
from travel_assistant.service.preference_service import PreferenceService


@pytest.fixture
def client():
    # **service 要先建好、lambda 只负责返回它**（§2.1.10 记过那个坑）：
    # 写成 `lambda: PreferenceService(InMemoryPreferenceStore())` 的话，
    # FastAPI 每次请求都调一遍这个 lambda，等于每条请求一个空 store。
    store = InMemoryPreferenceStore()
    service = PreferenceService(store)
    app.dependency_overrides[get_preference_service] = lambda: service
    try:
        yield TestClient(app), store
    finally:
        app.dependency_overrides.clear()


# ---------- 列表 ----------


def test_shouldListThisUsersPreferencesInOrder(client, without_login_gate):
    """**只回自己的、按写入先后、每条带 id**（§2.3.1 / §2.3.2）。
    id 是删除要用的那一格——少了它前端只能按内容拼。"""
    http, store = client
    store.add(1, "不吃辣", "chat")
    store.add(1, "对海鲜过敏", "form")
    store.add(2, "别人的偏好", "chat")

    body = http.get("/api/preferences").json()

    assert [item["content"] for item in body["items"]] == ["不吃辣", "对海鲜过敏"]
    assert [item["source"] for item in body["items"]] == ["chat", "form"]
    assert all(isinstance(item["id"], int) and item["id"] > 0 for item in body["items"])


def test_shouldCarryTheSubjectSoTheUserCanTellWhoseItIs(client, without_login_gate):
    """§2.5.1 那两段在列表里也得分开：不回 subject 的话，同行的人那条
    在界面上跟本人的长得一模一样，用户会删错。"""
    http, store = client
    store.add(1, "不吃辣", "chat")
    store.add(1, "不能吃辣", "chat", "老婆")

    items = http.get("/api/preferences").json()["items"]

    assert [(item["subject"], item["content"]) for item in items] == [
        ("本人", "不吃辣"), ("老婆", "不能吃辣")]


def test_shouldReturnAnEmptyListForANewUser(client, without_login_gate):
    """判据 7 的接口面：新用户是**空列表**，不是 404、不是报错。"""
    http, _ = client

    assert http.get("/api/preferences").json() == {"items": []}


# ---------- 删除 ----------


def test_shouldDeleteOwnPreference(client, without_login_gate):
    http, store = client
    store.add(1, "不吃辣", "chat")
    target = store.list_for_user(1)[0].id

    body = http.delete(f"/api/preferences/{target}").json()

    assert body == {"deleted": True}
    assert store.list_for_user(1) == []


def test_shouldNotDeleteAnotherUsersPreference(client, without_login_gate):
    """**本档唯一的安全面**（§2.3.3）：`id` 是连续 bigserial，漏掉 where 里那句
    `user_id`，任何一个登录用户能删光全站偏好，而且界面上看不出来。

    这条**不能只测回执**：还得回库里看 B 那条**还在不在**——回执对而数据被删掉
    正是漏掉 user_id 时的表现（deleted 会是 True）。
    """
    http, store = client
    store.add(2, "别人的偏好", "chat")
    victim = store.list_for_user(2)[0].id

    body = http.delete(f"/api/preferences/{victim}").json()

    assert body == {"deleted": False}
    assert [row.content for row in store.list_for_user(2)] == ["别人的偏好"]   # 还在


def test_shouldReportFalseForAnIdThatDoesNotExist(client, without_login_gate):
    """删不存在的不报错：与"不是你的"**同一种回法**，不区分（§2.3.3）。"""
    http, _ = client

    response = http.delete("/api/preferences/999999")

    assert response.status_code == 200
    assert response.json() == {"deleted": False}


def test_shouldNotDistinguishMissingFromSomeoneElses(client, without_login_gate):
    """两种情形的**状态码与响应体逐字相同**——分开就等于给攻击者一个
    "这个 id 存在"的探针（§2.1.5 同一个理由）。"""
    http, store = client
    store.add(2, "别人的偏好", "chat")
    someone_elses = store.list_for_user(2)[0].id

    response_a = http.delete(f"/api/preferences/{someone_elses}")
    response_b = http.delete("/api/preferences/999999")

    assert response_a.status_code == response_b.status_code
    assert response_a.json() == response_b.json()


# ---------- 门 ----------


def test_shouldRefuseBothEndpointsWhenNotLoggedIn(client):
    """判据 6：未登录打这两条 → 401，且走**项目统一的信封**（不是 FastAPI 的
    `{"detail": …}`）。**这条故意不挂 without_login_gate**。"""
    http, _ = client

    for response in (http.get("/api/preferences"), http.delete("/api/preferences/1")):
        assert response.status_code == 401
        body = response.json()
        assert body["code"] == "UNAUTHORIZED"
        assert body["message"]
        # 统一信封那几格都得在，别处的前端错误处理认的就是它们。
        assert set(body) == {"code", "message", "details", "timestamp"}
