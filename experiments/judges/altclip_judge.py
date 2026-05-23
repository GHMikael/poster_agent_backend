"""VLM-based figure-text alignment judge for A2.

Replaces the AltCLIP cosine-similarity approach with a direct VLM
rubric call. Given a panel image + its declared caption + its bullets,
ask Qwen-VL to score how well the figure aligns with the text on a
0-5 scale. Faster to set up than downloading 3GB of AltCLIP weights,
and produces a number with more direct semantic meaning to reviewers.

Caching is handled by ``llm_client.vlm_chat``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from experiments.tools.llm_client import parse_json, vlm_chat


_PROMPT = """Rate how well this figure aligns with the surrounding poster text on a 0-5 scale.

Rubric:
  5 = figure directly illustrates the bullets; caption matches the figure content
  4 = figure illustrates the topic but not the exact bullet claims
  3 = figure is related but adds little to the bullets
  2 = figure is loosely related; mostly decorative
  1 = figure barely relates to the panel topic
  0 = figure is unrelated or shows a different topic

PANEL SECTION: {section}
PANEL BULLETS:
{bullets}
DECLARED CAPTION: {caption}

OUTPUT (JSON only):
{"score": <0-5 int>, "rationale": "<one short sentence>"}
"""


def alignment_score(
    *,
    image_path: Path,
    section: str,
    bullets: List[str],
    caption: str,
    model: str = "Qwen/Qwen3-VL-32B-Instruct",
    experiment_logger: Optional[Any] = None,
) -> Dict[str, Any]:
    """Return ``{"score": 0-5 float, "rationale": str}`` for one panel."""
    bullets_str = "\n".join(f"- {b}" for b in bullets[:8])
    user = (
        _PROMPT
        .replace("{section}", (section or "")[:120])
        .replace("{bullets}", bullets_str[:1600])
        .replace("{caption}", (caption or "")[:400])
    )
    result = vlm_chat(
        system="You output only valid JSON. Be precise.",
        user=user,
        image_paths=[image_path],
        model=model,
        temperature=0.0,
        experiment_logger=experiment_logger,
        stage_label="a2_figure_alignment",
    )
    try:
        data = parse_json(result["content"])
    except ValueError:
        return {"score": 0.0, "rationale": "(parse error)"}
    return {
        "score": float(max(0.0, min(5.0, float(data.get("score", 0))))),
        "rationale": str(data.get("rationale", ""))[:300],
    }


# ---------------------------------------------------------------------------
# Backwards-compatible AltCLIP stubs — preserved so any caller that imports
# these names still works (raising NotImplementedError documents the swap).
# ---------------------------------------------------------------------------


def batch_embed_images(*args: Any, **kwargs: Any) -> List[Any]:
    raise NotImplementedError("AltCLIP path retired; use alignment_score() with Qwen-VL.")


def batch_embed_texts(*args: Any, **kwargs: Any) -> List[Any]:
    raise NotImplementedError("AltCLIP path retired; use alignment_score() with Qwen-VL.")


def altclip_cosine(*args: Any, **kwargs: Any) -> float:
    raise NotImplementedError("AltCLIP path retired; use alignment_score() with Qwen-VL.")
