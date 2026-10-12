"""数据文件的位置。

**只放路径，不 import 任何重依赖**——离线脚本（scripts/kb/）与运行时（本包其余模块）
都要 import 它，所以它必须是最轻的一层。

归属说明：`tags.json` 从本单起是**运行时输入**（提示词要拿词表约束模型），
`attractions.json` 仍是离线输入（运行时只读建成后的 `chroma/`，不读它）。
"""

from __future__ import annotations

from pathlib import Path

# travel_assistant/kb/paths.py -> kb -> travel_assistant -> 仓库根
REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
TAGS_PATH = DATA_DIR / "tags.json"
ATTRACTIONS_PATH = DATA_DIR / "attractions.json"
INDEX_DIR = DATA_DIR / "chroma"
