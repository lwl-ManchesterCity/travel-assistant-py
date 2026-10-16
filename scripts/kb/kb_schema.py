"""景点资料库的 schema、受控词表与校验规则（对应 docs/ATTRACTION_KB_DESIGN.md 第四、七节）。

本模块是纯离线工具，被scripts/kb/ 下的建表脚本与 tests/ 下的校验用例共用。
校验必须在不启动服务、不依赖容器装配的前提下能单独跑。

**路径的归属改过一次**（2026-10-11）：本文原写着"不 import travel_assistant 的任何东西"，
理由是这张表是运行时链路之外的一次性产物。接入追问检索那一轮起，`tags.json` 成了
**运行时输入**（提示词要拿词表约束模型只能在这几个词里挑），路径的唯一所有者跟着搬到
`travel_assistant/kb/paths.py`，本文改为从那里 import。
`attractions.json` 的归属没变，仍是纯离线；上面那条"能单独跑"的性质也没变——
`travel_assistant.kb.paths` 只有 pathlib，不引 chromadb、不引 torch。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from travel_assistant.kb.paths import (  # noqa: F401 - DATA_DIR/REPO_ROOT 是本模块对外的老接口
    ATTRACTIONS_PATH,
    DATA_DIR,
    REPO_ROOT,
    TAGS_PATH,
)

# 4.1 约束：category 必须对齐现有 Attraction.category 的四个合法值，
# 现有打分（planner_agent._map_type_to_category 的下游）与门票估算全依赖它。
VALID_CATEGORIES = ("自然风景", "人文历史", "美食购物", "城市漫游")

# 4.1 约束：hours 先当字符串存，不做结构化（不分旺季淡季、不分时段）。
# 形状只有两种：`09:00-17:00` 这种时段，和「全天开放」。
# **「全天开放」必须收**：高德把全天写成 `00:00-24:00` / `24小时营业`，解析器统一归成
# `amap_opentime.ALL_DAY` 那一个词（西湖、河坊街、什刹海这类全折在这一格上）。
# 但只收这一个词——「全天」「24小时营业」这些原样写法照样拒，形状别放开。
HOURS_PATTERN = re.compile(r"^(?:\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}|全天开放)$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
URL_PATTERN = re.compile(r"^https?://[^\s]+$")

# 闭馆日的受控写法。normalize 产出的就是这七个，但校验器要独立守住这个入口——
# 补数据的人可能手写「星期一」，而下游按「周一」比对就会漏掉闭馆。
VALID_CLOSED_DAYS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

DESCRIPTION_MAX_LEN = 200


class ValidationIssue(Exception):
    """一条记录的校验失败。message 里必须写清是哪条规则、哪个字段。"""

    def __init__(self, rule: int, field: str, message: str, record_id: str | None = None) -> None:
        self.rule = rule
        self.field = field
        self.record_id = record_id
        prefix = f"[规则{rule}] {field}"
        if record_id:
            prefix = f"{prefix} ({record_id})"
        super().__init__(f"{prefix}: {message}")


def load_tag_vocabulary(path: Path = TAGS_PATH) -> set[str]:
    """读受控词表，返回三个数组的并集。tags 不允许自由文本，否则检索立刻崩。"""
    raw = json.loads(path.read_text(encoding="utf-8"))
    vocabulary: set[str] = set()
    for group in ("场景", "属性", "类型"):
        values = raw.get(group)
        if not isinstance(values, list) or not values:
            raise ValidationIssue(1, "tags.json", f"分组 {group} 缺失或不是非空数组")
        vocabulary.update(str(value) for value in values)
    return vocabulary


def all_tag_vocabulary(path: Path = TAGS_PATH) -> set[str]:
    return load_tag_vocabulary(path)


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def validate_record(record: dict, vocabulary: set[str], seen_poi_ids: set[str] | None = None) -> list[str]:
    """校验单条记录，返回告警列表（可自动修复的）；不可修复的问题抛 ValidationIssue。

    第七节 7 条规则里，1/6 是「丢弃并告警」，2/3/4/5/7 是「失败」——
    区别在于前者丢一个 tag 或截断一段文字不影响这条记录成立，后者会让下游拿到脏数据。
    """
    warnings: list[str] = []
    record_id = str(record.get("id") or "<无 id>")

    for field in ("id", "amap_poi_id", "name", "city"):
        if _is_blank(record.get(field)):
            raise ValidationIssue(4 if field == "amap_poi_id" else 5, field, "必填字段为空", record_id)

    poi_id = str(record["amap_poi_id"])
    if seen_poi_ids is not None:
        if poi_id in seen_poi_ids:
            raise ValidationIssue(4, "amap_poi_id", f"重复的 amap_poi_id: {poi_id}", record_id)
        seen_poi_ids.add(poi_id)

    # 规则 1：tags ⊆ 词表并集。不在词表内的丢弃并告警，不丢整条记录。
    tags = record.get("tags") or []
    if not isinstance(tags, list):
        raise ValidationIssue(1, "tags", "必须是数组", record_id)
    kept_tags: list[str] = []
    for tag in tags:
        text = str(tag).strip()
        if text in vocabulary:
            kept_tags.append(text)
        else:
            warnings.append(f"[规则1] {record_id} 丢弃词表外的 tag: {text!r}")
    kept_tags = list(dict.fromkeys(kept_tags))
    record["tags"] = kept_tags

    # 规则 2（本单最重要的一条）：缺失必须是 null，不是 0。
    # 0 表示「确定免费」，把未知写成 0 会让预算里门票那一桶静默少算，且不报错不告警。
    if "ticket_price" not in record:
        raise ValidationIssue(2, "ticket_price", "字段缺失，必须显式写 null 或整数", record_id)
    price = record["ticket_price"]
    # bool 是 int 的子类，isinstance(True, int) 为 True——不显式排除的话
    # JSON 里的 true 会被当成票价 1 通过校验，而它在语义上就是「未知」。
    if isinstance(price, bool) or (price is not None and not isinstance(price, int)):
        raise ValidationIssue(
            2, "ticket_price", f"必须是整数或 null，实际是 {type(price).__name__}", record_id
        )
    if isinstance(price, int) and price < 0:
        raise ValidationIssue(2, "ticket_price", f"票价不能为负: {price}", record_id)

    # 规则 3：source_url 非空且形如 URL。不合格丢整条——没有出处的数据不可用。
    source_url = record.get("source_url")
    if _is_blank(source_url) or not URL_PATTERN.match(str(source_url).strip()):
        raise ValidationIssue(3, "source_url", f"为空或不是合法 URL: {source_url!r}", record_id)

    # 规则 5：category ∈ 四个合法值。
    if record.get("category") not in VALID_CATEGORIES:
        raise ValidationIssue(
            5, "category", f"必须是 {VALID_CATEGORIES} 之一，实际是 {record.get('category')!r}", record_id
        )

    # 规则 6：description ≤ 200 字。超了截断并告警。
    description = record.get("description") or ""
    if len(str(description)) > DESCRIPTION_MAX_LEN:
        record["description"] = str(description)[:DESCRIPTION_MAX_LEN]
        warnings.append(f"[规则6] {record_id} description 超 {DESCRIPTION_MAX_LEN} 字，已截断")

    # 规则 7：updated_at 形如 YYYY-MM-DD。
    updated_at = record.get("updated_at")
    if _is_blank(updated_at) or not DATE_PATTERN.match(str(updated_at).strip()):
        raise ValidationIssue(7, "updated_at", f"必须是 YYYY-MM-DD，实际是 {updated_at!r}", record_id)

    if not isinstance(record.get("hours"), (str, type(None))):
        raise ValidationIssue(1, "hours", "必须是字符串或 null", record_id)
    hours = record.get("hours")
    if isinstance(hours, str) and hours.strip() and not HOURS_PATTERN.match(hours.strip()):
        raise ValidationIssue(1, "hours", f"必须形如 09:00-17:00，实际是 {hours!r}", record_id)

    # 规则 1：closed_days 只能取这七个写法。理由同 tags——「周一/星期一/礼拜一」
    # 三种混用会让「周一闭馆」的判断在检索侧失效，而它恰恰是最容易被追问的一格。
    closed_days = record.get("closed_days")
    if not isinstance(closed_days, list):
        raise ValidationIssue(1, "closed_days", "必须是数组，不确定填 []", record_id)
    for day in closed_days:
        if day not in VALID_CLOSED_DAYS:
            raise ValidationIssue(1, "closed_days", f"必须是 {VALID_CLOSED_DAYS} 之一，实际是 {day!r}", record_id)

    duration = record.get("duration_min")
    if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or duration <= 0):
        raise ValidationIssue(1, "duration_min", f"必须是正整数或 null，实际是 {duration!r}", record_id)

    # location 要校验取值而不只是键存在：{"lng": "abc", "lat": null} 能蒙过 in 检查，
    # 但下游算距离会直接抛异常。
    location = record.get("location")
    if not isinstance(location, dict) or "lng" not in location or "lat" not in location:
        raise ValidationIssue(5, "location", "必须是含 lng/lat 的对象", record_id)
    for axis, low, high in (("lng", 73.0, 135.0), ("lat", 3.0, 54.0)):
        value = location[axis]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationIssue(5, "location", f"{axis} 必须是数字，实际是 {type(value).__name__}", record_id)
        # 范围按中国境内取值域卡；超出说明坐标写串行了（如经纬度写反）。
        if not low <= float(value) <= high:
            raise ValidationIssue(5, "location", f"{axis} 超出中国境内取值域: {value}", record_id)

    return warnings


def validate_records(
    records: Iterable[dict], vocabulary: set[str] | None = None
) -> tuple[list[dict], list[str]]:
    """校验一批记录，返回 (合格记录, 全部告警)。重复的 amap_poi_id 会被剔除。"""
    vocabulary = vocabulary if vocabulary is not None else load_tag_vocabulary()
    accepted: list[dict] = []
    warnings: list[str] = []
    seen_poi_ids: set[str] = set()
    for record in records:
        try:
            warnings.extend(validate_record(record, vocabulary, seen_poi_ids))
            accepted.append(record)
        except ValidationIssue as issue:
            warnings.append(str(issue))
    return accepted, warnings


def load_attractions(path: Path = ATTRACTIONS_PATH) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))