"""受控词表：给提示词用。

词表存在的意义是**限制**，不是给程序查的字典（第五节 5.1）——
没有这层约束，迟早会同时收到「雨天」「下雨天」「适合雨天」三种说法，筛选就废了。

所以本模块对外最重要的不是 `vocabulary_words()`，是 `describe_vocabulary()`：
把词表渲染成一行塞进提示词，让模型只能在这几个词里挑。
"""

from __future__ import annotations

import json

from .paths import TAGS_PATH


def load_tag_vocabulary() -> dict[str, list[str]]:
    return json.loads(TAGS_PATH.read_text(encoding="utf-8"))


def vocabulary_words() -> list[str]:
    """所有词，按分组顺序拉平。"""
    return [word for words in load_tag_vocabulary().values() for word in words]


def describe_vocabulary() -> str:
    """渲染成一行给提示词用。

    → 场景：夜游/亲子/避暑/拍照；属性：室内/免费/需预约；类型：博物馆/公园/古镇
    """
    raw = load_tag_vocabulary()
    return "；".join(f"{group}：{'/'.join(words)}" for group, words in raw.items())
