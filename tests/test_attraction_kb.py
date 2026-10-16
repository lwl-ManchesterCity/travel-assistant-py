"""第七节 7 条校验规则的正反例（对应 docs/ATTRACTION_KB_DESIGN.md 第七节）。

规则 2（ticket_price 缺失必须是 null 而不是 0）是本单最重要的一条，
所以它的用例写得最密：0 表示确定免费、null 表示未知，两者混用会让预算静默算错。
"""

import json
import re

import pytest

from scripts.kb.build_kb import build, merge_skeleton
from scripts.kb.kb_schema import (
    ATTRACTIONS_PATH,
    HOURS_PATTERN,
    TAGS_PATH,
    ValidationIssue,
    load_tag_vocabulary,
    validate_record,
    validate_records,
)
from scripts.kb.normalize import (
    derive_tags,
    fixture_to_skeletons,
    map_type_to_category,
    parse_opentime2,
    pick_candidate,
    poi_to_skeleton,
)


@pytest.fixture(scope="module")
def vocabulary() -> set[str]:
    return load_tag_vocabulary()


def _record(**overrides) -> dict:
    record = {
        "id": "hz-test01",
        "amap_poi_id": "B0TEST0001",
        "name": "测试景点",
        "aliases": [],
        "city": "杭州",
        "location": {"lng": 120.1, "lat": 30.2},
        "tags": ["公园"],
        "category": "自然风景",
        "ticket_price": None,
        "hours": "09:00-17:00",
        "closed_days": ["周一"],
        "duration_min": 120,
        "description": "一段自然语言简介。",
        "source_url": "https://example.com/spot",
        "updated_at": "2026-10-10",
    }
    record.update(overrides)
    return record


# ---------- 规则 2：ticket_price（本单最重要） ----------

def test_rule2_null_ticket_price_is_accepted(vocabulary):
    """未知票价写成 null 是对的，必须放行。"""
    record = _record(ticket_price=None)
    assert validate_record(record, vocabulary) == []


def test_rule2_zero_ticket_price_is_accepted(vocabulary):
    """0 表示「确定免费」，是合法值，不能和 null 混为一谈。"""
    record = _record(ticket_price=0, description="免费开放的公园")
    assert validate_record(record, vocabulary) == []


def test_rule2_missing_key_is_rejected(vocabulary):
    """字段整个缺失也要失败——静默变成 0 就是这条规则要防的事。"""
    record = _record()
    del record["ticket_price"]
    with pytest.raises(ValidationIssue) as exc:
        validate_record(record, vocabulary)
    assert exc.value.rule == 2


@pytest.mark.parametrize("bad_price", ["免费", 0.0, -10, [], {}])
def test_rule2_non_integer_or_negative_is_rejected(vocabulary, bad_price):
    record = _record(ticket_price=bad_price)
    with pytest.raises(ValidationIssue) as exc:
        validate_record(record, vocabulary)
    assert exc.value.rule == 2


def test_rule2_boolean_is_rejected(vocabulary):
    """bool 是 int 的子类：JSON 里的 true 会被当成票价 1，必须显式拦掉。"""
    record = _record(ticket_price=True)
    with pytest.raises(ValidationIssue) as exc:
        validate_record(record, vocabulary)
    assert exc.value.rule == 2


# ---------- 规则 1：tags ⊆ 词表 ----------

def test_rule1_unknown_tag_is_dropped_with_warning(vocabulary):
    """词表外的 tag 丢弃并告警，但**不丢整条记录**。"""
    record = _record(tags=["公园", "下雨天", "适合雨天"])
    warnings = validate_record(record, vocabulary)
    assert record["tags"] == ["公园"]
    assert len(warnings) == 2
    assert all("规则1" in warning for warning in warnings)


def test_rule1_near_synonym_is_rejected(vocabulary):
    """"室内景点"不是"室内"——语义相近也不行，这是词表存在的全部理由。"""
    assert "室内" in vocabulary
    assert "室内景点" not in vocabulary
    record = _record(tags=["室内景点"])
    validate_record(record, vocabulary)
    assert record["tags"] == []


def test_rule1_tags_are_deduplicated(vocabulary):
    record = _record(tags=["公园", "公园", "室内"])
    validate_record(record, vocabulary)
    assert record["tags"] == ["公园", "室内"]


# ---------- 规则 3：source_url ----------

@pytest.mark.parametrize("bad_url", [None, "", "   ", "example.com", "www.baidu.com", "ftp://x.com"])
def test_rule3_bad_source_url_rejects_whole_record(vocabulary, bad_url):
    record = _record(source_url=bad_url)
    with pytest.raises(ValidationIssue) as exc:
        validate_record(record, vocabulary)
    assert exc.value.rule == 3


@pytest.mark.parametrize("good_url", [
    "https://www.zhejiangmuseum.com/",
    "http://example.com/a?b=1",
])
def test_rule3_valid_source_url_is_accepted(vocabulary, good_url):
    assert validate_record(_record(source_url=good_url), vocabulary) == []


# ---------- 规则 4：amap_poi_id 唯一 ----------

def test_rule4_duplicate_poi_id_is_rejected(vocabulary):
    seen: set[str] = set()
    validate_record(_record(), vocabulary, seen)
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(id="hz-other"), vocabulary, seen)
    assert exc.value.rule == 4


def test_rule4_missing_poi_id_is_rejected(vocabulary):
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(amap_poi_id=""), vocabulary)
    assert exc.value.rule == 4


# ---------- 规则 5：category / 必填字段 ----------

@pytest.mark.parametrize("bad_category", ["自然", "文化", "", None, "NATURAL"])
def test_rule5_invalid_category_is_rejected(vocabulary, bad_category):
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(category=bad_category), vocabulary)
    assert exc.value.rule == 5


@pytest.mark.parametrize("field", ["id", "name", "city", "location"])
def test_required_fields_reject_empty(vocabulary, field):
    with pytest.raises(ValidationIssue):
        validate_record(_record(**{field: "" if field != "location" else {}}), vocabulary)


# ---------- 规则 6：description 长度 ----------

def test_rule6_long_description_is_truncated_with_warning(vocabulary):
    record = _record(description="看" * 300)
    warnings = validate_record(record, vocabulary)
    assert len(record["description"]) == 200
    assert any("规则6" in warning for warning in warnings)


def test_rule6_exactly_200_chars_is_kept(vocabulary):
    record = _record(description="看" * 200)
    assert validate_record(record, vocabulary) == []
    assert len(record["description"]) == 200


# ---------- 规则 7：updated_at ----------

@pytest.mark.parametrize("bad_date", ["2026/10/10", "20261010", "26-10-10", "", None])
def test_rule7_bad_updated_at_is_rejected(vocabulary, bad_date):
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(updated_at=bad_date), vocabulary)
    assert exc.value.rule == 7


# ---------- hours / closed_days / duration_min 的形状约束 ----------

@pytest.mark.parametrize("bad_hours", ["9点-17点", "09:00~17:00 旺季", "全天", "09:00 - 17:00 extra"])
def test_bad_hours_shape_is_rejected(vocabulary, bad_hours):
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(hours=bad_hours), vocabulary)
    assert exc.value.field == "hours"


def test_null_hours_is_allowed(vocabulary):
    assert validate_record(_record(hours=None), vocabulary) == []


def test_closed_days_must_be_list(vocabulary):
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(closed_days="周一"), vocabulary)
    assert exc.value.field == "closed_days"


@pytest.mark.parametrize("bad_day", ["星期一", "礼拜一", "周一至周日", "", "Mon"])
def test_closed_days_values_are_controlled(vocabulary, bad_day):
    """「周一/星期一/礼拜一」三种混用会让下游按「周一」比对时漏掉闭馆。"""
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(closed_days=[bad_day]), vocabulary)
    assert exc.value.field == "closed_days"


def test_empty_closed_days_is_allowed(vocabulary):
    """不确定填 [] 是设计文档的明确要求，不能因此拒绝。"""
    assert validate_record(_record(closed_days=[]), vocabulary) == []


# ---------- location 的取值校验 ----------

@pytest.mark.parametrize("bad_location", [
    {"lng": "abc", "lat": 30.2},
    {"lng": 120.1, "lat": None},
    {"lng": 120.1, "lat": True},
    {"lng": 30.2, "lat": 120.1},   # 经纬度写反
    {"lng": 999, "lat": 30.2},
])
def test_bad_location_values_are_rejected(vocabulary, bad_location):
    """只查键存在会放过 {"lng": "abc"}，下游算距离会直接抛异常。"""
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(location=bad_location), vocabulary)
    assert exc.value.field == "location"


def test_valid_location_passes(vocabulary):
    assert validate_record(_record(location={"lng": 120.1551, "lat": 30.2741}), vocabulary) == []


def test_boolean_duration_is_rejected(vocabulary):
    record = _record(duration_min=True)
    with pytest.raises(ValidationIssue) as exc:
        validate_record(record, vocabulary)
    assert exc.value.field == "duration_min"


# ---------- 第二层推导：opentime2 解析 ----------

@pytest.mark.parametrize("raw,expected_hours,expected_closed", [
    # 第 0 步实测到的三种真实形态
    ("周二至周日 09:00-17:00开放 最晚进入16:30； 周一 全天不开放(遇法定节假日正常开放)",
     "09:00-17:00", ["周一"]),
    # 全角破折号 U+2013：漏掉它会让整条解析失败
    ("全年 周二–周日 09:00–17:00开放 最晚进入16:30；全年 周一 全天不开放", "09:00-17:00", ["周一"]),
    ("周一至周日 07:30-17:30 最晚进入17:00", "07:30-17:30", []),
    # 00:00-24:00 是「全天开放」占位，不是开放时段
    ("周一至周日 00:00-24:00", None, []),
    ("09:00-17:00,节假日10.1-10.7:7:00-18:00", "09:00-17:00", []),
    # 时段与闭馆日写在同一子句、中间无分号——整段跳过会让 hours 变 null
    ("周二至周日 09:00-17:00 周二 全天不开放", "09:00-17:00", ["周二"]),
    ("周一,周三至周日 18:30-22:00；周二 全天关闭", "18:30-22:00", ["周二"]),
    ("", None, []),
    ([], None, []),
    (None, None, []),
    ("10月8日至3月31日 周一至周日 08:00-17:30 门票售票时间:8:00-17:00", "08:00-17:30", []),
    # 旺季/淡季两套时段，取第一个（4.1 约束：hours 不做结构化）
    ("4月1日至10月31日 周二至周日 09:00-17:00；11月1日至次年3月31日 周二至周日 09:30-17:00",
     "09:00-17:00", []),
])
def test_parse_opentime2(raw, expected_hours, expected_closed):
    assert parse_opentime2(raw) == (expected_hours, expected_closed)


def test_parse_opentime2_sorts_closed_days_by_week_order():
    _, closed = parse_opentime2("周日、周三、周一 全天不开放；周二、周四 全天不开放")
    assert closed == ["周一", "周二", "周三", "周四", "周日"]


def test_parse_opentime2_normalizes_sunday():
    _, closed = parse_opentime2("周天 全天不开放")
    assert closed == ["周日"]


def test_parse_opentime2_ignores_open_range_days():
    """「周二至周日」里的周一/周日属于开放区间，不能算成闭馆日。"""
    hours, closed = parse_opentime2("周二至周日 09:00-17:00 周二 全天不开放")
    assert hours == "09:00-17:00"
    assert closed == ["周二"]


# ---------- 第二层推导：category / tags ----------

@pytest.mark.parametrize("type_,expected", [
    ("风景名胜;风景名胜;风景名胜", "自然风景"),
    ("风景名胜;公园广场;公园", "自然风景"),
    ("科教文化服务;博物馆;博物馆", "人文历史"),
    ("风景名胜;风景名胜;寺庙道观", "人文历史"),
    ("科教文化服务;图书馆;图书馆", "人文历史"),
    ("购物服务;特色商业街;特色商业街", "美食购物"),
    ("地名地址信息;热点地名;热点地名", "城市漫游"),
])
def test_map_type_to_category(type_, expected):
    assert map_type_to_category(type_) == expected


def test_derive_tags_does_not_match_middle_characters():
    """"中山路步行街"含「山」字但不是山；标签只看高德 type 的末级细分。"""
    assert "山" not in derive_tags("购物服务;特色商业街;步行街", "南宋御街中山路步行街", "美食购物")
    assert "古镇" in derive_tags("购物服务;特色商业街;步行街", "南宋御街中山路步行街", "美食购物")


def test_derive_tags_marks_indoor():
    tags = derive_tags("科教文化服务;博物馆;博物馆", "浙江省博物馆", "人文历史")
    assert "室内" in tags and "博物馆" in tags


# ---------- 候选挑选 ----------

def test_pick_candidate_prefers_exact_expect_name():
    pois = [
        {"name": "岳王庙-东庑", "id": "B1", "type": "风景名胜;风景名胜;风景名胜", "location": "120.1,30.2"},
        {"name": "杭州西湖风景名胜区-岳王庙", "id": "B2", "type": "风景名胜;风景名胜;寺庙道观", "location": "120.1,30.2"},
    ]
    chosen = pick_candidate(pois, "岳王庙", "杭州西湖风景名胜区-岳王庙")
    assert chosen["name"] == "杭州西湖风景名胜区-岳王庙"


def test_pick_candidate_prefers_shorter_contained_name():
    """"杭州白塔公园"会同时命中本体与「-儿童游乐园」，后者是子项目。"""
    pois = [
        {"name": "杭州白塔公园-儿童游乐园", "id": "B1", "type": "体育休闲服务;休闲场所;游乐场", "location": "120.1,30.2"},
        {"name": "杭州白塔公园", "id": "B2", "type": "风景名胜;公园广场;公园", "location": "120.1,30.2"},
    ]
    assert pick_candidate(pois, "白塔公园", "杭州白塔公园")["name"] == "杭州白塔公园"


def test_pick_candidate_rejects_transit_poi():
    pois = [
        {"name": "龙井村(公交站)", "id": "B1", "type": "交通设施服务;公交车站;公交车站相关", "location": "120.1,30.2"},
        {"name": "龙井村牌坊", "id": "B2", "type": "风景名胜;风景名胜;风景名胜", "location": "120.1,30.2"},
    ]
    assert pick_candidate(pois, "龙井村")["name"] == "龙井村牌坊"


def test_pick_candidate_returns_none_when_all_rejected():
    """实测「楼观台」在高德返回的全是西安的起名馆/生活服务，一条都不该收。"""
    pois = [
        {"name": "杭州楼观台起名馆(总店)", "id": "B1", "type": "生活服务;生活服务场所;生活服务场所", "location": "120.1,30.2"},
    ]
    assert pick_candidate(pois, "楼观台") is None


# ---------- 合成与端到端 ----------

def test_merge_keeps_derived_tags_when_supplement_replaces_them():
    """第三层补场景标签时不能把第二层推的属性标签整份挤掉。"""
    skeleton = {
        "id": "hz-x", "amap_poi_id": "B1", "name": "浙江省博物馆", "tags": ["室内", "博物馆"],
        "ticket_price": None, "rating": 4.7, "type": "科教文化服务;博物馆;博物馆",
    }
    merged = merge_skeleton(skeleton, {"tags": ["亲子", "雨天"], "ticket_price": 0})
    assert merged["tags"] == ["室内", "博物馆", "亲子", "雨天"]
    assert merged["ticket_price"] == 0
    assert "rating" not in merged and "type" not in merged


def test_merge_does_not_let_supplement_touch_derived_fields():
    skeleton = {"id": "hz-x", "amap_poi_id": "B1", "name": "灵隐寺", "location": {"lng": 1.0, "lat": 2.0},
                "category": "人文历史", "tags": []}
    merged = merge_skeleton(skeleton, {"category": "美食购物", "location": {"lng": 9, "lat": 9},
                                       "amap_poi_id": "HACKED"})
    assert merged["category"] == "人文历史"
    assert merged["location"] == {"lng": 1.0, "lat": 2.0}
    assert merged["amap_poi_id"] == "B1"


def test_merge_does_not_let_a_null_supplement_erase_a_derived_value():
    """**补充值是 null 时不覆盖**：这份文件里 null 的意思是"第三层没查到"。

    这正是 hours 空着 36 条的那个 bug：高德把「全天开放」写成 00:00-24:00，
    离线那侧推得出来，第三层没查到、写了 null，然后在合并这一步把推出来的值盖了回去——
    fixture 里明明有数据，重建出来却一个字节都不变。
    """
    skeleton = {"id": "hz-x", "amap_poi_id": "B1", "name": "南宋御街中山路步行街",
                "tags": [], "hours": "全天开放", "closed_days": "周一闭馆"}
    merged = merge_skeleton(skeleton, {"hours": None, "closed_days": None,
                                       "ticket_price": None, "description": None})

    assert merged["hours"] == "全天开放"
    assert merged["closed_days"] == "周一闭馆"


def test_merge_still_lets_a_real_supplement_value_win():
    """反过来那一半也要钉住：补充**查到了**就以它为准（它是第三层的本意）。
    0 与空串不算"没查到"——票价那个 0 是有效值，不能因为 falsy 就被跳过。"""
    skeleton = {"id": "hz-x", "amap_poi_id": "B1", "name": "灵隐寺",
                "tags": [], "hours": "全天开放", "ticket_price": None}
    merged = merge_skeleton(skeleton, {"hours": "07:00-18:00", "ticket_price": 0})

    assert merged["hours"] == "07:00-18:00"
    assert merged["ticket_price"] == 0


def test_merge_defaults_updated_at():
    merged = merge_skeleton({"id": "hz-x", "amap_poi_id": "B1", "name": "n", "tags": []}, None)
    assert merged["updated_at"] == "2026-10-10"


def test_build_is_reproducible():
    """判据第 6 条：同一份 fixture 跑两次，输出一致。"""
    first, _ = build()
    second, _ = build()
    assert first == second


def test_committed_attractions_file_passes_all_rules():
    if not ATTRACTIONS_PATH.exists():
        pytest.skip("attractions.json 尚未生成")
    records = json.loads(ATTRACTIONS_PATH.read_text(encoding="utf-8"))
    accepted, warnings = validate_records(records)
    assert len(accepted) == len(records), f"有记录未通过校验：{warnings}"
    assert len(records) >= 30


def test_committed_attractions_has_no_free_for_everything():
    """反向体检：真正要防的是「所有票价都是 0」——那说明把未知当免费了。

    反过来，如果一条 null 都没有，也要警觉：那可能意味着每条票价都是模型编的
    （提示词第六节第 2 条：不确定就填 null）。所以这里断言的是
    「不能全为 0」，同时把null 比例打印出来供人工判断。
    """
    if not ATTRACTIONS_PATH.exists():
        pytest.skip("attractions.json 尚未生成")
    records = json.loads(ATTRACTIONS_PATH.read_text(encoding="utf-8"))
    prices = [record.get("ticket_price") for record in records]
    assert all(price is None or isinstance(price, int) for price in prices)
    # 真正要防的症状是「**每一条**都是 0」——那说明建表时把未知当成了免费，
    # 预算里门票那一桶会静默少算，且不报错不告警。
    # 有 0 也有非 0 是正常的（杭州确实有一批免费公园与免费博物馆）；
    # 关键是这个 0 是核实过的，不是「查不到就填 0」。
    assert not all(price == 0 for price in prices), "全部票价都是 0，说明把未知当成了免费"


def test_descriptions_are_pure_chinese():
    """description 会直接进向量库并被追问 agent 读出来，不该混进英文单词。

    校验器管的是格式（长度、字段），管不了语言——实际写数据时中英混排很容易发生
    （实测「最 famous 的长城段」「三月最 popular 的去处」都是这么漏进去的），
    所以这里单独守一条。
    """
    if not ATTRACTIONS_PATH.exists():
        pytest.skip("attractions.json 尚未生成")
    offenders = []
    for record in json.loads(ATTRACTIONS_PATH.read_text(encoding="utf-8")):
        words = re.findall(r"[A-Za-z]{3,}", record.get("description") or "")
        if words:
            offenders.append(f"{record['name']}: {words}")
    assert not offenders, "description 里混进了英文：" + "；".join(offenders)


def test_tags_vocabulary_file_is_intact():
    raw = json.loads(TAGS_PATH.read_text(encoding="utf-8"))
    assert set(raw) == {"场景", "属性", "类型"}
    assert "夜游" in raw["场景"] and "室内" in raw["属性"] and "博物馆" in raw["类型"]


def test_fixture_to_skeletons_is_stable():
    from scripts.kb.fetch_amap_fixture import fixture_path_for
    from scripts.kb.normalize import fixture_to_skeletons, load_fixture

    fixture = load_fixture(fixture_path_for("杭州"))
    first = fixture_to_skeletons(fixture, city="杭州")
    second = fixture_to_skeletons(fixture, city="杭州")
    assert first == second
    assert all(record["amap_poi_id"] for record in first)


# ---------- 多城参数化（第二轮：铺北京/上海/广州） ----------

def test_fixture_paths_are_city_scoped():
    """fixture 必须按城市分开存。

    曾经全塞进一个文件：用北京的关键词去搜时，已存在的 34 个杭州 slug 会被当成
    「已存在」整条跳过，循环还打印一串「已存在」——**静默失败**，一条都没抓。
    """
    from scripts.kb.fetch_amap_fixture import fixture_path_for

    paths = [fixture_path_for(city) for city in ("杭州", "北京", "上海", "广州")]
    assert len(set(paths)) == 4, "四座城的 fixture 路径必须互不相同"
    assert paths[0].name == "amap_pois_hangzhou.json"
    assert paths[1].name == "amap_pois_beijing.json"


def test_city_id_prefixes_are_short_and_distinct():
    """主键前缀要短（hz 不是 hangzhou），且四城互不相同。

    前缀退化成一个字面量 "city-" 会让多座城共用一段主键前缀，
    主键就失去了区分城市的能力。
    """
    from scripts.kb.keywords import city_id_prefix

    prefixes = [city_id_prefix(city) for city in ("杭州", "北京", "上海", "广州")]
    assert prefixes == ["hz", "bj", "sh", "gz"]
    assert "city" not in prefixes


def test_city_slug_and_id_prefix_are_independent():
    """文件名的 slug（长）与主键前缀（短）是两个东西，不能共用一个映射。

    曾经共用 CITY_SLUGS，结果杭州前缀从 hz 变成 hangzhou，34 条 id 全变——
    靠「杭州逐字节未变」这条回归判据才发现。
    """
    from scripts.kb.keywords import city_id_prefix, city_slug

    assert city_slug("杭州") == "hangzhou"
    assert city_id_prefix("杭州") == "hz"


def test_unknown_city_gets_distinct_prefixes():
    """没配的城市也要拿到互不相同的前缀，不能都落到 "city"。"""
    from scripts.kb.keywords import city_id_prefix, city_slug

    assert city_slug("成都") != city_slug("西安")
    assert city_id_prefix("成都") != city_id_prefix("西安")
    assert city_id_prefix("成都") != "city"


def test_specs_cover_four_cities():
    from scripts.kb.keywords import SPECS_BY_CITY, keywords_for

    assert set(SPECS_BY_CITY) >= {"杭州", "北京", "上海", "广州"}
    for city in ("杭州", "北京", "上海", "广州"):
        specs = SPECS_BY_CITY[city]
        assert len(specs) >= 30, f"{city} 只有 {len(specs)} 条规格，低于 30 条的基线"
        keywords = keywords_for(city)
        assert len(keywords) == len(set(keywords)), f"{city} 的关键词有重复"
        for spec in specs:
            assert {"keyword", "expect_name", "category"} <= set(spec)


def test_build_uses_city_scoped_specs():
    """fixture_to_skeletons 默认按 city 取规格表，不写死杭州。

    写死的话拿上海的 fixture 去套杭州的 expect_name，会挑出一批错的 POI 且不报错。
    """
    from scripts.kb.keywords import SPECS_BY_CITY, city_id_prefix
    from scripts.kb.normalize import fixture_to_skeletons

    fake_fixture = {
        str(spec["keyword"]): {
            "keyword": spec["keyword"],
            "city": "上海",
            "response": {"pois": [{
                "id": f"B0FAKE{i:04d}", "name": f"上海测试点{i}",
                "type": "风景名胜;风景名胜;风景名胜",
                "location": "121.47,31.23",
            }]},
        }
        for i, spec in enumerate(SPECS_BY_CITY["上海"])
    }
    skeletons = fixture_to_skeletons(fake_fixture, city="上海")
    assert len(skeletons) == len(SPECS_BY_CITY["上海"])
    assert all(record["city"] == "上海" for record in skeletons)
    # 主键前缀走city_id_prefix（短的 sh），不是 city_slug（长的 shanghai）。
    assert all(record["id"].startswith(f"{city_id_prefix('上海')}-") for record in skeletons)


def test_build_infers_city_from_fixture():
    """city 不传时从 fixture 反查——单城调试时不必每次都传。

    关键词必须取广州规格表里真实存在的那一条：fixture_to_skeletons 是**按规格表**
    遍历的，规格表里没有的关键词会被直接跳过。
    """
    from scripts.kb.keywords import SPECS_BY_CITY
    from scripts.kb.normalize import fixture_to_skeletons

    keyword = SPECS_BY_CITY["广州"][0]["keyword"]
    fake_fixture = {
        keyword: {
            "keyword": keyword, "city": "广州",
            "response": {"pois": [{
                "id": "B0FAKE9999", "name": "广州测试点",
                "type": "风景名胜;风景名胜;风景名胜", "location": "113.26,23.13",
            }]},
        }
    }
    skeletons = fixture_to_skeletons(fake_fixture)
    assert len(skeletons) == 1
    assert skeletons[0]["city"] == "广州"


def test_build_skips_keywords_absent_from_specs():
    """规格表里没有的关键词不会被收进来——这是刻意的，不是漏网。

    反过来说：规格表少写一条，就少一个景点，且**不会报错**。
    所以每座城的规格表条数要单独核对。
    """
    from scripts.kb.normalize import fixture_to_skeletons

    fake_fixture = {
        "一个不存在的景点名": {
            "keyword": "一个不存在的景点名", "city": "北京",
            "response": {"pois": [{
                "id": "B0FAKE8888", "name": "某野景点",
                "type": "风景名胜;风景名胜;风景名胜", "location": "116.40,39.90",
            }]},
        }
    }
    assert fixture_to_skeletons(fake_fixture, city="北京") == []


# ---------- 全天开放：离线这条链原来把高德的写法当占位符扔了 ----------

def _poi(**overrides) -> dict:
    poi = {"id": "B0FFH1", "name": "杭州西湖风景名胜区", "location": "120.15,30.25",
           "type": "风景名胜;风景名胜;国家级景点", "biz_ext": {}}
    poi.update(overrides)
    return poi


def test_an_all_day_attraction_keeps_the_wording_instead_of_a_null():
    """高德把全天开放写成 `00:00-24:00` 或 `24小时营业`，而 `parse_opentime2` 要的是
    `HH:MM-HH:MM`，两格都解析不出来 → 以前 hours 存成 null。

    西湖、河坊街、什刹海、南锣鼓巷这类全折在这一格上：实测 138 条里 36 条 hours 是空的，
    其中 16 条名字带「公园 / 广场 / 街 / 湿地」。运行时那条链
    （`amap_attraction._parse_opentime`）早就补了这层，离线这条漏了——
    所以才会「网页上现查答得出来、库里却存着空」。
    """
    record = poi_to_skeleton(_poi(biz_ext={"opentime2": "周一至周日 00:00-24:00"}), "杭州")

    assert record["hours"] == "全天开放"


def test_the_literal_24_hours_wording_counts_too():
    record = poi_to_skeleton(_poi(biz_ext={"open_time": "24小时营业"}), "杭州")

    assert record["hours"] == "全天开放"


def test_a_real_time_still_wins_over_the_all_day_placeholder():
    """同一条里既有占位又有具体时段时，具体时段更准——别被「全天开放」盖掉。"""
    poi = _poi(biz_ext={"opentime2": "周一至周日 00:00-24:00；北门开放时间:07:00-22:00"})

    assert poi_to_skeleton(poi, "杭州")["hours"] == "07:00-22:00"


def test_a_missing_opentime_is_still_a_null_not_a_made_up_all_day():
    """别矫枉过正：两格**都没有**时还是 null，不许凭空写成「全天开放」。"""
    assert poi_to_skeleton(_poi(), "杭州")["hours"] is None


def test_the_schema_accepts_the_wording_the_parser_produces():
    """两处不能各写各的：库里的形状校验必须收得下解析器产出的那个词。

    不收的话，上面补好的那几十条会在建库时被校验器**整个打回**——兜底补了也白补。
    """
    from travel_assistant.clients.amap_opentime import ALL_DAY

    assert HOURS_PATTERN.match(ALL_DAY), f"解析器产出 {ALL_DAY!r}，校验器却不认"


@pytest.mark.parametrize("bad", ["全天", "24小时营业", "24小时"])
def test_narrower_all_day_wordings_are_still_rejected(vocabulary, bad):
    """只放行「全天开放」这一个词，别顺手把形状放开——「全天」「24小时营业」照样拒。"""
    with pytest.raises(ValidationIssue) as exc:
        validate_record(_record(hours=bad), vocabulary)

    assert exc.value.field == "hours"