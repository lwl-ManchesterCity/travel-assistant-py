"""注册 / 登录 / 会话。对应 docs/ASK_LONGTERM_MEMORY.md §2.1。

密码用 bcrypt；会话是一条随机 token + 服务端一行记录，**不走 JWT**——
理由（httpOnly 偷不走、能真登出）在 §2.1.4，这里只落实。

这个类只干"已经合法的用户名密码"之后的活：长度、字节数、空格那些校验在
models/auth.py 里，走 FastAPI 那条既有的 400 通路。
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt

from ..clients.auth_store import AuthStore, UserRecord
from ..exceptions import UnauthorizedException
from ..models.auth import AuthUser

log = logging.getLogger(__name__)

# 登录失败只有这一句。区分"用户名不存在"和"密码错"等于替攻击者把有效用户名筛出来。
_BAD_CREDENTIALS = "用户名或密码不对"

# 32 字节随机 → token_urlsafe 出来 43 个字符，够长到猜不出来。
_TOKEN_BYTES = 32

_dummy_hash: bytes | None = None


def _dummy_hash_value() -> bytes:
    """一个真的 bcrypt 哈希，专给"用户不存在"那条路用。

    不这么做的话：未知用户名立刻返回，已知用户名要等 bcrypt 比对的几十毫秒，
    响应时间的差本身就能被拿来枚举用户名——上面那句统一文案就白写了。

    懒算而不是模块级常量：bcrypt 一次要几十毫秒，不该让"没用到登录"的进程也付这个钱。
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(b"never-a-real-password", bcrypt.gensalt())
    return _dummy_hash


class AuthService:
    def __init__(self, store: AuthStore, session_days: int = 30):
        self._store = store
        self.session_days = max(1, int(session_days))

    # ---------- 注册 / 登录 / 登出 ----------

    def register(self, username: str, password: str) -> tuple[AuthUser, str]:
        """建号并**直接登录**，返回 (用户, token)。重名由 store 抛 UsernameTakenException。"""
        record = self._store.create_user(username, _hash_password(password))
        return _to_user(record), self._open_session(record.id)

    def login(self, username: str, password: str) -> tuple[AuthUser, str]:
        found = self._store.find_user_by_name(username)
        if found is None:
            bcrypt.checkpw(password.encode("utf-8"), _dummy_hash_value())
            raise UnauthorizedException(_BAD_CREDENTIALS)
        if not bcrypt.checkpw(password.encode("utf-8"), found.password_hash.encode("utf-8")):
            raise UnauthorizedException(_BAD_CREDENTIALS)
        return _to_user(found), self._open_session(found.id)

    def logout(self, token: str) -> None:
        if token:
            self._store.delete_session(token)

    def current_user(self, token: str) -> AuthUser | None:
        """token 空、查不到、或已过期，一律 None——调用方只需要"有没有人"。"""
        if not token:
            return None
        record = self._store.resolve_session(token, _now())
        return _to_user(record) if record else None

    # ---------- 内部 ----------

    def _open_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._store.create_session(token, user_id, _now() + timedelta(days=self.session_days))
        return token


def _hash_password(password: str) -> str:
    # 盐由 gensalt() 生成并写在哈希串里，所以库里不用另存一列盐。
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _to_user(record: UserRecord) -> AuthUser:
    return AuthUser(id=record.id, username=record.username, created_at=record.created_at)


def _now() -> datetime:
    # 带时区。库里是 timestamptz，拿 naive 的去比会被按本地时区解释，跨时区就错。
    return datetime.now(timezone.utc)
