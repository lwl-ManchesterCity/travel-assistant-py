"""数值与取值的小工具：四舍五入、列表渲染、从模型返回的 JSON 里安全取值。"""

import math


def round_half_up(value: float) -> int:
    """四舍五入到整数：`floor(x + 0.5)`。

    **别换成内建的 `round`**：那个是银行家舍入，`round(2.5)` 得 2、`round(3.5)` 得 4。
    预算、时长、公里数这些要摆在用户眼前的数字，得按大家习惯的那种进上去。
    """
    return math.floor(value + 0.5)


def round1(value: float) -> float:
    """保留一位小数，同样走四舍五入。路线里程用它。"""
    return round_half_up(value * 10.0) / 10.0


def render_list(values: list[object] | None) -> str:
    """把一串值渲染成 `[美食, 自然风景]` 的样子，给提示词用。

    `None` 当空列表——调用方多半直接从请求里取那一格，而它可能是空的。
    """
    return "[" + ", ".join(str(value) for value in (values or [])) + "]"


def node_as_text(value: object, default: str) -> str:
    """从模型返回的 JSON 里安全地取一个字符串，取不到就用 `default`。

    逐类取值口径（**是一份契约**，提示词的逐字一致性靠它）：文本取原文、
    布尔取 `true`/`false`、数字取字面量、JSON `null` 取字面量 `"null"`、
    对象与数组取空串；算出来是空串才回落到 `default`。

    留意 `null` 那条：它**不回落到 `default`**，而是变成字符串 `"null"`。
    调用方若要区分"这一格没有"和"这一格是 null"，取完得自己判。
    """
    if value is None:
        return "null"
    if isinstance(value, str):
        text = value
    elif isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (int, float)):
        text = str(value)
    else:
        text = ""
    return text if text else default
