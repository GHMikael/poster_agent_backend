"""Shared JSONL reader used by D1/D2/D3 metrics.

Production-side writers (``JsonlExperimentLogger``) emit one JSON object
per line; downstream metrics historically duplicated the same forgiving
reader. Centralised here so a buggy file (truncated tail line, mid-write
crash) is handled the same way everywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Union


PathLike = Union[str, Path, None]


def read_jsonl(path: PathLike) -> List[Dict[str, Any]]:
    """Return the JSON objects from ``path``; missing / empty / malformed
    files become an empty list. Individual unparseable lines are skipped
    (so a crash mid-write doesn't lose the lines before it)."""
    if path is None:
        return []
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out
