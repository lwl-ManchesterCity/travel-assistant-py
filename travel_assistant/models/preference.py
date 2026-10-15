"""「我的偏好」两条端点的出参。对应 docs/ASK_LONGTERM_MEMORY.md §2.3.1。

照 models/auth.py 那个样子：CamelModel；删除回执仿 LogoutResult——
**有个字段而不是空 200**，前端好判断。
"""

from .base import CamelModel


class PreferenceItem(CamelModel):
    """列表里的一行。"""

    id: int
    content: str
    source: str
    # 这条是**谁的**（§2.5.1）。**必须回**：不回的话，同行的人那条在界面上跟本人的
    # 长得一模一样，用户会删错——而"删掉记错的那条"正是这个界面存在的全部意义。
    subject: str


class PreferenceList(CamelModel):
    """列表出参。包一层 `items` 而不是直接回数组：将来要加分页或总数时，
    加字段不用改前端的解析形状。"""

    items: list[PreferenceItem]


class PreferenceDeleteResult(CamelModel):
    """删除回执（§2.3.3）。删别人的 / 已经不存在的一律 `deleted=False`，**不区分**。"""

    deleted: bool = False
