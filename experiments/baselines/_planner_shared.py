"""Shared PDF → ``PosterTask`` planning helpers used by the Ours-* baselines.

Three strategies live here, in priority order for the ``Ours`` runners:

1. :func:`cached_plan` — load a pre-recorded ``PosterTask`` JSON from
   ``experiments/datasets/planner_cache/<arxiv_id>.json``. **This is how
   experiments stay faithful to the production Dify pipeline**: you run
   the 30 papers through your real Dify workflow once, capture each
   produced PosterTask JSON, and check those into the planner cache.
   Reruns of the experiment matrix then use the *exact* same planner
   output as production.
2. :func:`gpt4o_plan` — GPT-4o JSON-mode call (M3 deliverable). Used as
   a Dify-free fallback so reviewers can reproduce without a Dify install.
3. :func:`heuristic_plan` — deterministic, no LLM. Suitable for the M2
   smoke gate and for repeatable D1/D2/D3 measurements where we want
   zero variance in the planning step.

Both LLM strategies return a validated :class:`~app.models.PosterTask`.
All three reuse ``app.pdf_assets.extract_pdf_assets_from_bytes`` so
figure extraction is identical to production.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from app.models import ExtractedFigure, FigureAsset, Panel, PosterTask
from app.pdf_assets import extract_pdf_assets_from_bytes


__all__ = [
    "PaperAssets",
    "extract_assets",
    "cached_plan",
    "heuristic_plan",
    "gpt4o_plan",
    "PLANNER_CACHE_DIR",
]


PLANNER_CACHE_DIR = Path("experiments/datasets/planner_cache")


_SECTION_PATTERNS: List[Tuple[str, List[str]]] = [
    ("Motivation / Background", ["motivation", "background", "introduction", "intro"]),
    ("Problem / Challenge", ["problem", "challenge", "research question"]),
    ("Method / Framework", ["method", "framework", "approach", "model"]),
    ("Experiments / Results", ["experiment", "result", "evaluation", "benchmark"]),
    ("Key Findings", ["finding", "discussion", "ablation", "analysis"]),
    ("Conclusion / Takeaways", ["conclusion", "future work", "summary"]),
]


class PaperAssets:
    """Bundle of (text_preview, figures, title, authors)."""

    def __init__(self, text: str, figures: Dict[str, ExtractedFigure], title: str = "", authors: str = "") -> None:
        self.text = text
        self.figures = figures
        self.title = title
        self.authors = authors


def extract_assets(paper_path: Path) -> PaperAssets:
    pdf_bytes = paper_path.read_bytes()
    text, figures = extract_pdf_assets_from_bytes(pdf_bytes)
    title, authors = _guess_title_and_authors(text, fallback_title=paper_path.stem)
    return PaperAssets(text=text, figures=figures, title=title, authors=authors)


# ---------------------------------------------------------------------------
# Cached planner — replays a previously recorded Dify production output
# ---------------------------------------------------------------------------


def cached_plan(
    paper_path: Path,
    *,
    cache_dir: Path = PLANNER_CACHE_DIR,
) -> Optional[PosterTask]:
    """Return the cached production PosterTask for ``paper_path`` if it exists.

    Cache lookup tries, in order:

    1. ``cache_dir/<paper_path.stem>.json``
    2. ``cache_dir/<paper_path.name>.json`` (with extension; legacy)

    The file should be the exact JSON body that your Dify workflow's
    final HTTP node sends to ``POST /generate_ppt`` — i.e. a serialised
    :class:`~app.models.PosterTask`. The simplest way to fill the cache:

    1. Run your Dify workflow normally on each of the 30 papers.
    2. In your Dify workflow add an "End" node that dumps the assembled
       payload (or pull it from the FastAPI logs via
       ``outputs/runs/<run>/input.json``).
    3. Copy each ``input.json`` to
       ``experiments/datasets/planner_cache/<arxiv_id>.json``.

    Returns ``None`` when no cache exists, so callers can fall back to
    ``heuristic_plan`` (M2) or ``gpt4o_plan`` (M3 ablation).
    """
    for candidate in (cache_dir / f"{paper_path.stem}.json", cache_dir / f"{paper_path.name}.json"):
        if candidate.exists():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
                return PosterTask(**data)
            except Exception as exc:
                raise ValueError(f"planner_cache entry {candidate} is malformed: {exc}") from exc
    return None


# ---------------------------------------------------------------------------
# Heuristic planner — deterministic, no LLM
# ---------------------------------------------------------------------------


def heuristic_plan(
    assets: PaperAssets,
    *,
    template: str = "template_dashboard",
    color_theme: str = "academic_blue",
) -> PosterTask:
    """Build a 6-panel ``PosterTask`` from heuristic section detection.

    Used for M2 smoke and as a fixed-planner control in D1/D2 measurements.
    Real publishability of A1-A4 content metrics requires :func:`gpt4o_plan`
    which is invoked from M3 onwards.
    """
    sections = _slice_into_sections(assets.text)
    panels = _build_panels_from_sections(sections, list(assets.figures.values()))
    figures = {fid: _figure_to_asset(f) for fid, f in assets.figures.items()}

    return PosterTask(
        asset_token=f"heuristic_{abs(hash(assets.text)) % 1_000_000:06d}",
        template=template,
        color_theme=color_theme,
        poster_title=assets.title or "Untitled Paper",
        authors=assets.authors,
        panels=panels,
        figures=figures,
        use_commenter=False,
        max_iterations=1,
    )


# ---------------------------------------------------------------------------
# GPT-4o JSON-mode planner — content-quality oriented
# ---------------------------------------------------------------------------


def gpt4o_plan(
    assets: PaperAssets,
    *,
    model: str = "gpt-4o-2024-11-20",
    temperature: float = 0.2,
    template: str = "template_dashboard",
    color_theme: str = "academic_blue",
    experiment_logger: Optional[Any] = None,
) -> PosterTask:
    """Ask GPT-4o to plan the poster end-to-end.

    The call returns a fully validated ``PosterTask`` or raises
    :class:`ValueError` if the model output doesn't conform. Token counts
    and latency are recorded via ``experiment_logger`` for D2 cost
    accounting (M3 main experiment).

    Implementation note: kept as a stub so M2 smoke can land without an
    OPENAI_API_KEY. The full call is implemented in M3 in
    ``experiments/judges/gpt4o_planner.py`` (factored out because
    multiple baselines need it).
    """
    raise NotImplementedError(
        "gpt4o_plan is implemented as part of M3 deliverables; the M2 smoke "
        "gate uses heuristic_plan(). See experiments/judges/gpt4o_planner.py."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _guess_title_and_authors(text: str, *, fallback_title: str) -> Tuple[str, str]:
    """Heuristic title and author extraction from the first ~1000 chars."""
    head = (text or "")[:1500]
    lines = [ln.strip() for ln in head.split("\n") if ln.strip()]
    title = ""
    authors = ""
    # Title is usually the first non-trivial multi-word line, all in caps or title case.
    for ln in lines[:8]:
        if 10 < len(ln) < 200 and not ln.lower().startswith(("arxiv", "doi", "abstract")):
            title = ln
            break
    # Authors line usually follows the title and contains commas, "and", or affiliations.
    for ln in lines[1:12]:
        if ln == title:
            continue
        if any(tok in ln for tok in [", ", " and ", "; "]) and len(ln) < 400:
            authors = ln
            break
    return (title or fallback_title), authors


def _slice_into_sections(text: str) -> Dict[str, List[str]]:
    """Bucket sentences into the canonical 6 sections by keyword match."""

    sentences = _split_sentences(text)
    buckets: Dict[str, List[str]] = {name: [] for name, _ in _SECTION_PATTERNS}
    for sent in sentences:
        s_lower = sent.lower()
        for name, keys in _SECTION_PATTERNS:
            if any(k in s_lower for k in keys):
                buckets[name].append(sent)
                break
    # Ensure every section has at least a few candidate sentences by spilling
    # from the first non-empty bucket.
    pool = [s for s in sentences if len(s) > 20]
    for name, _ in _SECTION_PATTERNS:
        while len(buckets[name]) < 2 and pool:
            buckets[name].append(pool.pop(0))
    return buckets


def _split_sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+(?=[A-Z一-鿿])", (text or "").strip())
    return [p.strip() for p in parts if p and len(p.strip()) > 10]


def _build_panels_from_sections(
    buckets: Dict[str, List[str]],
    figures: List[ExtractedFigure],
) -> List[Panel]:
    """One panel per canonical section, 3-4 bullets, figure if available."""
    panels: List[Panel] = []
    fig_idx = 0
    for name, _ in _SECTION_PATTERNS:
        content = [s[:200] for s in buckets[name][:4]]
        figure_id = ""
        caption = ""
        layout_hint = "text_only"
        if fig_idx < len(figures) and name not in {"Motivation / Background", "Conclusion / Takeaways"}:
            fig = figures[fig_idx]
            figure_id = fig.figure_id
            caption = fig.caption[:200]
            layout_hint = "text_left_image_right"
            fig_idx += 1
        panels.append(
            Panel(
                section=name,
                content=content or ["(no extracted content)"],
                figure_id=figure_id,
                figure_caption=caption,
                layout_hint=layout_hint,
                body_font_scale=1.0,
            )
        )
    return panels


def _figure_to_asset(fig: ExtractedFigure) -> FigureAsset:
    return FigureAsset(
        caption=fig.caption,
        type="other",
        description=fig.caption[:120],
        importance="medium",
        image_source=fig.image_source,
        image_url=fig.image_url,
        thumbnail_url=fig.thumbnail_url,
    )
