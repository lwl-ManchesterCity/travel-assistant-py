"""第 3-4 步：把骨架 + 第三层补充数据合成 attractions.json，并做第七节的 7 条校验。

用法：
    python -m scripts.kb.build_kb                # 生成 data/attractions.json
    python -m scripts.kb.build_kb --check-only    # 只校验现有文件，不重新生成

第三层数据放在 data/llm_supplement.json，格式是
    {"<amap_poi_id>": {ticket_price, hours, closed_days, description, tags, source_url, updated_at}}
每条**单独**产出（不批量），护栏见设计文档第六节。
票价缺失必须是 null，不许写 0。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.kb.kb_schema import (  # noqa: E402
    ATTRACTIONS_PATH,
    DATA_DIR,
    ValidationIssue,
    load_tag_vocabulary,
    validate_records,
)
from scripts.kb.keywords import SPECS_BY_CITY, city_slug  # noqa: E402
from scripts.kb.normalize import fixture_to_skeletons, load_fixture  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
SUPPLEMENT_PATH = DATA_DIR / "llm_supplement.json"

# 第三层只允许覆盖这些字段；骨架字段（amap_poi_id/location/category…）不在此列，
# 防止补充文件把离线推导的结果改掉——那会让「同一份 fixture 跑两次输出一致」这条判据失效。
SUPPLEMENTABLE_FIELDS = (
    "ticket_price", "hours", "closed_days", "description", "tags", "source_url", "updated_at",
)
# 骨架自带这几个字段，属于内部推导过程，落库时不该出现在最终文件里。
_INTERNAL_FIELDS = ("rating", "type", "address", "tel", "website", "biz_ext_cost", "city_slug")

DEFAULT_UPDATED_AT = "2026-10-10"


def fixture_path_for(city: str) -> Path:
    return FIXTURE_DIR / f"amap_pois_{city_slug(city)}.json"


def merge_skeleton(skeleton: dict, supplement: dict | None) -> dict:
    merged: list[dict] = dict(skeleton)
    for field in _INTERNAL_FIELDS:
        merged.pop(field, None)
    if supplement:
        for field in SUPPLEMENTABLE_FIELDS:
            if field == "tags":
                continue
            # **补充值是 null 时不覆盖**。这份文件里 null 的定义是"第三层没查到"，
            # 不是"这里就是空"——票价那一行的注释写着"缺失必须是 null，不许写 0"。
            # 一个"不知道"不该盖掉骨架**已经离线推出来的**值。
            #
            # 这一条正是 hours 空了 36 条的原因：高德把"全天开放"写成 00:00-24:00，
            # 离线那侧（normalize.poi_to_skeleton）推得出来，第三层没查到、
            # 写了 null，然后在这里把推出来的值盖了回去——fixture 里明明有数据，
            # 输出却一个字节都不变。closed_days 同一类（骨架也会推）。
            if supplement.get(field) is not None:
                merged[field] = supplement[field]
        # tags 是合并而不是覆盖：第二层推的属性标签（室内/博物馆）不该被第三层整份替换掉。
        # 必须在上面的循环里跳过 tags，否则先被覆盖成补充值，再合并时就没有基线了。
        if "tags" in supplement:
            combined = list(merged.get("tags") or [])
            for tag in supplement["tags"] or []:
                if tag not in combined:
                    combined.append(tag)
            merged["tags"] = combined
    merged.setdefault("updated_at", DEFAULT_UPDATED_AT)
    return merged


def build_city(city: str, supplements: dict) -> tuple[list[dict], list[str]]:
    """单城：fixture → 骨架 → 合并第三层。返回 (记录, 缺补充数据的景点名)。"""
    path = fixture_path_for(city)
    if not path.exists():
        return [], [f"[{city}] 还没有 fixture：{path}（先跑 fetch_amap_fixture --city {city}）"]

    skeletons = fixture_to_skeletons(load_fixture(path), city=city)
    records: list[dict] = []
    missing: list[str] = []
    for skeleton in skeletons:
        supplement = supplements.get(skeleton["amap_poi_id"])
        if not supplement:
            missing.append(f"{skeleton['name']}({skeleton['amap_poi_id']})")
        records.append(merge_skeleton(skeleton, supplement))
    return records, missing


def build(
    supplement_path: Path = SUPPLEMENT_PATH,
    cities: list[str] | None = None,
) -> tuple[list[dict], list[str]]:
    """多城合并。cities 给了就只build 这几座并按给定顺序输出；没给就按规格表的顺序全建。

    **跨城去重**：四座城可能命中同一个 amap_poi_id（比如连锁品牌的馆），
    重复的会被丢掉——落库时 id 是按城市 slug 生成的，所以同poi_id 在不同城是两条不同记录，
    这里按 (city, amap_poi_id) 去重，不按 amap_poi_id 单字段去。
    """
    target_cities = cities if cities else list(SPECS_BY_CITY)
    supplements = (
        json.loads(supplement_path.read_text(encoding="utf-8")) if supplement_path.exists() else {}
    )

    merged: list[dict] = []
    missing_supplement: list[str] = []
    seen: set[tuple[str, str]] = set()
    for city in target_cities:
        records, missing = build_city(city, supplements)
        for record in records:
            key = (record["city"], record["amap_poi_id"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(record)
        if missing:
            missing_supplement.extend(f"{city}/{name}" for name in missing)

    vocabulary = load_tag_vocabulary()
    accepted, warnings = validate_records(merged, vocabulary)
    if missing_supplement:
        warnings.append(
            f"[第三层] {len(missing_supplement)} 条缺补充数据，ticket_price 保持 null（未知）："
            + "、".join(missing_supplement)
        )
    return accepted, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="合成并校验 data/attractions.json")
    parser.add_argument("--check-only", action="store_true", help="只校验现有文件")
    parser.add_argument("--strict", action="store_true", help="有任何告警就返回非零")
    parser.add_argument(
        "--cities",
        default="",
        help="只 build 这几座城（逗号分隔，按给定顺序）。不传= 按规格表顺序全建",
    )
    args = parser.parse_args()

    if args.check_only:
        records = json.loads(ATTRACTIONS_PATH.read_text(encoding="utf-8"))
        vocabulary = load_tag_vocabulary()
        accepted, warnings = validate_records(records, vocabulary)
        failed = len(records) - len(accepted)
    else:
        cities = [city.strip() for city in args.cities.split(",") if city.strip()] or None
        accepted, warnings = build(cities=cities)
        failed = 0
        ATTRACTIONS_PATH.write_text(
            json.dumps(accepted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    counts: dict[str, int] = {}
    for record in accepted:
        counts[record.get("city") or "?"] = counts.get(record.get("city") or "?", 0) + 1

    print(f"合格记录 {len(accepted)} 条 | 校验失败 {failed} 条 | 告警 {len(warnings)} 条")
    print("  分城：" + " | ".join(f"{city} {count}" for city, count in counts.items()))
    for warning in warnings:
        print(f"  - {warning}")
    if not args.check_only:
        print(f"\n已写入 {ATTRACTIONS_PATH}")
    return 1 if (failed or (args.strict and warnings)) else 0


if __name__ == "__main__":
    raise SystemExit(main())