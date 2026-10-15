"""长期偏好的清洗、去重与读取。设计见 docs/ASK_LONGTERM_MEMORY.md §2.2.2。

两条链（追问、规划）都只经过这里，不直接碰 Store——清洗与上限是**一处**的规矩，
分两处写迟早会分叉，而分叉的那天表现为"聊天里记的会去重、表单里记的不会"。
"""

from dataclasses import dataclass, field

from ..clients.preference_store import (
    DEFAULT_SUBJECT,
    PreferenceRecord,
    PreferenceStore,
    render_preference,
)

# 一条偏好的长度上限。**这不是洁癖**：模型偶尔会把用户整句话当偏好吐出来
# （「我希望这次去杭州的时候能顺便看看西湖」）。那不是偏好，是一条被截断的行程
# 要求，存进去之后**每一次**规划都会被带上。
MAX_CONTENT_CHARS = 40

# 角色名的长度闸门（第五档）。角色是「本人」「老婆」「我妈」这种，10 字对谁都够宽；
# 它防的是模型把整句话塞进 subject——那一格被污染，这条偏好就会被归到一段
# 根本不存在的"同行的人"里，从而**有条件地不生效**（§2.5.4 那条规矩）。
MAX_SUBJECT_CHARS = 10

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

    def record(self, user_id: int, items: object, source: str) -> RecordResult:
        """把模型/表单抽出的一批偏好落库，**只返回真正新存进去的**。

        `items` 是 `[{"subject": "本人", "content": "不吃辣"}, …]`，也就是信封里那一格的
        原样；**也收裸字符串**，那种按「本人」兜（§2.5.5）——老模型、老调用方只吐一串
        文本，不能让它们变成"记不上"。

        任何一步出问题都不抛：记偏好是搭便车的乘客，不能让它把车开翻——
        规划失败和偏好没记住，代价差着一个量级（§2.2.4）。
        """
        result = RecordResult()
        if user_id is None or not items:
            return result
        cleaned = self._clean_batch(items)
        if not cleaned:
            return result

        # 现有那几条一次读出来，两件事都用它：**撞上限的计数**，以及**判重**。
        # 顺序要紧——先判重再判上限：满了之后又说一遍已经有的，那是"重复"
        # 不是"溢出"，报成溢出会让留痕说"丢弃了 N 条"，而其实一条都没丢。
        #
        # 判重的键是 **(subject, content)** 两列，与库上那个三列唯一键（含 user_id）
        # 同一套判据。「本人不吃辣」和「老婆不吃辣」是两条，**不许撞**（§2.5.2）。
        rows = self._store.list_for_user(user_id)
        existing = {(row.subject, row.content) for row in rows}
        kept = len(rows)
        for subject, content in cleaned:
            if (subject, content) in existing:
                continue
            if kept >= MAX_PER_USER:
                result.dropped_for_cap += 1
                continue
            if self._store.add(user_id, content, source, subject):
                # **回执也走 render_preference**（§2.5.6）：added 是"答案下面那行
                # 「已记住：…」"的来源，裸给 content 的话，两个角色说同一句话时
                # 屏幕上会长得一模一样——而那是两个人。
                result.added.append(render_preference(subject, content))
                existing.add((subject, content))
                kept += 1
        return result

    def list_for_user(self, user_id: int | None) -> list[PreferenceRecord]:
        """这个人的全部偏好**原样**（含 id 与 subject），按写入先后。给第三档的列表用。

        与 `list_belonging` / `list_companions` 的分工：那两个是**注入**用的
        （分两段、只给内容）；这一个要的是"库里到底有什么"，包括每条的 `id`——
        删除按它删（§2.3.2）。少了 id，前端只能按内容拼，那就把"显示的那串
        跟库里那串是否逐字相同"变成了一个隐含前提。
        """
        if user_id is None:
            return []
        return list(self._store.list_for_user(user_id))

    def forget(self, user_id: int | None, preference_id: int | None) -> bool:
        """删掉这个人的一条偏好。删别人的 / 已经不存在的都回 False（§2.3.3）。

        **走 Service 而不是让控制器直接碰 Store**：删除既没有清洗也没有上限，
        直接写 Store 看着更省事——但那是"两条链都只经过 Service"这个约束的
        **第一个口子**（见本文件开头）。规矩破一次，下一个改动就有先例了。

        **异常不吞**：与 `record` 相反。记偏好失败可以算了（搭便车的乘客）；
        删除失败不能回一句 `deleted: false`——那是在撒谎，用户会以为库里没有了。
        """
        if user_id is None or preference_id is None:
            return False
        return self._store.delete(user_id, preference_id)

    def list_belonging(self, user_id: int) -> list[str]:
        """**用户本人**的偏好原文，按写入先后。没有就是空列表——**空列表不是错误**。"""
        if user_id is None:
            return []
        return [row.content for row in self._store.list_for_user(user_id)
                if row.subject == DEFAULT_SUBJECT]

    def list_companions(self, user_id: int) -> list[str]:
        """**同行的人**的偏好，按写入先后，每条**带上是谁**（「老婆不能吃辣」）。

        第五档之前这一份不存在（别人的整条不记，§1.2 第 3 条）；现在记了，就必须
        带着角色一起注入：只给「不能吃辣」，模型会当成用户自己的。
        """
        if user_id is None:
            return []
        return [render_preference(row.subject, row.content)
                for row in self._store.list_for_user(user_id)
                if row.subject != DEFAULT_SUBJECT]

    @staticmethod
    def _clean_batch(items: object) -> list[tuple[str, str]]:
        """清洗 + 本批内去重 + 截到 5 条。**顺序不能反**：先截 5 条的话，
        前 5 条里若有两条清洗后变成同一条，实际就只记了 4 条。"""
        if not isinstance(items, list):
            return []
        cleaned: list[tuple[str, str]] = []
        for item in items:
            pair = PreferenceService._clean_item(item)
            if pair is not None and pair not in cleaned:
                cleaned.append(pair)
        return cleaned[:MAX_PER_CALL]

    @staticmethod
    def _clean_item(item: object) -> tuple[str, str] | None:
        """一条偏好 → (subject, content)。不合规（空的、太长的）返回 None。

        **subject 缺了 / 空白按「本人」兜**（§2.5.5）：宁可当成他本人的（第五档之前
        所有条目本来也都是本人的），也不要凭空造一个同行的人——那会让这条偏好
        变成"有条件才生效"。
        """
        if isinstance(item, str):
            subject, content = DEFAULT_SUBJECT, item
        elif isinstance(item, dict):
            subject, content = item.get("subject"), item.get("content")
        else:
            return None
        cleaned_subject = clean_content(subject, MAX_SUBJECT_CHARS) or DEFAULT_SUBJECT
        cleaned_content = clean_content(content, MAX_CONTENT_CHARS)
        if not cleaned_content:
            return None
        return cleaned_subject, cleaned_content

    @staticmethod
    def _clean(item: object) -> str:
        """只取内容那一半。留着是给别处单点清洗用。"""
        pair = PreferenceService._clean_item(item)
        return pair[1] if pair else ""


def render_extracted(items: object) -> list[str]:
    """把抽出来的那一批（信封 / 备注那一格的形状：`[{"subject":…, "content":…}]`）
    渲染成给人看的一串。裸字符串也收（老模型 / 老提示词）。

    留痕用它，走的是与 PreferenceRecord.display_text 同一份 render_preference——
    **本人只给内容、别人的带上是谁**，这样留痕里一眼能看出"这条记给谁了"。
    """
    if not isinstance(items, list):
        return []
    rendered: list[str] = []
    for item in items:
        if isinstance(item, str):
            text = item.strip()
        elif isinstance(item, dict):
            text = render_preference(item.get("subject"), item.get("content") or "")
        else:
            continue
        if text:
            rendered.append(text)
    return rendered


def build_injection_block(belonging: list[str] | None,
                          companions: list[str] | None = None,
                          episodes: list[str] | None = None,
                          episode_city: str | None = None) -> str:
    """把长期记忆原文拼成进提示词的那一段。**两条链共用这一份**（§2.2.6）。

    三段：**本人的偏好**、**同行的人的偏好**（§2.5.4 拆出来的）、**本城市发生过的场景**
    （§2.4.5）。都进同一个位置。**三段都空时返回空串**，调用方据此跳过——
    不要拼一段"暂无长期记忆"塞进去，那等于每轮都提醒模型"这里本来该有点什么"。

    场景那一段只在**本次这个城市**有东西时出现：取的时候就是按城市选的
    （§2.4.3），所以到这里一定同属一个城市，一行 `城市：A；B` 就够。
    """
    belonging = belonging or []
    companions = companions or []
    episodes = episodes or []
    if not belonging and not companions and not episodes:
        return ""
    lines: list[str] = []
    if belonging:
        lines.append(f"【这个人的长期偏好】{'、'.join(belonging)}")
        lines.append("这些是他在过去某次说过的、关于**他自己**的偏好，不限于本趟。"
                     "回答与规划时都要照顾到。")
    if companions:
        lines.append(f"【同行的人的偏好】{'、'.join(companions)}")
        # 这一条**没有代码兜底**，与 §2.2.6 的「表单优先」同类：系统不知道这一趟谁同行，
        # 判不了，就只能写成一句给模型的话。
        lines.append("这些是**同行的人**的，不是他的——每条前面写着是谁。"
                     "**只在本趟确实有这个人时才生效**（他这次提到了这个人，"
                     "或者这一趟本来就是这个组合）；不确定就别硬套，"
                     "但也别默默丢掉，说一句你不确定。")
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
