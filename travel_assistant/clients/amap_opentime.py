"""解析高德 POI 的 `biz_ext.opentime2`：一个字段里同时含开放时段与闭馆日。

这段逻辑原本只住在 `scripts/kb/normalize.py`（离线建库那条链）。追问侧要现查开放时间
时也得用它，而 `scripts/` 是离线工具链的地盘——运行时反向 import 它是错的方向，
所以搬到这里，`scripts/kb/normalize.py` 改成从这里再导出。

只引 `re`：不引 chromadb、不引 torch。`kb/store.py` 顶部那条约定
（别为了写进工具说明就往服务启动里塞 0.3s + 35M）在这里同样适用。
"""

from __future__ import annotations

import re
from typing import Any

_CLOSED_DAY_ORDER: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
# 时段分隔符实测有五种写法：半角 -、全角 –(U+2013)/―(U+2015)、波浪 ~、至。
# 漏掉全角会让「周二–周日 09:00–17:00」整条解析失败——实测中国丝绸博物馆与良渚博物院都栽在这里。
_TIME_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*[-–—―~至]\s*(\d{1,2}:\d{2})")

# 高德把「全天开放」写成 00:00-24:00。对 `parse_opentime2` 来说它不是开放时段，是占位——
# 那个函数要的是 HH:MM-HH:MM 形状的时段，收进去会得到一个荒谬的 hours。
_ALL_DAY_PLACEHOLDER = ("00:00-24:00", "0:00-24:00", "00:00-24:00 ")

# 但**对追问侧它不是占位、是答案**：「全天开放」回答「几点关门」完全够用。
# 所以那边在 `hours` 落空时用 `is_all_day` 再认一次，填成这个值。
ALL_DAY = "全天开放"
_ALL_DAY_TOKENS = _ALL_DAY_PLACEHOLDER + ("全天开放", "全天营业", "24小时开放", "24小时营业")


def is_all_day(raw: Any) -> bool:
    """这条 `opentime2` / `open_time` 说的是不是「全天开放」。

    两种写法都算：`00:00-24:00` 这个占位，和直接写「全天开放」。
    **要在 `parse_opentime2` 返回 `hours=None` 之后再问它**——那个函数会主动跳过占位，
    它返回 None 不等于"高德什么都没说"，也可能正是"全天开放"。
    """
    if not isinstance(raw, str):
        return False
    return any(token in raw for token in _ALL_DAY_TOKENS)


def parse_opentime2(raw: Any) -> tuple[str | None, list[str]]:
    """从 biz_ext.opentime2 抽 (hours, closed_days)。

    第 0 步实测：opentime2 在杭州样本里覆盖 90%，内容形如
    「周二至周日 09:00-17:00开放 最晚进入16:30； 周一 全天不开放(遇法定节假日正常开放…)」。
    它同时含时段与闭馆日，所以 hours 与 closed_days 都能从这里拿，不必联网。

    返回的 hours 必须是 HH:MM-HH:MM：取第一个完整时段，并剔除「最晚进入16:30」这类
    干扰项——它也是 HH:MM-HH:MM 形状但不是开放时段。
    """
    if not isinstance(raw, str):
        return None, []
    text = raw.strip()
    if not text or text == "[]":
        return None, []

    # 先把「最晚进入16:30」「停止入园16:00」这类干扰短语整段剥掉。
    # 它们的形状也是 HH:MM-HH:MM，不剥掉会被当成开放时段——实测三个博物馆的
    # 「周二–周日 09:00–17:00开放 最晚进入16:30」整段都因此被跳过，hours 变 null。
    text = re.sub(r"最晚[^；;，,]*", " ", text)
    text = re.sub(r"(停止入园|停止进入|停止售票|最晚售票)[^；;，,]*", " ", text)

    hours: str | None = None
    closed_days: list[str] = []

    for clause in re.split(r"[；;]", text):
        clause = clause.strip()
        if not clause:
            continue
        # 一个子句可能同时含时段与闭馆日（「周二至周日 09:00-17:00 周二 全天不开放」），
        # 所以这里不整段跳过，只把闭馆关键词之后的部分切掉，剩下的照常找时段。
        for closing in re.finditer(r"(不开放|闭馆|闭园|全天关闭|停止开放|休息)", clause):
            before = clause[: closing.start()]
            after = clause[closing.end():]
            closed_days.extend(_closed_days_in(before))
            clause = before
            clause = f"{clause} {after}".strip() if after.strip() else clause
            if hours is None:
                match = _TIME_RANGE.search(clause)
                if match:
                    candidate = f"{_pad(match.group(1))}-{_pad(match.group(2))}"
                    if candidate not in _ALL_DAY_PLACEHOLDER:
                        hours = candidate
            break

        if hours is None:
            match = _TIME_RANGE.search(clause)
            if match:
                candidate = f"{_pad(match.group(1))}-{_pad(match.group(2))}"
                if candidate not in _ALL_DAY_PLACEHOLDER:
                    hours = candidate

    closed_days = list(dict.fromkeys(closed_days))
    closed_days.sort(key=lambda day: _CLOSED_DAY_ORDER.index(day) if day in _CLOSED_DAY_ORDER else 99)
    return hours, closed_days


def _closed_days_in(before: str) -> list[str]:
    """从闭馆关键词**之前**的那段文字里取星期。

    只取紧邻的那几个，不能把整句的星期都算进来：「周二至周日 09:00-17:00 周二 全天不开放」
    里「周二至周日」是开放区间，只有最后那个「周二」是闭馆日。

    做法：先剥掉尾部的时段与日期，剩下的若整段都是「星期+分隔符」的组合就全收；
    否则从右往左逐个星期吃，撞上「至」或非星期内容就停。
    """
    tail = before.rstrip()
    # 剥掉尾部的前缀词与时段日期：「…周一 全天」→「…周一」。
    # 「全天」必须剥：闭馆关键词是「全天不开放」时，before 的尾巴是「全天」而不是星期，
    # 不剥就会挡住后面真正要取的那个星期。
    while True:
        stripped = re.sub(
            r"(?:[\d:：/\-–—―~年月日号第.\s]+|(?:全天|每天|全年|全日|节假日|法定节假日)+)$",
            "",
            tail,
        )
        if stripped == tail:
            break
        tail = stripped
    if not tail:
        return []

    # 整段都是星期列表（「周一、周三」「周一,周三至周日」）——直接全收。
    if re.fullmatch(r"(?:周[一二三四五六日天]\s*[、,，/]?\s*)+", tail):
        return ["周日" if day == "周天" else day for day in re.findall(r"周[一二三四五六日天]", tail)]

    # 否则只从右往左吃紧邻的星期，撞上「至」停：「周二至周日」里的周日是区间右端。
    days: list[str] = []
    remainder = tail
    while True:
        match = re.search(r"周[一二三四五六日天]$", remainder)
        if not match:
            break
        days.append("周日" if match.group() == "周天" else match.group())
        remainder = remainder[: match.start()].rstrip()
        if remainder.endswith("至") or remainder.endswith("-") or not remainder:
            break
    return days


def _pad(time_str: str) -> str:
    hour, _, minute = time_str.partition(":")
    return f"{int(hour):02d}:{minute}"
