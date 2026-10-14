"""测试台公共夹具。

登录门禁（`require_current_user`）挂在本项目所有 `/api/trips/*` 上。而绝大多数既有用例
测的是**门后面的东西**——规划结果、追问信封、路由顺序——不是登录本身。
所以这里给一个夹具把门摘掉，免得每条用例都要先注册一个用户。

摘掉是**显式挂夹具**，不是 autouse：autouse 会让将来写门禁用例的人莫名测不到门。
（`test_ask_agent.py` 里那个 autouse 是那一份文件自己的局部声明，只作用于该文件。）

门禁**本身**由这两个文件守，它们不挂这个夹具：

    tests/test_auth_controller.py  —— HTTP 层：401 信封、cookie、注册/登出
    tests/test_auth_service.py     —— bcrypt 与会话

真 SQL 由 `tests/test_auth_store_postgres.py` 守（连不上就 skip）。
"""

import pytest

from travel_assistant.api.auth_dependency import require_current_user
from travel_assistant.main import app
from travel_assistant.models.auth import AuthUser

# 假用户。只在这道门被摘掉时存在，不写库。
# 哪个用例要断言"是谁"，就该走真登录，别拿它当断言对象。
TEST_USER = AuthUser(id=1, username="tester")


@pytest.fixture
def without_login_gate():
    """挂上它，本用例内的 /api/trips/* 不再要求登录。"""
    app.dependency_overrides[require_current_user] = lambda: TEST_USER
    yield TEST_USER
    app.dependency_overrides.pop(require_current_user, None)
