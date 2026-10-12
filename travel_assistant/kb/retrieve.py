"""检索：先筛后检索 / 纯向量。对外一个函数 search_attractions。

对应 docs/ATTRACTION_RETRIEVAL_DESIGN.md 第六、十二节。
口径只有一句：**能用确定条件筛的，绝不交给向量。**
"""

from __future__ import annotations

from .encoder import encode_texts, get_encoder
from .store import open_collection

# 阈值：本机实测（2026-10-11，12 条问句、60 个分数）
#
#   域外问句（修手机/考驾照/海底捞）  top1 最高 0.537
#   域内问句（下雨天/免费景点/爬山）  top1 最低 0.554
#
# 取中间那条空带的中点。**但它只挡得住"问错领域"。**
#
# 挡不住"问对领域、库里没有"——实测擦边问句与相关问句的分数完全重叠：
#     0.6546  杭州有什么湖        ← 库里没有
#     0.6484  杭州下雨天去哪      ← 必须答对
#     0.6450  杭州哪里能爬山      ← 库里没有（「山」是上一轮删掉的空词）
#     0.6133  杭州有哪些博物馆    ← 必须答对
# 任何能筛掉「爬山」的阈值会把「下雨天」「博物馆」一起筛掉。
# 这条限制的解法是补数据 + 提示词护栏，不是调这个数。
DEFAULT_MIN_SCORE = 0.545
DEFAULT_TOP_K = 3

_PASSTHROUGH = (
    "id", "amap_poi_id", "name", "aliases", "city", "tags", "category",
    "duration_min", "description", "ticket_price", "hours", "closed_days",
    "source_url", "updated_at",
)


def _where(city: str | None, tags: list[str] | None) -> dict | None:
    """筛条件。tags 用 $contains —— 实测 $in 命中空（第四节 4.3）。"""
    conditions = []
    if city:
        conditions.append({"city": city})
    for tag in tags or []:
        conditions.append({"tags": {"$contains": tag}})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


def _row(metadata: dict, document: str, score: float) -> dict:
    """还原成一条完整记录：location 从拍平的两个字段拼回去。"""
    record = {key: metadata[key] for key in _PASSTHROUGH if key in metadata}
    if "location_lng" in metadata:
        record["location"] = {
            "lng": metadata["location_lng"],
            "lat": metadata["location_lat"],
        }
    record["hours"] = metadata.get("hours") or None  # 存进去时空串，取出来还原成 None
    record["page_content"] = document
    record["score"] = score
    return record


def search_attractions(
    city: str | None = None,
    tags: list[str] | None = None,
    query: str | None = None,
    top_k: int = DEFAULT_TOP_K,
    min_score: float = DEFAULT_MIN_SCORE,
    index_dir=None,
    encoder=None,
) -> list[dict]:
    """检索景点。

    - ``tags=["室内"]`` -> 先筛后检索（硬筛 + 软排）
    - ``tags=None``     -> 纯向量（只按 city 收窄）
    - ``min_score`` **只在纯向量那条路上生效**，为什么见下面那段注释

    ``query`` 为空时用 tags 拼一个查询词，纯筛选场景够用。

    索引没建时抛 ``IndexNotBuilt``（见 store.py），调用方要接住并照实说。
    """
    collection = open_collection(index_dir)

    text = query or "|".join(tags or []) or (city or "")
    if encoder is None:
        encoder = get_encoder()
    vector = encode_texts(encoder, [text])

    result = collection.query(
        query_embeddings=vector,
        n_results=max(top_k, 1),
        where=_where(city, tags),
        include=["documents", "metadatas", "distances"],
    )

    # 阈值只管纯向量那条路。
    #
    # 带了 tag 就是**筛**在答，向量只负责把这批筛出来的排个序。这时候拿相似度把命中丢掉，
    # 等于把"先筛后检索"的意义抹掉。实测踩到的：
    #     筛「需预约」-> 命中灵隐寺（全库唯一一条带这个 tag 的）
    #     但它对「杭州有什么要预约的」的相似度只有 0.5399，被 0.545 丢掉
    #     用户拿到"没有命中的景点"——而库里明明有，还是唯一一条
    # 网上的顶多是排序不理想，不该让筛选条件本身失效。
    apply_threshold = not tags

    rows = []
    for document, metadata, distance in zip(
        result["documents"][0], result["metadatas"][0], result["distances"][0]
    ):
        score = 1.0 - distance  # cosine distance -> similarity
        if apply_threshold and score < min_score:
            continue
        rows.append(_row(metadata, document, round(score, 4)))
    return rows[:top_k]
