"""与 Java 语义对齐的辅助函数。"""

import math


def jround(value: float) -> int:
    """Java Math.round(double) 的语义：floor(x + 0.5)。"""
    return math.floor(value + 0.5)


def round1(value: float) -> float:
    """Java AmapRouteClient/RuleBasedRouteClient 里的 round(double)：保留一位小数。"""
    return jround(value * 10.0) / 10.0


def java_list_to_string(values: list[object] | None) -> str:
    """Java List.toString() 的渲染形式：[美食, 自然风景]，用于保持 LLM 提示词逐字一致。"""
    return "[" + ", ".join(str(value) for value in (values or [])) + "]"


def node_as_text(value: object, default: str) -> str:
    """Jackson JsonNode.asText(defaultValue) 的取值语义。

    文本取原文、布尔取 true/false、数字取字面量、JSON null 取字符串 "null"、
    对象与数组取空串，空串再回落到 defaultValue。
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
