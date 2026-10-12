"""建索引：读 data/attractions.json -> 写进 Chroma。

对应 docs/ATTRACTION_RETRIEVAL_DESIGN.md 第四、十一节。
索引是**产物**，不入库（第十节第 3 条）：attractions.json 一改就要重建。

本文件是**离线**半边。集合名、索引路径、编码器一律从 `travel_assistant.kb` 取，
不在这里另写一份——否则"建的时候写进哪个集合"与"查的时候读哪个集合"会各说各话。
"""

from __future__ import annotations

from pathlib import Path

from travel_assistant.kb.encoder import encode_texts, get_encoder
from travel_assistant.kb.paths import INDEX_DIR
from travel_assistant.kb.store import COLLECTION_NAME

from .kb_schema import load_attractions

# 进向量库的文本 = 这四个字段拼起来（第四节 4.1）
VECTOR_FIELDS = ("name", "aliases", "tags", "description")


def build_text(record: dict) -> str:
    """name + aliases + tags + description，用 | 分隔。

    aliases 为空数组时留空串，所以会出现 `浙江省博物馆(孤山馆区)||室内|...` 这样的
    双竖线——这是对的，不是 bug。
    """
    parts = []
    for field in VECTOR_FIELDS:
        value = record.get(field)
        if value is None:
            value = ""
        elif isinstance(value, list):
            value = "|".join(str(item) for item in value)
        parts.append(str(value))
    return "|".join(parts)


def flat_metadata(record: dict) -> dict:
    """Chroma 的 metadata 只收标量：嵌套对象拍平，null 换空串。

    tags / closed_days 是数组，**原样存**——tags 要靠 $contains 筛（第四节 4.3）。
    """
    out: dict = {}
    for key, value in record.items():
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                out[f"{key}_{sub_key}"] = sub_value
        elif value is None:
            out[key] = ""
        elif isinstance(value, list):
            # Chroma 拒收**空数组**（实测：Expected metadata list value ... non-empty）。
            # 非空原样存数组——tags 要靠 $contains 筛；空的换成空串。
            out[key] = value if value else ""
        else:
            out[key] = value
    return out


def build_index(
    records: list[dict] | None = None,
    index_dir: Path | None = None,
    encoder=None,
):
    """把记录写进 Chroma，返回 collection。已存在则先删后建（保证可重复运行）。"""
    import chromadb

    if records is None:
        records = load_attractions()
    target = Path(index_dir) if index_dir else INDEX_DIR
    target.mkdir(parents=True, exist_ok=True)

    if encoder is None:
        encoder = get_encoder()

    client = chromadb.PersistentClient(path=str(target))
    try:
        client.delete_collection(COLLECTION_NAME)
    except Exception:  # noqa: BLE001 - 首次运行没有这个集合
        pass

    collection = client.create_collection(
        COLLECTION_NAME, metadata={"hnsw:space": "cosine"}
    )
    texts = [build_text(record) for record in records]
    collection.add(
        ids=[record["id"] for record in records],
        documents=texts,
        embeddings=encode_texts(encoder, texts),
        metadatas=[flat_metadata(record) for record in records],
    )
    return collection


if __name__ == "__main__":
    collection = build_index()
    print(f"已写入 {collection.count()} 条 -> {INDEX_DIR}")
