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

# `subject` 的缺省值，也是**"这条是用户本人的"那个标记**（docs/ASK_LONGTERM_MEMORY.md §2.5.1）。
# 注入时按它把偏好分成两段（本人 / 同行的人，§2.5.4），所以它不只是个默认值——
# **改这个字符串要同时改 schema.sql 里那个 column default**（那里是字面量，改不了引用）。
DEFAULT_SUBJECT = "本人"


def render_preference(subject: str | None, content: str) -> str:
    """一条偏好渲染成给人看的那串：**本人的只给内容，别人的带上是谁**。

    回执（答案下面那行「已记住：…」）、规划留痕、注入块里的同行那一段、以及第三档的
    列表，全走这一份——同一条偏好在四个地方得长一样，否则用户对不上。
    只给「不能吃辣」的话，他妻子那条会被他当成自己的。
    """
    if not subject or subject == DEFAULT_SUBJECT:
        return content
    return f"{subject}{content}"


@dataclass(frozen=True)
class PreferenceRecord:
    """库里的一行 preferences。"""

    id: int
    user_id: int
    content: str
    source: str
    # 这条是**谁的**（第五档）。自由文本，不做归一化：老婆可能每趟都在、朋友就那一趟，
    # 这个区别本身就是有用的信息（§2.5.1）。等于 DEFAULT_SUBJECT 就是用户本人的。
    subject: str = DEFAULT_SUBJECT
    created_at: datetime | None = None

    @property
    def display_text(self) -> str:
        """给前端「已记住：…」和留痕用的那一段。前端不直接读 dataclass，见 to_payload。"""
        return render_preference(self.subject, self.content)

    def to_payload(self) -> dict:
        """过 API 的那一份（第三档的列表用，见 §2.3.2）。

        - **带 `id`**：删除按它删，**不按 `content`**。两个都能唯一定位，但 content 是
          给人看的那一份、经过模型规范化与清洗，而 id 是主键——按它删没有
          "前端显示的那串跟库里那串是否逐字相同"这个隐忧。
        - **带 `source`**：用户判断哪条记错了，第一个线索就是"这条哪来的"。
        - **带 `subject`**：不回的话，同行的人那条在界面上跟本人的长得一样，会删错。
        - **不带 `created_at`**：界面上不显示，服务端也没有要它对齐的场景——
          给前端没人用的字段等于多一个将来要维护的兼容面。
        """
        return {"id": self.id, "content": self.content, "source": self.source,
                "subject": self.subject}


class PreferenceStore(Protocol):
    def add(self, user_id: int, content: str, source: str,
            subject: str = DEFAULT_SUBJECT) -> bool:
        """存一条。**已经有一条一模一样的就返回 False**（不算错误，是正常去重）。

        判重按 **(user_id, subject, content)**，与表上的 unique 约束同一套判据。
        `subject` 放在最后并给缺省：**老调用方（只有本人的年代写的）不用改就还是本人的**，
        而"缺省即本人"正好也是 §2.5.5 要的那条兜底。
        """
        ...

    def list_for_user(self, user_id: int) -> list[PreferenceRecord]:
        """这个人的全部偏好，**按写入先后**（旧的在前）。

        注入时按这个顺序拼进提示词，所以顺序要稳定——它进的是 prompt，
        顺序一变，同样两条偏好会让模型看到不同的东西。
        """
        ...

    def delete(self, user_id: int, preference_id: int) -> bool:
        """删一条，删掉了返回 True（§2.3.3）。

        **`user_id` 是这个方法存在的理由**：`id` 是连续 bigserial，只按 id 删的话，
        任何一个登录用户能删光**全站**的偏好，而且界面上看不出来（他只是在删自己的）。
        删别人的 / 已经不存在的一律回 False，调用方据此回 `deleted: false`——
        **两种情形不许区分**，否则就是一个"这个 id 存在"的探针。
        """
        ...


class InMemoryPreferenceStore:
    """给测试用的实现。行为要和 PostgresPreferenceStore 对得上，否则测试守不住真库。"""

    def __init__(self) -> None:
        self._rows: dict[int, list[PreferenceRecord]] = {}
        self._next_id = 1

    def add(self, user_id: int, content: str, source: str,
            subject: str = DEFAULT_SUBJECT) -> bool:
        rows = self._rows.setdefault(user_id, [])
        # 判重按三列：同一句话挂在两个角色下是两条（§2.5.2）。
        if any(row.content == content and row.subject == subject for row in rows):
            return False
        rows.append(PreferenceRecord(
            id=self._next_id, user_id=user_id, content=content, source=source,
            subject=subject))
        self._next_id += 1
        return True

    def list_for_user(self, user_id: int) -> list[PreferenceRecord]:
        return list(self._rows.get(user_id, []))

    def delete(self, user_id: int, preference_id: int) -> bool:
        rows = self._rows.get(user_id)
        if not rows:
            return False
        for index, row in enumerate(rows):
            if row.id == preference_id:
                del rows[index]
                return True
        return False
