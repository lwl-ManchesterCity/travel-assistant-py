"""本地 BGE-M3 编码器：懒加载 + 进程内缓存。

**为什么不放在服务启动时加载**（实测，见 docs/ATTRACTION_RETRIEVAL_DESIGN.md 第三节）：

    服务装配完        0.100 GB      0.6s
    + chroma          0.135 GB     +0.3s
    + BGE-M3（含torch） 0.871 GB     +5.9s

追问里有相当一部分根本不碰检索（问方案里写了什么、判要不要改方案），
为这些请求在启动时就背上 0.87G 和 5.9s 不划算，健康检查窗口也会跟着变长。
所以第一次真的编码时才加载，此后进程内复用。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

# 必须用这份完整权重，**不能用 HuggingFace 缓存那份**——那份有 7 个断链，
# 指向一个不存在的 Xorbits--bge-m3/ 路径（第三节坑 1）。
_DEFAULT_MODEL_PATH = Path.home() / "Documents" / "bge-m3" / "snapshots" / "master"

_encoder = None
_encoder_lock = threading.Lock()


def model_path() -> Path:
    return Path(os.environ.get("TRAVEL_EMBED_MODEL_PATH", str(_DEFAULT_MODEL_PATH)))


def get_encoder():
    """第一次调用加载（约 5.9s / 0.87G），之后直接返回同一个实例。

    双重检查加锁：FastAPI 是多线程跑同步工具的，两个请求同时第一次进来会各加载一份模型，
    那等于白烧一倍内存。
    """
    global _encoder
    if _encoder is None:
        with _encoder_lock:
            if _encoder is None:
                from sentence_transformers import SentenceTransformer

                _encoder = SentenceTransformer(str(model_path()))
    return _encoder


def encode_texts(encoder, texts: list[str]) -> list[list[float]]:
    return encoder.encode(list(texts), normalize_embeddings=True).tolist()
