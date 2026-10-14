"""长期偏好的存取接口，外加一个内存实现。

分层与理由照抄 auth_store.py（那里写得更详细）：

    单元测试 → InMemoryPreferenceStore（本文件）
    真服务   → PostgresPreferenceStore（postgres_preference_store.py，container.py 装配）

真 SQL 由 tests/test_preference_store_postgres.py（连不上就 skip）和端到端探针覆盖。

**这里只存不判**：什么算偏好（「你好」不算）、是这个人的还是这一趟的，都是模型按
ask_agent 规则 13 / RequirementAnalysisService 的提示词判的；清洗与上限在
service/preference_service.py。Store 只管"这一条进没进去"。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# 两个来源。记错的时候第一个要问的就是"这条哪来的"，而两个写入点的输入长得
# 完全不同（一个是表单自由文本、一个是聊天原话），出问题全靠它定位。
SOURCE_FORM = "form"
SOURCE_CHAT = "chat"


@dataclass(frozen=True)
class PreferenceRecord:
    """库里的一行 preferences。"""

    id: int
    user_id: int
    content: str
    source: str
    created_at: datetime | None = None

    @property
    def display_text(self) -> str:
        """给前端「已记住：…」和留痕用的那一段。前端不直接读 dataclass，见 to_payload。"""
        return self.content

    def to_payload(self) -> dict:
        """过 API 的那一份。带 source 是给第三档的列表用的，前端现在只读 content。"""
        return {"content": self.content, "source": self.source}


class PreferenceStore(Protocol):
    def add(self, user_id: int, content: str, source: str) -> bool:
        """存一条。**已经有一条一模一样的就返回 False**（不算错误，是正常去重）。

        判重按 (user_id, content)，与表上的 unique 约束同一套判据。
        """
        ...

    def list_for_user(self, user_id: int) -> list[PreferenceRecord]:
        """这个人的全部偏好，**按写入先后**（旧的在前）。

        注入时按这个顺序拼进提示词，所以顺序要稳定——它进的是 prompt，
        顺序一变，同样两条偏好会让模型看到不同的东西。
        """
        ...


class InMemoryPreferenceStore:
    """给测试用的实现。行为要和 PostgresPreferenceStore 对得上，否则测试守不住真库。"""

    def __init__(self) -> None:
        self._rows: dict[int, list[PreferenceRecord]] = {}
        self._next_id = 1

    def add(self, user_id: int, content: str, source: str) -> bool:
        rows = self._rows.setdefault(user_id, [])
        if any(row.content == content for row in rows):
            return False
        rows.append(PreferenceRecord(
            id=self._next_id, user_id=user_id, content=content, source=source))
        self._next_id += 1
        return True

    def list_for_user(self, user_id: int) -> list[PreferenceRecord]:
        return list(self._rows.get(user_id, []))
