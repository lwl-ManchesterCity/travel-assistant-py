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

# 解析器本体 2026-10-16 搬到了运行时：追问侧要现查开放时间，而 scripts/ 是离线工具链的地盘，
# 运行时反过来 import 它是错的方向（见 docs/ASK_LIVE_HOURS.md §3.1）。
# 这里只是**原样再导出**——离线这条链与 tests/test_attraction_kb.py 的 import 一个字都不用改。
from travel_assistant.clients.amap_opentime import ALL_DAY, is_all_day, parse_opentime2  # noqa: F401

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

# 开放时间那套（_CLOSED_DAY_ORDER / _TIME_RANGE / _ALL_DAY_PLACEHOLDER）与 parse_opentime2
# 一起搬到了 travel_assistant.clients.amap_opentime，见顶部那条 import。
# 顺带丢掉了 _CLOSED_DAY：定义了但全库没有任何地方用过，死代码不跟着搬。

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


def _parse_location(raw: Any) -> dict[str, float] | None:
    if not isinstance(raw, str) or "," not in raw:
        return None
    lng, _, lat = raw.partition(",")
    try:
        return {"lng": float(lng), "lat": float(lat)}
    except ValueError:
        return None


def _make_id(city: str, poi_id: str) -> str:
    """稳定主键：{城市slug}-{poi_id 小写短码}。

    不引入 pypinyin：这是一个一次性离线作业，为几十条 id 拼音化不值得给项目加依赖。
    poi_id 本身已经是稳定的官方标识（设计文档 4.1「与高德对齐的外键」），
    比拼音更不容易撞。

    城市前缀取不到时走sha1 兜底，**不能**退化成字面量 "city"——
    那样四座城会共用一个前缀，主键就失去了区分城市的能力。
    """
    from scripts.kb.keywords import city_id_prefix

    city_part = city_id_prefix(city)
    short = "".join(char for char in poi_id.lower() if char.isalnum())[-8:]
    return f"{city_part}-{short}"


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
    if hours is None and (is_all_day(biz_ext.get("opentime2"))
                          or is_all_day(biz_ext.get("open_time"))):
        # **两格都空，不等于「没数据」**：高德把全天开放写成 `00:00-24:00`（或
        # `24小时营业`），而上面两句走的 `parse_opentime2` 要的是 `HH:MM-HH:MM`，
        # 于是把它当占位符跳过了——**西湖、河坊街、什刹海、南锣鼓巷这类全天开放的地方
        # 全折在这一句上**（实测 138 条里 36 条 hours 是空的，其中 16 条名字带
        # 「公园/广场/街/湿地」）。
        # 运行时那条链 (`amap_attraction._parse_opentime`) 早就补了这层，离线这条漏了——
        # 所以「网页上现查能答出来、库里却存着空」。改完必须**重新生成一遍** data/attractions.json。
        hours = ALL_DAY

    record_id = _make_id(city, poi_id)
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
    city: str | None = None,
    specs: tuple[dict[str, str | None], ...] | None = None,
    one_per_keyword: bool = True,
) -> list[dict]:
    """整份 fixture → 骨架列表，按 amap_poi_id 去重，保持稳定顺序（可重复运行）。

    one_per_keyword=True 时每个关键词只取一个最佳候选——这是设计文档第五节第 1 步要的
    「一个城市的核心景点 30 个」，而不是把 302 条 POI 全部收进来。

    specs 没给时**按 city 从keywords.SPECS_BY_CITY 取对应那张表**，不写死杭州：
    写死的话拿北京的 fixture 也会去套杭州的 expect_name，挑出一批错的 POI 且不报错。
    """
    from scripts.kb.keywords import specs_for

    if specs is None:
        resolved_city = city
        if resolved_city is None:
            # 从 fixture 里反查城市：每条 entry 都记了抓的时候传的 --city。
            entries = [entry for entry in fixture.values() if isinstance(entry, dict)]
            cities = {str(entry.get("city")) for entry in entries if entry.get("city")}
            if len(cities) == 1:
                resolved_city = cities.pop()
        specs = specs_for(resolved_city or "")

    skeletons: list[dict] = []
    seen: set[str] = set()
    # 按规格表顺序而不是 fixture 的 key 排序：输出顺序要稳定且可预期。
    for spec in specs:
        keyword = str(spec["keyword"])
        entry = fixture.get(keyword.replace(" ", "_"))
        if entry is None:
            continue
        entry_city = entry.get("city") or city or "杭州"
        pois = (entry.get("response") or {}).get("pois") or []
        if one_per_keyword:
            candidate = pick_candidate(pois, keyword, spec.get("expect_name"))
            pois = [candidate] if candidate else []
        for index, poi in enumerate(pois):
            skeleton = poi_to_skeleton(
                poi, entry_city, category_override=spec.get("category"), fallback_index=index
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