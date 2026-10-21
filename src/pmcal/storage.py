"""Reading and writing raw API data on disk.

Raw responses are written once and never re-fetched, so an interrupted fetch can
be resumed and the analysis can be rebuilt without touching the network.
"""

from __future__ import annotations

import gzip
import json
import os
from pathlib import Path
from typing import Any


def write_json(path: Path, obj: Any) -> None:
    """Writes atomically so a crash never leaves a half-written file behind."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if path.suffix == ".gz":
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
    else:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, separators=(",", ":"))
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def safe_name(key: str) -> str:
    """Turns an API identifier into a safe file name."""
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in key)
