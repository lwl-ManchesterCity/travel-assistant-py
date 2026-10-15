"""长期偏好的清洗、去重与读取。设计见 docs/ASK_LONGTERM_MEMORY.md §2.2.2。

两条链（追问、规划）都只经过这里，不直接碰 Store——清洗与上限是**一处**的规矩，
分两处写迟早会分叉，而分叉的那天表现为"聊天里记的会去重、表单里记的不会"。
"""

from dataclasses import dataclass, field

from ..clients.preference_store import PreferenceStore

# 一条偏好的长度上限。**这不是洁癖**：模型偶尔会把用户整句话当偏好吐出来
# （「我希望这次去杭州的时候能顺便看看西湖」）。那不是偏好，是一条被截断的行程
# 要求，存进去之后**每一次**规划都会被带上。
MAX_CONTENT_CHARS = 40

# 一次最多取几条。用户一口气说八条，前五条大概率才是他真正在意的，
# 后三条往往是抱怨的复述。
MAX_PER_CALL = 5

# 一个人最多留几条。偏好**进每一次 prompt**，没上限的话话多的用户
# （或一次注入攻击）能把系统提示词撑爆。
MAX_PER_USER = 50

# 尾部的标点。模型吐「不吃辣。」「不吃辣！」是常态，带上标点就和
# 「不吃辣」变成两条，去重直接失效。
_TRAILING_PUNCTUATION = "。．，、！？；：!?;:,.…~～ 　\t\"'“”‘’（）()【】[]"


def clean_content(item: object, max_chars: int) -> str:
    """规范化一条记忆原文：strip、削尾部标点、卡长度。不合规返回空串。

    **偏好与场景（episode_service.py）两个 service 共用这一份**，只有 max_chars
    不同（40 / 60）。尾标点那张表要是各写一份，迟早一边改了另一边没改——
    那时的表现是"聊天里记的会去重、表单里记的不会"这一类，得从两个文件对起。
    """
    if not isinstance(item, str):
        return ""
    content = item.strip()
    # 尾标点可能不止一个（「不吃辣！！」），循环削到不动为止。
    content = content.rstrip(_TRAILING_PUNCTUATION)
    content = content.strip()
    if not content or len(content) > max_chars:
        return ""
    return content


@dataclass
class RecordResult:
    """一次记录的结果。

    `added` 是**真正新存进去的**——重复说的不算。前端那行「已记住」只报它，
    每次都弹会变成噪音，用户会开始无视它，那比不显示更糟（§2.2.7）。

    `dropped_for_cap` 是撞上 50 条上限被丢掉的条数，调用方拿去记一条留痕。
    单独一个字段而不是塞进 added：上限是"系统没照做"，与"用户说的是哪几条"
    不是一回事，混在一起前端会把它显示成「已记住」。
    """

    added: list[str] = field(default_factory=list)
    dropped_for_cap: int = 0

    @property
    def overflowed(self) -> bool:
        return self.dropped_for_cap > 0


class PreferenceService:

    def __init__(self, store: PreferenceStore):
        self._store = store

    def record(self, user_id: int, contents: list[str] | None, source: str) -> RecordResult:
        """把模型/表单抽出的一批偏好落库，**只返回真正新存进去的**。

        任何一步出问题都不抛：记偏好是搭便车的乘客，不能让它把车开翻——
        规划失败和偏好没记住，代价差着一个量级（§2.2.4）。
        """
        result = RecordResult()
        if user_id is None or not contents:
            return result
        cleaned = self._clean_batch(contents)
        if not cleaned:
            return result

        # 现有那几条一次读出来，两件事都用它：**撞上限的计数**，以及**判重**。
        # 顺序要紧——先判重再判上限：满了之后又说一遍已经有的，那是"重复"
        # 不是"溢出"，报成溢出会让留痕说"丢弃了 N 条"，而其实一条都没丢。
        rows = self._store.list_for_user(user_id)
        existing = {row.content for row in rows}
        kept = len(rows)
        for content in cleaned:
            if content in existing:
                continue
            if kept >= MAX_PER_USER:
                result.dropped_for_cap += 1
                continue
            if self._store.add(user_id, content, source):
                result.added.append(content)
                existing.add(content)
                kept += 1
        return result

    def list_for_injection(self, user_id: int) -> list[str]:
        """注入提示词用的那几条原文，按写入先后。没有就是空列表——**空列表不是错误**。"""
        if user_id is None:
            return []
        return [row.content for row in self._store.list_for_user(user_id)]

    @staticmethod
    def _clean_batch(contents: list[str]) -> list[str]:
        """清洗 + 本批内去重 + 截到 5 条。**顺序不能反**：先截 5 条的话，
        前 5 条里若有两条清洗后变成同一条，实际就只记了 4 条。"""
        cleaned: list[str] = []
        for item in contents:
            content = PreferenceService._clean(item)
            if content and content not in cleaned:
                cleaned.append(content)
        return cleaned[:MAX_PER_CALL]

    @staticmethod
    def _clean(item: object) -> str:
        """规范化一条。不合规（空的、太长的）返回空串，由调用方丢掉。"""
        return clean_content(item, MAX_CONTENT_CHARS)


def build_injection_block(preferences: list[str] | None,
                          episodes: list[str] | None = None,
                          episode_city: str | None = None) -> str:
    """把长期记忆原文拼成进提示词的那一段。**两条链共用这一份**（§2.2.6）。

    偏好与场景（§2.4.5）两段，都进同一个位置。**两样都没有时返回空串**，
    调用方据此跳过——不要拼一段"暂无长期记忆"塞进去，
    那等于每轮都提醒模型"这里本来该有点什么"。

    场景那一段只在**本次这个城市**有东西时出现：取的时候就是按城市选的
    （§2.4.3），所以到这里一定同属一个城市，一行 `城市：A；B` 就够。
    """
    preferences = preferences or []
    episodes = episodes or []
    if not preferences and not episodes:
        return ""
    lines: list[str] = []
    if preferences:
        lines.append(f"【这个人的长期偏好】{'、'.join(preferences)}")
        lines.append("这些是他在过去某次说过的、关于**他自己**的偏好，不限于本趟。"
                     "回答与规划时都要照顾到。")
    if episodes:
        city = episode_city or "本次目的地"
        lines.append(f"【他去过的地方，提到过的事】{city}：{'；'.join(episodes)}")
        lines.append("这些是他**上次**在这个城市遇到过的事，**已经发生过**，不是这一趟的要求。"
                     "该提就提（他上次踩过的坑别再让他踩一遍），但不要把它们当成这次的行程安排。")
    lines.append(
        "两条规矩：\n"
        "1. **本次明确说的优先。** 他这一次填的表单、这一句里说的话，跟上面冲突时按本次的来，"
        "并且**当场说一句你按本次的改了**——不要默默改掉，他会以为你忘了。\n"
        "2. **冲突要说出来。** 上面这几条跟当前方案内容对不上时（方案里排了一家辣菜馆，"
        "而他记着不吃辣），必须指出来，不许顺着方案说「这家不错」。\n"
    )
    return "\n".join(lines)
