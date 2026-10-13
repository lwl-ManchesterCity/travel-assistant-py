"""第 1 步：拉景点骨架，把高德原始响应存成fixture（对应设计文档第五节第 1 步）。

只花一次配额，可重复转换：转换逻辑改了不用重拉，有审计痕迹。
存的是**未归一化的原始响应**，保留 biz_ext —— scripts/fixtures/result-plan.json 存的是
归一化之后的对象，没有 biz_ext，不能拿来当输入。

用法：
    python -m scripts.kb.fetch_amap_fixture                 # 增量补齐缺失的关键词
    python -m scripts.kb.fetch_amap_fixture --force          # 全量重拉
    python -m scripts.kb.fetch_amap_fixture --only灵隐寺      # 只拉一个（注意无空格）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.kb.keywords import city_slug, known_cities, keywords_for  # noqa: E402
from travel_assistant.clients.amap_http import build_candidate_urls, fetch_json  # noqa: E402
from travel_assistant.config.settings import AmapSettings  # noqa: E402

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


def fixture_path_for(city: str) -> Path:
    """按城市派生 fixture 路径。

    **必须按城市分开存**，不能全塞进一个文件：用杭州的关键词去搜北京时，
    已存在的 slug 会被当成「已存在」整条跳过，循环还会打印一串「已存在」，
    输出看着一切正常，实际一条都没抓——这是静默失败，不是报错。
    """
    return FIXTURE_DIR / f"amap_pois_{city_slug(city)}.json"

# 高德个人 key 的 QPS 很低（实测 10021 CUQPS），这里主动限速而不是靠重试兜。
REQUEST_INTERVAL_SECONDS = 1.2


def _slug(keyword: str) -> str:
    return keyword.replace(" ", "_")


def fetch_one(amap: AmapSettings, keyword: str, city: str, page_size: int) -> dict:
    urls = build_candidate_urls(
        amap,
        "/v3/place/text",
        {
            "key": amap.api_key,
            "keywords": keyword,
            "city": city,
            "citylimit": amap.city_limit,
            "offset": page_size,
            "page": 1,
            "extensions": "all",
        },
    )
    return fetch_json(
        amap,
        urls,
        log_scope=f"骨架抓取 {keyword}",
        error_prefix="景点骨架抓取",
        request_exception_prefix="景点骨架抓取请求异常：",
        curl_exception_prefix="curl fallback 未获取到有效 POI 响应：",
        catch_curl_errors=True,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="拉取高德景点骨架并存成 fixture")
    parser.add_argument("--city", default="杭州", help="城市名，不带「市」字；决定关键词表与 fixture 文件名")
    parser.add_argument("--page-size", type=int, default=10)
    parser.add_argument("--force", action="store_true", help="忽略已有 fixture，全量重拉")
    parser.add_argument("--only", default="", help="只拉指定关键词（逗号分隔）")
    args = parser.parse_args()

    amap = AmapSettings()
    if not amap.api_key or not amap.api_key.strip():
        print("AMAP_API_KEY 未配置，无法抓取", file=sys.stderr)
        return 1

    if args.only:
        keywords = [kw.strip() for kw in args.only.split(",") if kw.strip()]
    else:
        keywords = list(keywords_for(args.city))
        if not keywords:
            print(
                f"keywords.SPECS_BY_CITY 里没有「{args.city}」，先写规格表再抓。\n"
                f"已配置的城：{'、'.join(known_cities())}",
                file=sys.stderr,
            )
            return 1

    fixture_path = fixture_path_for(args.city)
    stored: dict[str, dict] = {}
    if fixture_path.exists() and not args.force:
        stored = json.loads(fixture_path.read_text(encoding="utf-8"))
        print(f"已有 fixture {len(stored)} 条，--force 可全量重拉")
    elif args.force:
        print("--force：忽略已有 fixture，全量重拉")

    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    for index, keyword in enumerate(keywords, start=1):
        slug = _slug(keyword)
        if slug in stored:
            print(f"[{index}/{len(keywords)}] {keyword} -> 已存在，跳过")
            continue
        try:
            response = fetch_one(amap, keyword, args.city, args.page_size)
        except Exception as exception:  # noqa: BLE001 - 离线作业，单条失败不应中断整批
            failures.append(f"{keyword}: {type(exception).__name__}: {exception}")
            print(f"[{index}/{len(keywords)}] {keyword} -> 失败: {exception}")
            continue

        pois = response.get("pois") or []
        stored[slug] = {
            "keyword": keyword,
            "city": args.city,
            "response": response,
        }
        print(f"[{index}/{len(keywords)}] {keyword} -> {len(pois)} 条 POI (count={response.get('count')})")
        if index < len(keywords):
            time.sleep(REQUEST_INTERVAL_SECONDS)

    fixture_path.write_text(
        json.dumps(stored, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"\n已写入 {fixture_path}（{len(stored)} 个关键词）")
    if failures:
        print(f"失败 {len(failures)} 个：")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())