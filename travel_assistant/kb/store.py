"""Chroma 索引的打开方式。

chromadb 与 sentence_transformers 一律**函数内 import**：`travel_assistant.kb` 会被
AskAgent 的 import 链带上，不能因为引了这个包就让服务启动多背 0.3s + 35M。
"""

from __future__ import annotations

from pathlib import Path

from .paths import INDEX_DIR

COLLECTION_NAME = "attractions"

# 本地库收录的城市，只用来写进工具说明，让模型别去调明显答不了的城市。
# **这是给模型看的提示，不是判断依据**——真正的判断走 indexed_cities()，以索引里实际有什么为准。
# 写死是刻意的：建 agent 时读一遍索引会把 chromadb 拖进服务启动（+0.3s），
# 而这条说明只需要"大概范围对"。两者会不会对不上，由 tests/test_kb_runtime.py 里那条判据守着。
#
# **补城市时必须同步改这里**：它会写进工具说明，漏改等于告诉模型「本地库只有杭州」，
# 模型就不再检索另外三城。判断本身不受影响（走 indexed_cities），但**模型会不会去查**受它影响。
#
# 2026-10-12 实录：补完三城后，广州的景点问题仍然全答「方案里没有这个景点」。
# 原因不在本常量（它改对了），而是 **ask_agent.py 的系统提示词里另有一句写死的
# 「本地库只收录杭州」**，级别比工具说明高，模型信了那句就不去调工具。
# 所以补城市要改的是**两处**：这里，和提示词里所有提到城市范围的地方。
# 已经把那句改成"以工具说明那一行为准"，提示词里**不许再出现城市名**。
KB_CITIES = ("杭州", "北京", "上海", "广州")


class IndexNotBuilt(Exception):
    """索引还没建：`data/chroma/` 不存在，或里面没有这个 collection。

    单独一个异常类型是为了让工具层能**照实告诉用户**，而不是把栈打给模型。
    `data/chroma/` 是 gitignore 的（索引是产物，不入库），所以部署时必须先建。
    """


def open_collection(index_dir=None):
    import chromadb

    target = Path(index_dir) if index_dir else INDEX_DIR
    if not target.exists():
        raise IndexNotBuilt(f"索引还没建：{target}")
    client = chromadb.PersistentClient(path=str(target))
    try:
        return client.get_collection(COLLECTION_NAME)
    except Exception as exception:  # noqa: BLE001 - 没建过就是没建过，统一成一个类型
        raise IndexNotBuilt(f"索引里没有 {COLLECTION_NAME} 集合：{target}") from exception


def indexed_cities(index_dir=None) -> list[str]:
    """索引里**实际**有哪些城市。

    不写成 `KB_CITY = "杭州"` 这样的常量：本地库将来铺了别的城市，
    这里跟着变，"本地库只有杭州"就不会变成一句等着过期的谎。
    代价是每次全量读一遍 metadata——34 条的量级，可以忽略。
    """
    collection = open_collection(index_dir)
    metadatas = collection.get(include=["metadatas"])["metadatas"] or []
    return sorted({meta.get("city") for meta in metadatas if meta.get("city")})
