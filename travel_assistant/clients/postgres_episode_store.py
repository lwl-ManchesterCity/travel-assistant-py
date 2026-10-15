"""EpisodeStore 的 Postgres 实现。SQL 全在这里，别处不写 SQL。

接口与内存实现见 episode_store.py；那个文件里也讲了为什么要三个方法。
"""

from ..db import connection
from .episode_store import EpisodeRecord

# 两处 select 的是同一组列，顺序也必须一致，_to_record 按位置取。
_COLUMNS = "id, user_id, city, content, source, created_at"


class PostgresEpisodeStore:
    def add(self, user_id: int, city: str, content: str, source: str) -> bool:
        # 靠 unique (user_id, city, content) 去重，不用先 select 再 insert：那样
        # 两个请求同时到会双写。`on conflict do nothing` 冲突时一行都不返回，
        # fetchone() 拿到 None —— 这就是"已经有一条了"。
        with connection() as conn:
            row = conn.execute(
                f"insert into episodes (user_id, city, content, source) "
                f"values (%s, %s, %s, %s) "
                f"on conflict (user_id, city, content) do nothing returning {_COLUMNS}",
                (user_id, city, content, source),
            ).fetchone()
        return row is not None

    def list_for_user(self, user_id: int) -> list[EpisodeRecord]:
        # 按 id 排而不是 created_at：同一毫秒内写进去的两条靠时间戳分不出先后，
        # 而 id 是 bigserial，天然就是写入顺序。
        with connection() as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from episodes where user_id = %s order by id",
                (user_id,),
            ).fetchall()
        return [_to_record(row) for row in rows]

    def list_for_city(self, user_id: int, city: str) -> list[EpisodeRecord]:
        # 走 unique (user_id, city, content) 的最左前缀 (user_id, city)，不另建索引。
        with connection() as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from episodes where user_id = %s and city = %s order by id",
                (user_id, city),
            ).fetchall()
        return [_to_record(row) for row in rows]


def _to_record(row: tuple) -> EpisodeRecord:
    return EpisodeRecord(
        id=row[0], user_id=row[1], city=row[2], content=row[3], source=row[4], created_at=row[5])
