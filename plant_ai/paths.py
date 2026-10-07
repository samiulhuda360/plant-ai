"""Filesystem locations. PLANT_AI_HOME moves all generated data (historian, caches, reports) elsewhere."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOME = Path(os.getenv("PLANT_AI_HOME", ROOT))
DATA = HOME / "data"
CACHE = HOME / ".cache"
REPORTS = HOME / "reports"
KB = Path(__file__).resolve().parent / "assistant" / "kb"
EVAL = ROOT / "eval"
HISTORIAN = DATA / "historian.db"
