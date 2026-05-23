"""VLM-as-judge for B1 layout rationality.

Passes the rendered poster PNG to GPT-4o (gpt-4o-2024-11-20) with a
5-criterion rubric:

    balance — visual weight distribution
    alignment — edges, grids, gutters
    hierarchy — title > section > body
    whitespace — neither cramped nor empty
    flow — reading order matches reading_order metadata

Each criterion scored 1-5; mean of 5 scores normalised to [0,1] is
returned. Prompt template: ``configs/prompts/vlm_layout_judge.txt``.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional


def layout_score_5_criteria(
    *,
    png_path: Path,
    model: str = "gpt-4o-2024-11-20",
    criteria: Optional[List[str]] = None,
    experiment_logger: Optional[object] = None,
) -> float:
    """Return a layout quality score in [0, 1] from GPT-4o's 5-criterion rubric."""
    raise NotImplementedError(
        "VLM layout judge is M3 deliverable. See configs/prompts/vlm_layout_judge.txt; "
        "expected JSON: {\"balance\": 4, \"alignment\": 5, ...}."
    )
