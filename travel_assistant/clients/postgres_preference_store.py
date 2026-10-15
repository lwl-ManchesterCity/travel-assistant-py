"""PreferenceStore 的 Postgres 实现。SQL 全在这里，别处不写 SQL。

接口与内存实现见 preference_store.py；那个文件里也讲了为什么要有两个实现。
"""

from ..db import connection
from .preference_store import DEFAULT_SUBJECT, PreferenceRecord

# 两处 select 的是同一组列，顺序也必须一致，_to_record 按位置取。
_COLUMNS = "id, user_id, content, source, subject, created_at"


class PostgresPreferenceStore:
    def add(self, user_id: int, content: str, source: str,
            subject: str = DEFAULT_SUBJECT) -> bool:
        # 靠 unique (user_id, subject, content) 去重，不用先 select 再 insert：那样两个请求
        # 同时到会双写。`on conflict do nothing` 冲突时一行都不返回，fetchone() 拿到
        # None —— 这就是"已经有一条了"。
        #
        # **on conflict 后面那三列必须与表上的唯一键逐字对齐**：只写 (user_id, content)
        # 而表上是三列键的话，冲突判据不一样，写不进去也看不出来。改成第五档的时候
        # 这里和 schema.sql 是一起改的。
        with connection() as conn:
            row = conn.execute(
                f"insert into preferences (user_id, content, source, subject) "
                f"values (%s, %s, %s, %s) "
                f"on conflict (user_id, subject, content) do nothing returning {_COLUMNS}",
                (user_id, content, source, subject),
            ).fetchone()
        return row is not None

    def list_for_user(self, user_id: int) -> list[PreferenceRecord]:
        # 按 id 排而不是 created_at：同一毫秒内写进去的两条靠时间戳分不出先后，
        # 而 id 是 bigserial，天然就是写入顺序。
        with connection() as conn:
            rows = conn.execute(
                f"select {_COLUMNS} from preferences where user_id = %s order by id",
                (user_id,),
            ).fetchall()
        return [_to_record(row) for row in rows]


def _to_record(row: tuple) -> PreferenceRecord:
    return PreferenceRecord(
        id=row[0], user_id=row[1], content=row[2], source=row[3], subject=row[4],
        created_at=row[5])
