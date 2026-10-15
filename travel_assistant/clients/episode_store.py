"""情节记忆（场景）的存取接口，外加一个内存实现。

分层与 preference_store.py 逐字对应（那里写了为什么要有两个实现）：

    单元测试 → InMemoryEpisodeStore（本文件）
    真服务   → PostgresEpisodeStore（postgres_episode_store.py，container.py 装配）

真 SQL 由 tests/test_episode_store_postgres.py（连不上就 skip）和端到端探针覆盖。

**与偏好的三点不同**（设计见 docs/ASK_LONGTERM_MEMORY.md §2.4）：

1. 多一个 `city`，而且**它不能为空**。取的时候按城市捞（§2.4.3），
   没地点就捞不出来——存了等于没存。所以这道闸门开在**写入端**，不在读取端。
2. 去重键是**三列**。「上次杭州那家隔音差」和「上次成都那家隔音差」是两条，不能撞。
3. 多一个 `list_for_user`。**上限是按人算的，不是按城市算的**（§2.4.2：每人最多 50 条），
   而注入只问一个城市——只要 `list_for_city` 的话，计数会漏掉别的城市的行。

**这里只存不判**：什么算场景、城市从哪来，都是模型按 ask_agent 规则 13 /
RequirementAnalysisService 的提示词判的；清洗与上限在 service/episode_service.py。
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

# 来源词汇（chat / form）与偏好共用同一套：它说的是**哪个写入点**（助手输入 / 表单备注），
# 不是哪张表。**定义只许有一处**，在 preference_store.py，这里转出来是让调用方与用例
# 从本模块就能拿到——不是另立一份。
from .preference_store import SOURCE_CHAT, SOURCE_FORM  # noqa: F401


@dataclass(frozen=True)
class EpisodeRecord:
    """库里的一行 episodes。"""

    id: int
    user_id: int
    city: str
    content: str
    source: str
    created_at: datetime | None = None


class EpisodeStore(Protocol):
    def add(self, user_id: int, city: str, content: str, source: str) -> bool:
        """存一条。**已经有 (user_id, city, content) 一模一样的就返回 False**
        （不算错误，是正常去重）。判据与表上的 unique 约束同一套。"""
        ...

    def list_for_user(self, user_id: int) -> list[EpisodeRecord]:
        """这个人的**全部**场景，按写入先后。上限计数用（见上面第 3 点）。"""
        ...

    def list_for_city(self, user_id: int, city: str) -> list[EpisodeRecord]:
        """这个人在**这个城市**的场景，按写入先后。注入用。

        与 list_for_user 同一个排序理由：它进的是 prompt，顺序一变，
        同样几条会让模型看到不同的东西。
        """
        ...


class InMemoryEpisodeStore:
    """给测试用的实现。行为要和 PostgresEpisodeStore 对得上，否则测试守不住真库。"""

    def __init__(self) -> None:
        self._rows: dict[int, list[EpisodeRecord]] = {}
        self._next_id = 1

    def add(self, user_id: int, city: str, content: str, source: str) -> bool:
        rows = self._rows.setdefault(user_id, [])
        if any(row.city == city and row.content == content for row in rows):
            return False
        rows.append(EpisodeRecord(
            id=self._next_id, user_id=user_id, city=city, content=content, source=source))
        self._next_id += 1
        return True

    def list_for_user(self, user_id: int) -> list[EpisodeRecord]:
        return list(self._rows.get(user_id, []))

    def list_for_city(self, user_id: int, city: str) -> list[EpisodeRecord]:
        return [row for row in self._rows.get(user_id, []) if row.city == city]
