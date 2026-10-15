"""情节记忆（场景）的清洗、去重与读取。设计见 docs/ASK_LONGTERM_MEMORY.md §2.4.2。

**与 PreferenceService 是两个平行的类**，不合并、不继承：写入点的形状不同
（场景多一个城市），闸门也不同（长度 60 对 40）。两样差别都靠参数塞进一个类里，
读的人要先解一堆开关才看得懂"到底存了什么"。

共用的那一小段规范化在 preference_service.clean_content——尾标点那张表只许有一份。

两条链（追问、规划）都只经过这里，不直接碰 Store，理由同 §2.2.2。
"""

from dataclasses import dataclass, field

from ..clients.episode_store import EpisodeStore
from .preference_service import clean_content

# 一条场景的长度上限。**比偏好的 40 宽**：偏好多是短语（「不吃辣」），
# 场景是一句话（「上回在成都第二天排太满」/「上次那家酒店隔音差」），
# 同一个数会把合理的情节砍掉。
MAX_CONTENT_CHARS = 60

# 城市名的那道闸门。设计里没写，是防模型把整句话塞进 city（那一格一旦被污染，
# 这条场景就永远挂在一个查不出来的城市下，等于白存）。20 字对任何城市名都够宽。
MAX_CITY_CHARS = 20

# 一次最多取几条、一个人最多留几条。理由同 §2.2.2：它进 prompt，没上限能把提示词撑爆。
MAX_PER_CALL = 5
MAX_PER_USER = 50


@dataclass
class EpisodeRecordResult:
    """一次记录的结果。`added` 里的每条是 **(city, content) 两元组**。

    与 `RecordResult.added`（一串纯文本）不同：场景的定义里就带城市，
    只回内容的话，留痕里"记到哪个城市去了"就丢了——而那正是这一档最容易错的地方。

    `dropped_for_cap` 同 §2.2.2：上限是"系统没照做"，与"用户说的是哪几条"不是一回事。
    """

    added: list[tuple[str, str]] = field(default_factory=list)
    dropped_for_cap: int = 0

    @property
    def overflowed(self) -> bool:
        return self.dropped_for_cap > 0


class EpisodeService:

    def __init__(self, store: EpisodeStore):
        self._store = store

    def record(self, user_id: int | None, items: object, source: str) -> EpisodeRecordResult:
        """把模型抽出的一批场景落库，**只返回真正新存进去的**。

        `items` 是 `[{"city": …, "content": …}, …]`，也就是信封里那一格的原样。
        任何一步出问题都不抛：记记忆是搭便车的乘客，不能让它把车开翻（§2.2.4）。

        上限**按人算、不按城市算**（§2.4.2），所以读的是 `list_for_user` 而不是
        `list_for_city`——只数本城市的话，跑十个城市就能存下 500 条。
        """
        result = EpisodeRecordResult()
        if user_id is None or not items:
            return result
        cleaned = self._clean_batch(items)
        if not cleaned:
            return result

        # 现有那几条一次读出来，两件事都用它：**撞上限的计数**，以及**判重**。
        # 顺序要紧——先判重再判上限（§2.2.11 记过的那次教训）：满了之后又说一遍
        # 已经有的，那是"重复"不是"溢出"，报成溢出会让留痕说"丢弃了 N 条"，
        # 而其实一条都没丢。
        rows = self._store.list_for_user(user_id)
        existing = {(row.city, row.content) for row in rows}
        kept = len(rows)
        for city, content in cleaned:
            if (city, content) in existing:
                continue
            if kept >= MAX_PER_USER:
                result.dropped_for_cap += 1
                continue
            if self._store.add(user_id, city, content, source):
                result.added.append((city, content))
                existing.add((city, content))
                kept += 1
        return result

    def list_for_city(self, user_id: int | None, city: str | None) -> list[str]:
        """注入提示词用的那几条原文，按写入先后。没有城市 / 没登录 / 没有 → 空列表。

        **空列表不是错误**：绝大多数城市他都没去过，那是常态。
        """
        if user_id is None or not city:
            return []
        return [row.content for row in self._store.list_for_city(user_id, city)]

    @staticmethod
    def _clean_batch(items: object) -> list[tuple[str, str]]:
        """清洗 + 本批内去重 + 截到 5 条。**顺序不能反**（同 §2.2.2）：
        先截 5 条的话，前 5 条里若有两条清洗后变成同一条，实际就只记了 4 条。"""
        if not isinstance(items, list):
            return []
        cleaned: list[tuple[str, str]] = []
        for item in items:
            pair = EpisodeService._clean_item(item)
            if pair is not None and pair not in cleaned:
                cleaned.append(pair)
        return cleaned[:MAX_PER_CALL]

    @staticmethod
    def _clean_item(item: object) -> tuple[str, str] | None:
        """一条场景 → (city, content)。不合规返回 None。

        **城市为空就整条丢**（§2.4.1）：取的时候按城市捞，没地点就永远捞不出来，
        存了等于没存——而且它还占着 50 条的上限，用户却以为记下了。
        """
        if not isinstance(item, dict):
            return None
        city = clean_content(item.get("city"), MAX_CITY_CHARS)
        content = clean_content(item.get("content"), MAX_CONTENT_CHARS)
        if not city or not content:
            return None
        return city, content
