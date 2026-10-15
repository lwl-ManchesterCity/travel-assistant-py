"""用户库的连接池与建表（建表脚本是 schema.sql）。

只服务长期记忆那一路（用户、会话、将来的偏好）。**规划链路一个字节都不碰它**——
所以没有起 Postgres 的时候，整套规划照常跑，只有登录相关的事情会失败。

用同步驱动：本项目的端点除 SSE 外全是同步 `def`，FastAPI 会把它们丢进线程池，
所以这里不需要 asyncpg。
"""

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import psycopg
from psycopg import Connection
from psycopg_pool import ConnectionPool

from .config.settings import get_settings

log = logging.getLogger(__name__)

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"

_pool: ConnectionPool | None = None


def _build_pool() -> ConnectionPool:
    """建池子，顺手把表建出来。

    池子不传 `open=`：默认就是**第一次 `connection()` 时才连**，
    于是"没起 Postgres 就用不了登录"而不是"整个应用起不来"。

    建表用 `ClientCursor` 而不是 `conn.execute()`：psycopg3 默认走扩展查询协议，
    **一次只允许一条语句**，而 schema.sql 是多条（还带注释）。ClientCursor 走简单查询协议，
    能把整个脚本一次发过去。
    """
    # open 必须显式写：psycopg_pool 的默认值将来会从 True 变成 False，
    # 到那天不写这一句的行为是"池子关着"，connection() 直接抛 PoolClosed。
    # 写 True 不会让应用启动时就连库——_build_pool 只在第一次 get_pool() 时才被调到。
    pool = ConnectionPool(get_settings().db.dsn, min_size=1, max_size=4, open=True)
    with pool.connection() as conn:
        with psycopg.ClientCursor(conn) as cursor:
            cursor.execute(_SCHEMA_PATH.read_text(encoding="utf-8"))
    log.info("用户库已就绪：%s", _redacted_dsn())
    return pool


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = _build_pool()
    return _pool


@contextmanager
def connection() -> Iterator[Connection]:
    """借一条连接。正常出块自动 commit，抛异常自动 rollback，两种情况都归还给池子。"""
    with get_pool().connection() as conn:
        yield conn


def _redacted_dsn() -> str:
    """日志里不打印密码。DSN 可能长成 postgresql://user:secret@host/db。"""
    dsn = get_settings().db.dsn
    if "@" not in dsn or "//" not in dsn:
        return dsn
    scheme, _, rest = dsn.partition("//")
    credentials, _, host = rest.rpartition("@")
    if ":" not in credentials:
        return dsn
    user, _, _ = credentials.partition(":")
    return f"{scheme}//{user}:***@{host}"


def reset_pool() -> None:
    """测试用：把池子关掉，下次用到时重建（换库、换配置之后要用）。"""
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = None
