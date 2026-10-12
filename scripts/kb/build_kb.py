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
from scripts.kb.normalize import fixture_to_skeletons, load_fixture  # noqa: E402

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "amap_pois_hangzhou.json"
SUPPLEMENT_PATH = DATA_DIR / "llm_supplement.json"

# 第三层只允许覆盖这些字段；骨架字段（amap_poi_id/location/category…）不在此列，
# 防止补充文件把离线推导的结果改掉——那会让「同一份 fixture 跑两次输出一致」这条判据失效。
SUPPLEMENTABLE_FIELDS = (
    "ticket_price", "hours", "closed_days", "description", "tags", "source_url", "updated_at",
)
# 骨架自带这几个字段，属于内部推导过程，落库时不该出现在最终文件里。
_INTERNAL_FIELDS = ("rating", "type", "address", "tel", "website", "biz_ext_cost", "city_slug")


def merge_skeleton(skeleton: dict, supplement: dict | None) -> dict:
    merged: list[dict] = dict(skeleton)
    for field in _INTERNAL_FIELDS:
        merged.pop(field, None)
    if supplement:
        for field in SUPPLEMENTABLE_FIELDS:
            if field == "tags":
                continue
            if field in supplement:
                merged[field] = supplement[field]
        # tags 是合并而不是覆盖：第二层推的属性标签（室内/博物馆）不该被第三层整份替换掉。
        # 必须在上面的循环里跳过 tags，否则先被覆盖成补充值，再合并时就没有基线了。
        if "tags" in supplement:
            combined = list(merged.get("tags") or [])
            for tag in supplement["tags"] or []:
                if tag not in combined:
                    combined.append(tag)
            merged["tags"] = combined
    merged.setdefault("updated_at", "2026-10-10")
    return merged


def build(supplement_path: Path = SUPPLEMENT_PATH, fixture_path: Path = FIXTURE_PATH) -> tuple[list[dict], list[str]]:
    fixture = load_fixture(fixture_path)
    skeletons = fixture_to_skeletons(fixture)
    supplements = (
        json.loads(supplement_path.read_text(encoding="utf-8")) if supplement_path.exists() else {}
    )

    merged: list[dict] = []
    missing_supplement: list[str] = []
    for skeleton in skeletons:
        poi_id = skeleton["amap_poi_id"]
        supplement = supplements.get(poi_id)
        if not supplement:
            missing_supplement.append(f"{skeleton['name']}({poi_id})")
        merged.append(merge_skeleton(skeleton, supplement))

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
    args = parser.parse_args()

    if args.check_only:
        records = json.loads(ATTRACTIONS_PATH.read_text(encoding="utf-8"))
        vocabulary = load_tag_vocabulary()
        accepted, warnings = validate_records(records, vocabulary)
        failed = len(records) - len(accepted)
    else:
        accepted, warnings = build()
        failed = 0
        ATTRACTIONS_PATH.write_text(
            json.dumps(accepted, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    print(f"合格记录 {len(accepted)} 条 | 校验失败 {failed} 条 | 告警 {len(warnings)} 条")
    for warning in warnings:
        print(f"  - {warning}")
    if not args.check_only:
        print(f"\n已写入 {ATTRACTIONS_PATH}")
    return 1 if (failed or (args.strict and warnings)) else 0


if __name__ == "__main__":
    raise SystemExit(main())