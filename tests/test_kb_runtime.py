"""景点库**运行时**这一层的判据：不依赖 BGE-M3，也不依赖索引已建。

为什么单独一个文件：tests/test_retrieval.py 的 skipif 是模块级的（权重不在就整组跳），
而这里守的东西跟权重没关系——懒加载、索引缺失时的报错、词表与数据的对得上。
放进那个文件会被一起跳掉，等于没守。

对应 docs/ATTRACTION_RETRIEVAL_DESIGN.md 第三、五、十节。
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from travel_assistant.kb import (
    KB_CITIES,
    IndexNotBuilt,
    describe_vocabulary,
    open_collection,
    vocabulary_words,
)
from travel_assistant.kb.paths import ATTRACTIONS_PATH, REPO_ROOT

_HEAVY = ("torch", "sentence_transformers", "chromadb")


# ---------- 懒加载：拍过板的决定，钉住它 ----------

def test_importing_the_kb_package_does_not_pull_heavy_dependencies():
    """接本地检索时拍过板：服务启动不背那 0.87G / 5.9s（第三节实测）。

    走子进程是因为本进程早就把 torch 导进来过了，在进程内查 sys.modules 永远是脏的。
    """
    code = (
        "import sys, travel_assistant.kb;"
        f"print(','.join(m for m in {_HEAVY!r} if m in sys.modules))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True, text=True
    )

    assert result.returncode == 0, result.stderr
    pulled = result.stdout.strip()
    assert pulled == "", f"import travel_assistant.kb 连带加载了 {pulled}，懒加载被破坏了"


def test_shouldRaiseClearErrorWhenIndexIsNotBuilt(tmp_path):
    """索引是 gitignore 的产物。没建时要抛得明白，让工具层能照实说，而不是抛一个看不懂的栈。"""
    with pytest.raises(IndexNotBuilt) as error:
        open_collection(index_dir=tmp_path / "not-there")

    assert "还没建" in str(error.value)


# ---------- 常量与数据：写死的那句"只收录杭州"不能过期 ----------

def test_kb_cities_constant_matches_attractions_data():
    """`KB_CITIES` 只写进工具说明给模型看，**判断**走 indexed_cities()（索引里实际有什么）。

    写死是为了不在建 agent 时把 chromadb 拖进服务启动。代价是会过期——这条判据把代价兜住。
    """
    records = json.loads(ATTRACTIONS_PATH.read_text(encoding="utf-8"))
    assert set(KB_CITIES) == {record["city"] for record in records}


# ---------- 词表：给模型的那份必须和文件一致 ----------

def test_describe_vocabulary_lists_every_word():
    """渲染进工具说明的那一行，不能漏词——漏了模型就永远挑不到它。"""
    text = describe_vocabulary()
    for word in vocabulary_words():
        assert word in text, f"工具说明里漏了「{word}」"


def test_vocabulary_has_no_duplicate_word():
    words = vocabulary_words()
    assert len(words) == len(set(words)), "同一个词出现在两个分组里"
