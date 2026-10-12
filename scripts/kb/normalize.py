"""第 2 步：把 fixture 离线转换成第一、二层字段（对应设计文档 4.2）。

第一层（高德直接给）：amap_poi_id / name / city / location / type
第二层（能推导）：category（由 type 映射）、部分 tags、duration_min

**不联网**，纯函数，输入是 fixture，输出是骨架记录。
第三层（ticket_price / hours / description / source_url）由 build_kb.py合并进来。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# 4.3 受控词表里的「类型」，值与 data/tags.json 对齐。
# 每个标签配一组「高德 type 末级词」——只匹配 type 的最后一段（高德 type 形如
# 「风景名胜;公园广场;公园」，末级才是真正的细分类型），不再对整串做 in匹配。
# 之前用 `any(kw in type_ or kw in name)` 导致「南宋御街中山路步行街」因为含「山」
# 被打成「山」标签——「中山路」不是山。
TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("博物馆", ("博物馆", "展览馆", "美术馆", "艺术馆", "博物院", "纪念馆", "科技馆", "名人故居")),
    ("公园", ("公园", "植物园", "动物园", "游乐园", "湿地公园", "遗址公园", "城市广场", "度假区")),
    ("古镇", ("古镇", "历史文化街区", "商业街", "步行街", "老街", "历史街区")),
    ("山", ("山", "峰", "岭", "峡谷", "风景区")),
    ("湖", ("湖", "江", "河", "溪", "泉", "瀑布", "岛")),
)

# 4.1 约束：category 必须对齐现有 Attraction.category（amap_attraction._map_type_to_category）。
# 判定顺序与它一致：风景/公园 → 自然风景，博物馆/寺庙等 → 人文历史，餐饮购物 → 美食购物，
# 兜底城市漫游。
#
# 与运行时的**已知差异**（有意为之，记录在此以免被当成 bug 顺手「修回去」）：
# 本表多认了 图书馆/纪念馆/展览馆/艺术馆/科技馆 五类归人文历史。运行时那五个会落到
# 「城市漫游」，因为它的规则里只有 博物馆/古迹/寺庙/名胜 四个词——高德把图书馆的type
# 写成「科教文化服务;图书馆;图书馆」，一个都不匹配。图书馆显然不是「城市漫游」，
# 而门票估算（人文历史 50 vs 城市漫游 30）与 planner 的人文向打分都跟着 category 走。
# 本单红线是不改travel_assistant/ 下的现有代码，所以差异只在这张离线表里存在。
# 下一轮接检索时若要消除，应改的是 amap_attraction._map_type_to_category，两边一起改。
CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("自然风景", ("风景", "公园", "景点", "湿地", "动物园", "植物园")),
    ("人文历史", ("博物馆", "古迹", "寺庙", "名胜", "博物院", "美术馆", "故居",
                  "图书馆", "纪念馆", "展览馆", "艺术馆", "科技馆")),
    ("美食购物", ("餐饮", "购物", "商场", "商业")),
)

DURATION_BY_CATEGORY: dict[str, int] = {
    "自然风景": 180,
    "人文历史": 150,
    "美食购物": 90,
    "城市漫游": 120,
}

# 4.2 第二层：type 的末级细分含这些词就是室内景点，与高德无关，靠类型词判定。
INDOOR_KEYWORDS: tuple[str, ...] = (
    "博物馆", "展览馆", "美术馆", "图书馆", "艺术馆", "博物院", "纪念馆",
)

_CLOSED_DAY_ORDER: tuple[str, ...] = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
# 时段分隔符实测有五种写法：半角 -、全角 –(U+2013)/―(U+2015)、波浪 ~、至。
# 漏掉全角会让「周二–周日 09:00–17:00」整条解析失败——实测中国丝绸博物馆与良渚博物院都栽在这里。
_TIME_RANGE = re.compile(r"(\d{1,2}:\d{2})\s*[-–—―~至]\s*(\d{1,2}:\d{2})")
_CLOSED_DAY = re.compile(r"(周[一二三四五六日天])")

# 高德把「全天开放」写成 00:00-24:00。它不是开放时段，是占位——
# 存进hours 会让追问「几点关门」得到一个荒谬答案。实测 27/30 条是这个值。
_ALL_DAY_PLACEHOLDER = ("00:00-24:00", "0:00-24:00", "00:00-24:00 ")

# 与travel_assistant/clients/amap_attraction.py 的 _is_valid_attraction_poi 同一套排除口径。
# 除此之外还排除交通设施与生活服务：实测「楼观台」首条返回「起名馆」、
# 「龙井村」首条返回「公交站」，只看原口径还不够。
_BANNED_TYPE_KEYWORDS: tuple[str, ...] = (
    "公司企业", "餐饮服务", "金融保险服务", "汽车服务", "汽车维修", "汽车销售",
    "房地产", "商务住宅", "医疗保健服务", "政府机构及社会团体",
    "交通设施服务", "生活服务", "住宿服务", "餐饮", "门址信息",
)
_BANNED_NAME_KEYWORDS: tuple[str, ...] = (
    "有限公司", "公司", "奶茶", "火锅", "烧烤", "公交站", "地铁站", "停车场",
    "起名馆", "服务区", "售票处", "游客中心", "便利店", "银行", "医院", "学校",
)
# 这些 type 说明它是个地名/路段而不是可游览的实体，收进来会让表里出现
# 「河坊街（道路名）」这种查不到门票也查不到时长的噪声。
_BANNED_TYPE_PREFIXES: tuple[str, ...] = ("地名地址信息", "商务住宅")


def map_type_to_category(type_: str) -> str:
    """高德 type → 四个合法 category。

    先看末级细分，再看整串，顺序不能反。
    高德的 type 是一级;二级;三级三段式，寺庙写成「风景名胜;风景名胜;寺庙道观」——
    「风景名胜」是一级词，整串匹配会先命中「风景」把category 定成「自然风景」，
    灵隐寺、岳王庙、六和塔会被全部误判。末级才是真正的细分类型，所以先判末级。
    """
    segments = [segment.strip() for segment in (type_ or "").split(";") if segment.strip()]
    leaf = segments[-1] if segments else ""
    for category, keywords in CATEGORY_RULES:
        if leaf and any(keyword in leaf for keyword in keywords):
            return category
    for category, keywords in CATEGORY_RULES:
        if any(keyword in (type_ or "") for keyword in keywords):
            return category
    return "城市漫游"


def duration_for_category(category: str) -> int:
    return DURATION_BY_CATEGORY.get(category, 120)


def derive_tags(type_: str, name: str, category: str) -> list[str]:
    """只推第二层能确定的属性类 tag。场景类（雨天/亲子/夜游…）留给第三层。

    宁少勿多：推错的 tag 比没有 tag 更坏，它会污染向量检索的 metadata。
    「室内」与「类型」标签都只看高德 type 的**末级**细分类型，不看整串也不看名字——
    名字里「中山路」「西湖」都有误导性；整串里则可能混进一级词（如「风景名胜」）造成误判。
    """
    tags: list[str] = []
    segments = [segment.strip() for segment in (type_ or "").split(";") if segment.strip()]
    leaf = segments[-1] if segments else ""

    if any(keyword in leaf for keyword in INDOOR_KEYWORDS):
        tags.append("室内")
    for tag, keywords in TYPE_KEYWORDS:
        if any(keyword in leaf for keyword in keywords):
            tags.append(tag)
            break
    return tags


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
    # 「周二–周日 09:00–17:00开放 最晚进入16:30」整段都因此被跳过，hours 变null。
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


def _parse_location(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, str) or "," not in raw:
        return None
    lng, _, lat = raw.partition(",")
    try:
        return {"lng": float(lng), "lat": float(lat)}
    except ValueError:
        return None


def _make_id(city: str, name: str, poi_id: str) -> str:
    """稳定主键：城市拼音-短名拼音。不许用 name 直接当主键（会随官方改名而变）。

    不引入 pypinyin：这是一个一次性离线作业，为十几条 id 拼音化不值得给项目加依赖。
    城市用固定映射（目前只做杭州），景点部分用高德 poi_id 的小写短码——
    poi_id 本身已经是稳定的官方标识，比拼音更不容易撞。
    """
    city_part = _CITY_PINYIN.get(city, "hz" if city.startswith("杭州") else "city")
    short = "".join(char for char in poi_id.lower() if char.isalnum())[-8:]
    return f"{city_part}-{short}"


_CITY_PINYIN: dict[str, str] = {"杭州": "hz"}


def is_pickable_poi(poi: dict) -> bool:
    """这条 POI 是不是一个可游览的实体（而不是公交站/起名馆/道路名）。"""
    type_ = str(poi.get("type") or "")
    name = str(poi.get("name") or "")
    if any(prefix in type_ for prefix in _BANNED_TYPE_PREFIXES):
        return False
    if any(keyword in type_ for keyword in _BANNED_TYPE_KEYWORDS):
        return False
    if any(keyword in name for keyword in _BANNED_NAME_KEYWORDS):
        return False
    return bool(name.strip()) and bool(str(poi.get("id") or "").strip())


def relevance_score(poi: dict, keyword: str) -> int:
    """候选打分：同名 > 名字含关键词 > 高德 match 字段 > 评分。分数相同按 poi_id 排序保稳定。"""
    name = str(poi.get("name") or "")
    score = 0
    if name == keyword:
        score += 100
    elif keyword in name:
        score += 60
    elif keyword in str(poi.get("address") or "") or keyword in str(poi.get("business_area") or ""):
        score += 20
    match_field = str(poi.get("match") or "")
    if match_field in ("exact", "keyword"):
        score += 30
    if poi.get("biz_ext", {}).get("opentime2") if isinstance(poi.get("biz_ext"), dict) else None:
        score += 5
    rating = poi.get("biz_ext", {}).get("rating") if isinstance(poi.get("biz_ext"), dict) else None
    try:
        score += int(float(str(rating)) * 2)
    except (TypeError, ValueError):
        pass
    return score


def pick_candidate(pois: list[dict], keyword: str, expect_name: str | None = None) -> dict | None:
    """从一组 POI 里挑最像「这个关键词本身」的那一条。挑不出就返回 None。

    expect_name 优先：规格表里已经写明期望命中哪条时，按名字挑比按分数猜可靠。
    没有 expect_name 才退回打分。
    """
    pickable = [poi for poi in pois if is_pickable_poi(poi)]
    if not pickable:
        return None
    if expect_name:
        exact = [poi for poi in pickable if str(poi.get("name") or "").strip() == expect_name]
        if exact:
            return exact[0]
        # 只接受「期望名被包含」，不接受反向：expect_name="岳王庙" 时
        # 「岳王庙-东庑」长度更短，用双向匹配会选中景区里的一个子建筑。
        # 命中多个时取最短名：expect_name="杭州白塔公园" 会同时命中
        # 「杭州白塔公园」和「杭州白塔公园-儿童游乐园」，后者是子项目不是景点。
        contained = [poi for poi in pickable if expect_name in str(poi.get("name") or "")]
        if contained:
            return min(
                contained,
                key=lambda poi: (len(str(poi.get("name") or "")), -relevance_score(poi, keyword), str(poi.get("id"))),
            )
    return max(pickable, key=lambda poi: (relevance_score(poi, keyword), str(poi.get("id"))))


def poi_to_skeleton(
    poi: dict, city: str, category_override: str | None = None, fallback_index: int = 0
) -> dict | None:
    """一条高德 POI → 第一、二层骨架记录。关键字段缺失返回 None。"""
    name = str(poi.get("name") or "").strip()
    poi_id = str(poi.get("id") or "").strip()
    location = _parse_location(poi.get("location"))
    if not name or not poi_id or location is None:
        return None

    type_ = str(poi.get("type") or "").strip()
    category = category_override or map_type_to_category(type_)
    biz_ext = poi.get("biz_ext") if isinstance(poi.get("biz_ext"), dict) else {}
    hours, closed_days = parse_opentime2(biz_ext.get("opentime2"))
    if hours is None:
        # open_time 是简版时段（实测覆盖 55%），作为 opentime2 缺失时的备份。
        hours, _ = parse_opentime2(biz_ext.get("open_time"))

    record_id = _make_id(city, name, poi_id)
    if not record_id.split("-", 1)[1]:
        record_id = f"{record_id}-{fallback_index:02d}"

    rating = None
    raw_rating = biz_ext.get("rating")
    if raw_rating not in (None, "", [], [""]):
        try:
            rating = float(str(raw_rating))
        except ValueError:
            rating = None

    return {
        "id": record_id,
        "amap_poi_id": poi_id,
        "name": name,
        "aliases": _build_aliases(poi),
        "city": city,
        "location": location,
        "tags": derive_tags(type_, name, category),
        "category": category,
        "duration_min": duration_for_category(category),
        "description": None,       # 第三层
        "ticket_price": None,     # 第三层：null = 未知，绝不用 0
        "hours": hours,
        "closed_days": closed_days,
        "rating": rating,
        "type": type_,
        "address": _build_address(poi),
        "tel": str(poi.get("tel") or "").strip() or None,
        "website": str(poi.get("website") or "").strip() or None,
        "biz_ext_cost": biz_ext.get("cost"),
        "source_url": None,       # 第三层
        "updated_at": None,       # 第三层
    }


def _build_aliases(poi: dict) -> list[str]:
    raw = poi.get("alias")
    if isinstance(raw, list):
        return [str(item).strip() for item in raw if str(item).strip()]
    if isinstance(raw, str) and raw.strip() and raw.strip() != "[]":
        return [part.strip() for part in re.split(r"[;；,，]", raw) if part.strip()]
    return []


def _build_address(poi: dict) -> str:
    parts = [
        str(poi.get(key) or "").strip()
        for key in ("pname", "cityname", "adname", "address", "business_area")
    ]
    return "".join(part for part in parts if part)


def fixture_to_skeletons(
    fixture: dict,
    specs: tuple[dict[str, str | None], ...] | None = None,
    one_per_keyword: bool = True,
) -> list[dict]:
    """整份fixture → 骨架列表，按 amap_poi_id 去重，保持稳定顺序（可重复运行）。

    one_per_keyword=True 时每个关键词只取一个最佳候选——这是设计文档第五节第 1 步要的
    「一个城市的核心景点 30 个」，而不是把 302 条 POI 全部收进来。
    specs 给了就走规格表的 expect_name 与 category 覆写；没给就退回纯打分。
    """
    from scripts.kb.keywords import ATTRACTION_SPECS

    specs = specs if specs is not None else ATTRACTION_SPECS
    by_keyword = {str(spec["keyword"]): spec for spec in specs}

    skeletons: list[dict] = []
    seen: set[str] = set()
    # 按规格表顺序而不是 fixture 的 key 排序：输出顺序要稳定且可预期。
    for spec in specs:
        keyword = str(spec["keyword"])
        entry = fixture.get(keyword.replace(" ", "_"))
        if entry is None:
            continue
        city = entry.get("city") or "杭州"
        pois = (entry.get("response") or {}).get("pois") or []
        if one_per_keyword:
            candidate = pick_candidate(pois, keyword, spec.get("expect_name"))
            pois = [candidate] if candidate else []
        for index, poi in enumerate(pois):
            skeleton = poi_to_skeleton(
                poi, city, category_override=spec.get("category"), fallback_index=index
            )
            if skeleton is None:
                continue
            if skeleton["amap_poi_id"] in seen:
                continue
            seen.add(skeleton["amap_poi_id"])
            skeletons.append(skeleton)
    return skeletons


def load_fixture(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))