"""景点资料库的**运行时**半边：查索引、读词表。

离线半边（读 `attractions.json`、建索引、词表校验）在 `scripts/kb/` 下。

分界一句话：
- 运行时读 `data/chroma/`（查）与 `data/tags.json`（词表），**从不读 `data/attractions.json`**；
- 离线脚本读 `data/attractions.json`，建出 `data/chroma/`。

共用常量（集合名、索引路径、编码器）归本包所有，离线脚本反过来 import 本包，
这样"建的时候写进哪个集合"与"查的时候读哪个集合"不可能对不上。

`scripts/kb/kb_schema.py` 原写着"不 import travel_assistant 的任何东西"——
那句话是在这张表还纯粹是离线产物时写的。`tags.json` 从本单起是运行时输入，
路径的归属跟着搬到这里；`attractions.json` 的归属没变，仍是离线。
"""

from .encoder import encode_texts, get_encoder, model_path
from .retrieve import DEFAULT_MIN_SCORE, DEFAULT_TOP_K, search_attractions
from .store import (
    COLLECTION_NAME,
    KB_CITIES,
    IndexNotBuilt,
    indexed_cities,
    open_collection,
)
from .vocabulary import describe_vocabulary, load_tag_vocabulary, vocabulary_words

__all__ = [
    "COLLECTION_NAME",
    "KB_CITIES",
    "DEFAULT_MIN_SCORE",
    "DEFAULT_TOP_K",
    "IndexNotBuilt",
    "describe_vocabulary",
    "encode_texts",
    "get_encoder",
    "indexed_cities",
    "load_tag_vocabulary",
    "model_path",
    "open_collection",
    "search_attractions",
    "vocabulary_words",
]
