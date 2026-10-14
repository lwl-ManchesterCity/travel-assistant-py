"""用户与会话的存取接口，外加一个内存实现。

**为什么要有内存实现**：现有 339 条测试是纯单元测试、不碰任何外部依赖，
不能因为加了用户库就把整个 `pytest` 变成"必须先起 Postgres"。
做法照抄项目已有的惯例——`clients/` 下每个高德实现（`amap_*`）都配一个本地兜底
（`local_attraction` / `rule_*`），共用同一个接口，只是这里没有运行时的二选一开关：

    单元测试 → InMemoryAuthStore（本文件）
    真服务   → PostgresAuthStore（postgres_auth_store.py，container.py 装配）

真 SQL 由 tests/test_auth_store_postgres.py（连不上就 skip）和端到端探针覆盖。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ..exceptions import UsernameTakenException


@dataclass(frozen=True)
class UserRecord:
    """库里的一行 users。`password_hash` 只在这条链里流转，不出 API。"""

    id: int
    username: str
    password_hash: str
    created_at: datetime | None = None


class AuthStore(Protocol):
    def create_user(self, username: str, password_hash: str) -> UserRecord:
        """建号。用户名已存在时抛 UsernameTakenException。"""
        ...

    def find_user_by_name(self, username: str) -> UserRecord | None:
        ...

    def create_session(self, token: str, user_id: int, expires_at: datetime) -> None:
        ...

    def resolve_session(self, token: str, now: datetime) -> UserRecord | None:
        """会话有效则返回那个人，否则 None。**过期的算无效。**"""
        ...

    def delete_session(self, token: str) -> None:
        ...


class InMemoryAuthStore:
    """给测试用的实现。行为要和 PostgresAuthStore 对得上，否则测试守不住真库。"""

    def __init__(self) -> None:
        self._users: dict[str, UserRecord] = {}
        self._sessions: dict[str, tuple[int, datetime]] = {}
        self._next_id = 1

    def create_user(self, username: str, password_hash: str) -> UserRecord:
        if username in self._users:
            raise UsernameTakenException(username)
        record = UserRecord(id=self._next_id, username=username, password_hash=password_hash)
        self._next_id += 1
        self._users[username] = record
        return record

    def find_user_by_name(self, username: str) -> UserRecord | None:
        return self._users.get(username)

    def create_session(self, token: str, user_id: int, expires_at: datetime) -> None:
        self._sessions[token] = (user_id, expires_at)

    def resolve_session(self, token: str, now: datetime) -> UserRecord | None:
        found = self._sessions.get(token)
        if found is None:
            return None
        user_id, expires_at = found
        if expires_at <= now:
            return None
        return next((user for user in self._users.values() if user.id == user_id), None)

    def delete_session(self, token: str) -> None:
        self._sessions.pop(token, None)
