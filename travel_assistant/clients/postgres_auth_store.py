"""AuthStore 的 Postgres 实现。SQL 全在这里，别处不写 SQL。

接口与内存实现见 auth_store.py；那个文件里也讲了为什么要有两个实现。
"""

from datetime import datetime

from ..db import connection
from ..exceptions import UsernameTakenException
from .auth_store import UserRecord

# 四处 select 的是同一组列，顺序也必须一致，_to_record 按位置取。
_USER_COLUMNS = "id, username, password_hash, created_at"


class PostgresAuthStore:
    def create_user(self, username: str, password_hash: str) -> UserRecord:
        # 靠 unique 约束去重，不用先 select 再 insert：那样两个请求同时注册同一个名字会双写。
        # `on conflict do nothing` 冲突时一行都不返回，fetchone() 拿到 None —— 这就是"重名"。
        with connection() as conn:
            row = conn.execute(
                f"insert into users (username, password_hash) values (%s, %s) "
                f"on conflict (username) do nothing returning {_USER_COLUMNS}",
                (username, password_hash),
            ).fetchone()
        if row is None:
            raise UsernameTakenException(username)
        return _to_record(row)

    def find_user_by_name(self, username: str) -> UserRecord | None:
        with connection() as conn:
            row = conn.execute(
                f"select {_USER_COLUMNS} from users where username = %s", (username,)
            ).fetchone()
        return _to_record(row) if row else None

    def create_session(self, token: str, user_id: int, expires_at: datetime) -> None:
        with connection() as conn:
            # 顺手清掉这个人已经过期的会话。放在这里而不是定时任务：一次登录清一次，
            # 量很小，也就不用为"过期行会慢慢堆积"另外养一个调度器。
            conn.execute(
                "delete from sessions where user_id = %s and expires_at <= now()", (user_id,)
            )
            conn.execute(
                "insert into sessions (token, user_id, expires_at) values (%s, %s, %s)",
                (token, user_id, expires_at),
            )

    def resolve_session(self, token: str, now: datetime) -> UserRecord | None:
        # 过期判断写在 where 里而不是取回来再比：少一次往返，也不会忘。
        with connection() as conn:
            row = conn.execute(
                f"select u.id, u.username, u.password_hash, u.created_at "
                f"from sessions s join users u on u.id = s.user_id "
                f"where s.token = %s and s.expires_at > %s",
                (token, now),
            ).fetchone()
        return _to_record(row) if row else None

    def delete_session(self, token: str) -> None:
        with connection() as conn:
            conn.execute("delete from sessions where token = %s", (token,))


def _to_record(row: tuple) -> UserRecord:
    return UserRecord(id=row[0], username=row[1], password_hash=row[2], created_at=row[3])
