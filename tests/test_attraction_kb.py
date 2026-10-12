"""第七节 7 条校验规则的正反例（对应 docs/ATTRACTION_KB_DESIGN.md 第七节）。

规则 2（ticket_price 缺失必须是 null 而不是 0）是本单最重要的一条，
所以它的用例写得最密：0 表示确定免费、null 表示未知，两者混用会让预算静默算错。
"""

import json

import pytest

from scripts.kb.build_kb import build, merge_skeleton
from scripts.kb.kb_schema import (
    ATTRACTIONS_PATH,
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


def test_tags_vocabulary_file_is_intact():
    raw = json.loads(TAGS_PATH.read_text(encoding="utf-8"))
    assert set(raw) == {"场景", "属性", "类型"}
    assert "夜游" in raw["场景"] and "室内" in raw["属性"] and "博物馆" in raw["类型"]


def test_fixture_to_skeletons_is_stable():
    from scripts.kb.normalize import load_fixture
    from scripts.kb.fetch_amap_fixture import FIXTURE_PATH

    fixture = load_fixture(FIXTURE_PATH)
    first = fixture_to_skeletons(fixture)
    second = fixture_to_skeletons(fixture)
    assert first == second
    assert all(record["amap_poi_id"] for record in first)