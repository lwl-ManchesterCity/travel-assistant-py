"""判分引擎：把「一道题的期望」和「一次真实的响应」对上。

这个文件是整个评测集里唯一需要动脑的地方，`run.py` 和 `questions.yaml` 都只是搬运。

判分的第一原则：**优先看结构化字段，尽量别去读那句答案的措辞。**

为什么——`scripts/opening_hours_probe.py` 判据 5 连踩三回的教训：一开始写
「答案里必须有『查不到』」，模型说「都没给到」→ 红；改成列举，它说「未查询到」→
又红；说「都没有查到」→ 再红。**描述性措辞是个开放词表，永远追不上模型换说法。**

所以能拿结构化字段就拿。下面这三样都是服务端**已经归一化过的**，模型怎么措辞都不影响：

    意图        响应体的 `intent` 字段
    记没记      响应体的 `recordedPreferences`
    调没调工具  `traces[].agentName`

只有万不得已才读答案，且一律写成**封闭**形式：

    答里有否定   没 / 未 / 不 / 无法 / 暂不 —— 封闭集合，见 NEGATIONS
    答里不许有   否命题（「不得出现 HH:MM」比「必须出现『查不到』」好判得多）

**编新题时先自问**：这条期望能不能改成看 `intent` / `recordedPreferences` / `traces`？
能就改。实在改不了再读答案。
"""

from __future__ import annotations

import re
from pathlib import Path

QUESTIONS_PATH = Path(__file__).resolve().parent / "questions.yaml"

# 「如实说没有」这类判据用的否定语素。**这是个封闭集合**——一个把广州塔的开放时间
# 直接安在「广州塔旋转餐厅」头上的答案，里面不会有这几个字；反过来，只要模型是在
# 说「没有」，不管它用哪种说法，都会落到这五个里面。
NEGATIONS = ("没", "未", "不", "无法", "暂不")

# 认得的断言键。写错一个键名（比如把「不该调」写成「不要调」）会**静默失效**——
# 题看着过了，其实那条断言压根没跑。所以要显式白名单，见 `_unknown_keys`。
KNOWN_KEYS = frozenset({
    "问", "类别", "城市", "历史", "说明",
    "意图", "该调其一", "不该调", "不该调任何工具",
    "记了", "有锚点", "答里要有", "答里有否定", "答里不许有",
})


def load_questions(path: Path | None = None) -> list[dict]:
    """读题库。同时做**结构自检**，把写错的题当场喊出来。"""
    import yaml

    rows = yaml.safe_load((path or QUESTIONS_PATH).read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"题库顶层该是一个列表，实际是 {type(rows).__name__}")

    for index, row in enumerate(rows, start=1):
        where = f"第 {index} 题「{row.get('问', '?')}」"
        unknown = set(row) - KNOWN_KEYS
        if unknown:
            # 不 raise 成错误，是因为一行写错不该让整个评测集跑不起来；但必须吵。
            row.setdefault("_警告", []).append(f"{where} 有认不得的键：{'、'.join(sorted(unknown))}")
        if not row.get("问"):
            raise ValueError(f"{where} 没有「问」")
        if not row.get("类别"):
            raise ValueError(f"{where} 没有「类别」（分类统计要用）")
        if not any(key in row for key in KNOWN_KEYS - {"问", "类别", "城市", "历史", "说明"}):
            raise ValueError(f"{where} 一条期望都没写，这题永远会过，等于没出")
        for key in ("该调其一", "不该调"):
            for name in row.get(key) or []:
                if name not in TOOL_NAMES:
                    # 工具名写错一个字母的后果最阴：`该调其一` 永远凑不齐、`不该调`
                    # 永远不命中，两条都**静静判过**。所以宁可在加载时炸。
                    raise ValueError(
                        f"{where} 的「{key}」里写了「{name}」，不是 7 个工具之一。"
                        f"写错工具名会让这条断言静默失效。")
    return rows


def judge(case: dict, body: dict) -> list[str]:
    """判一道题。返回**失败原因**列表；空列表 = 过。

    返回原因而不是 bool，是因为「38/50 过」这个数本身没用——
    有用的是**没过的那几条为什么没过**。
    """
    reasons: list[str] = []
    calls = _called_tools(body)
    answer = body.get("answer") or ""
    recorded = body.get("recordedPreferences") or []

    if "意图" in case and body.get("intent") != case["意图"]:
        reasons.append(f"意图：要 {case['意图']}，实际 {body.get('intent')}")

    if expected := case.get("该调其一"):
        if not (set(expected) & calls):
            reasons.append(f"该调其一：{'/'.join(expected)} 一个都没调，实际调了 {_show(calls)}")

    if forbidden := case.get("不该调"):
        hit = set(forbidden) & calls
        if hit:
            reasons.append(f"不该调：不该调 {'/'.join(sorted(hit))}，却调了")

    if case.get("不该调任何工具") and calls:
        reasons.append(f"不该调任何工具：却调了 {_show(calls)}")

    if "记了" in case:
        if case["记了"] and not recorded:
            reasons.append("记了：期望记一条长期偏好，实际一条没记")
        if not case["记了"] and recorded:
            reasons.append(f"记了：期望**不**记，实际记了 {recorded}")

    if case.get("有锚点") and not (body.get("anchorHint") or "").strip():
        reasons.append("有锚点：期望给出 anchorHint（用户说了住宿位置），实际是空的")

    if required := case.get("答里要有"):
        missing = [word for word in required if word not in answer]
        if missing:
            reasons.append(f"答里要有：答案里没有 {'、'.join(missing)}")

    if case.get("答里有否定") and not any(word in answer for word in NEGATIONS):
        reasons.append(f"答里有否定：答案里没有否定语素（{'/'.join(NEGATIONS)}），像是硬答了")

    for pattern in case.get("答里不许有") or []:
        if match := re.search(pattern, answer):
            reasons.append(f"答里不许有：答案里出现了 {match.group(0)!r}（匹配 {pattern}）")

    return reasons


def _called_tools(body: dict) -> set[str]:
    """本轮真调过的工具名。

    `AskAgent` / `MemoryService` 那两条非工具留痕也在 traces 里，但它们是**每次都会有**的，
    混进来会让「不该调任何工具」永远判否——所以这里只保留工具名，判据里显式列出来的那些。
    """
    return {t.get("agentName") for t in body.get("traces") or [] if t.get("agentName") in TOOL_NAMES}


# 7 个工具的名字。**硬编码在这里是故意的**：题库里若把工具名写错一个字母，
# 那条断言就会静默失效（「该调其一」永远凑不齐、「不该调」永远不命中）。
# 硬编码 + `load_questions` 的自检，才能在写错的当下就发现。
# 改工具名时这里要跟着改——这一条写进 README 了。
TOOL_NAMES = frozenset({
    "get_plan_overview",
    "get_day_plan",
    "get_attraction_detail",
    "search_attractions",
    "search_attractions_by_tag",
    "query_weather",
    "recommend_hotels",
})


def _show(names: set[str]) -> str:
    return "、".join(sorted(names)) or "（空）"
