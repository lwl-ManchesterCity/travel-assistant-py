"""检索侧判据（docs/ATTRACTION_RETRIEVAL_DESIGN.md 第八节）。

需要本地 BGE-M3 权重；路径不存在则整组跳过，不会让套件变红。
"""
from __future__ import annotations

import json

import pytest

from scripts.kb.build_index import build_index, build_text, flat_metadata
from scripts.kb.kb_schema import ATTRACTIONS_PATH, TAGS_PATH
from travel_assistant.kb import (
    DEFAULT_MIN_SCORE,
    get_encoder,
    indexed_cities,
    model_path,
    search_attractions,
)

# 不需要模型的那几条（懒加载、索引缺失、词表、KB_CITIES）在 tests/test_kb_runtime.py，
# 因为本文件的 skipif 是模块级的，会把它们一起跳掉。

pytestmark = pytest.mark.skipif(
    not model_path().exists(), reason=f"本地权重不存在：{model_path()}"
)


@pytest.fixture(scope="module")
def records() -> list[dict]:
    return json.loads(ATTRACTIONS_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def vocabulary() -> set[str]:
    raw = json.loads(TAGS_PATH.read_text(encoding="utf-8"))
    return {word for words in raw.values() for word in words}


@pytest.fixture(scope="module")
def indexed(tmp_path_factory, records):
    """建一次索引，整组复用。"""
    index_dir = tmp_path_factory.mktemp("chroma")
    encoder = get_encoder()
    build_index(records=records, index_dir=index_dir, encoder=encoder)
    return index_dir, encoder


# ---------- 判据 1：词表里每个词至少被 1 条记录使用 ----------

def test_criterion1_no_empty_vocabulary_word(records, vocabulary):
    used = {tag for record in records for tag in record["tags"]}
    assert vocabulary - used == set(), "词表里有没人用的空词"


# ---------- 判据 2：tags 全部在词表内 ----------

def test_criterion2_tags_within_vocabulary(records, vocabulary):
    for record in records:
        assert set(record["tags"]) <= vocabulary, record["name"]


# ---------- 判据 3：metadata 无嵌套、无 null ----------

def test_criterion3_metadata_is_flat_scalars(records):
    def is_scalar(value):
        if isinstance(value, list):
            return all(is_scalar(item) for item in value)
        return isinstance(value, (str, int, float, bool))

    for record in records:
        metadata = flat_metadata(record)
        assert "location" not in metadata, "location 没拍平"
        assert "location_lng" in metadata and "location_lat" in metadata
        for key, value in metadata.items():
            assert value is not None, f"{record['name']} 的 {key} 是 null"
            assert is_scalar(value), f"{record['name']} 的 {key} 不是标量"


# ---------- 判据 4、5：先筛后检索能修好 2.1 那个失败 ----------

def test_criterion4_and_5_rainy_day_returns_indoor(indexed):
    index_dir, encoder = indexed
    rows = search_attractions(
        city="杭州", tags=["室内"], query="杭州下雨天去哪",
        index_dir=index_dir, encoder=encoder,
    )
    assert rows, "筛「室内」之后不该为空"
    for row in rows:
        assert "室内" in row["tags"], f"{row['name']} 不带室内标签"
    names = [row["name"] for row in rows]
    assert any("浙江省博物馆" in name for name in names), f"前 3 名是 {names}"


def test_pure_vector_fails_without_filter(indexed):
    """不筛的对照组：溶洞/露天会顶掉博物馆——这就是必须有筛这一层的理由。"""
    index_dir, encoder = indexed
    rows = search_attractions(
        city="杭州", query="杭州下雨天去哪", index_dir=index_dir, encoder=encoder
    )
    assert rows
    assert not all("室内" in row["tags"] for row in rows), (
        "纯向量竟然全给室内的，那 2.1 节的失败案例不成立了，本单前提需重核"
    )


# ---------- 判据 6：无条件查询不报错、不为空 ----------

def test_criterion6_open_question_works(indexed):
    index_dir, encoder = indexed
    rows = search_attractions(
        city="杭州", query="杭州有什么好玩的", index_dir=index_dir, encoder=encoder
    )
    assert len(rows) > 0


# ---------- 判据 2（阈值）：无关键词不进结果 ----------

def test_min_score_drops_irrelevant(indexed):
    index_dir, encoder = indexed
    rows = search_attractions(
        city="杭州", query="杭州滑雪场单板双板", min_score=0.99,
        index_dir=index_dir, encoder=encoder,
    )
    assert rows == [], "阈值拉到 0.99 就该一条都不剩"


# ---------- 判据 8：索引可重复构建，结果一致 ----------

def test_criterion8_rebuild_is_stable(tmp_path, records, indexed):
    index_dir, encoder = indexed
    first = search_attractions(
        city="杭州", tags=["室内"], query="杭州下雨天去哪",
        index_dir=index_dir, encoder=encoder,
    )
    second_dir = tmp_path / "chroma"
    build_index(records=records, index_dir=second_dir, encoder=encoder)
    second = search_attractions(
        city="杭州", tags=["室内"], query="杭州下雨天去哪",
        index_dir=second_dir, encoder=encoder,
    )
    assert [row["id"] for row in first] == [row["id"] for row in second]


# ---------- 结构：page_content 与字段还原 ----------

def test_build_text_uses_four_fields():
    record = {
        "name": "某馆", "aliases": [], "tags": ["室内"], "description": "说明",
        "ticket_price": 30,
    }
    text = build_text(record)
    assert text == "某馆||室内|说明", "空 aliases 应留空串（双竖线是对的）"
    assert "30" not in text, "数字字段不许进向量"


def test_row_restores_location_and_hours(indexed):
    index_dir, encoder = indexed
    rows = search_attractions(
        city="杭州", query="杭州有什么好玩的", top_k=5,
        index_dir=index_dir, encoder=encoder,
    )
    for row in rows:
        assert set(row["location"]) == {"lng", "lat"}
        assert row["hours"] is None or isinstance(row["hours"], str)
        assert row["ticket_price"] is not None


# ---------- 阈值不许管筛过的那条路 ----------

def test_tag_filter_is_not_vetoed_by_the_threshold(indexed):
    """筛出来的东西不许被相似度丢掉。

    实测踩到的：筛「需预约」命中灵隐寺（全库唯一一条带这个 tag 的），
    它对这个问句的相似度低于阈值，于是用户拿到"没有命中的景点"——而库里明明有。
    """
    index_dir, encoder = indexed
    rows = search_attractions(
        city="杭州", tags=["需预约"], query="杭州有什么要预约的",
        index_dir=index_dir, encoder=encoder,
    )

    assert [row["name"] for row in rows] == ["灵隐寺"]
    assert rows[0]["score"] < DEFAULT_MIN_SCORE, (
        "这条本来就落在阈值下面——它要是没低于阈值，这条判据就白写了"
    )


def test_tag_filter_ignores_even_an_absurd_threshold(indexed):
    """把规则本身钉死：带了筛条件，阈值就没有能力把结果清空。"""
    index_dir, encoder = indexed
    rows = search_attractions(
        city="杭州", tags=["需预约"], query="杭州有什么要预约的",
        min_score=0.99,
        index_dir=index_dir, encoder=encoder,
    )

    assert rows, "带了筛条件时，阈值不该有能力把结果清空"


# ---------- 判据 9：城市从索引里读，不从常量读 ----------

def test_criterion9_indexed_cities_comes_from_the_index(indexed, records):
    """工具说明里那句"只收录杭州"是写死的常量，**判断**必须走索引里实际有什么。

    将来铺了别的城市，判断跟着变；常量与数据对不对得上由 test_kb_runtime.py 守着。
    """
    index_dir, _ = indexed
    assert indexed_cities(index_dir=index_dir) == sorted({r["city"] for r in records})
