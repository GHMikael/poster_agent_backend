"""BaselineRunner abstract base + the shared PosterArtifact dataclass.

Every concrete baseline (Ours-SVFP, Ours-no-SVFP, GPT-4o zero-shot,
Paper2Poster, PosterAgent) inherits :class:`BaselineRunner` and implements
:meth:`run`. The orchestrator in ``experiments/scripts/run_matrix.py``
instantiates one runner per baseline and dispatches ``paper × runner``
cells in parallel.

A run produces a :class:`PosterArtifact` whose four files
(``poster.pptx``, ``poster.png``, ``metadata.json``,
``experiment_log.jsonl``) are everything the downstream metric and
plotting code needs.
"""

from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional


__all__ = [
    "BaselineRunner",
    "PosterArtifact",
    "RunMetadata",
    "BaselineError",
    "ensure_artifact_dir",
    "render_pptx_to_png_via_app",
]


class BaselineError(RuntimeError):
    """Raised when a baseline cannot produce a poster (other than expected
    timeouts, which are reported via the artifact ``metadata.exit_code``)."""


@dataclass
class RunMetadata:
    """One row per ``(baseline, paper)`` cell."""

    baseline: str
    arxiv_id: str
    paper_title: str = ""
    started_at: str = ""
    finished_at: str = ""
    total_latency_ms: float = 0.0
    exit_code: int = 0                # 0 = success, !=0 = failed (D3)
    error: str = ""
    config: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


@dataclass
class PosterArtifact:
    """Everything a metric needs to evaluate a single (baseline × paper)."""

    pptx_path: Path
    png_path: Optional[Path]              # None when soffice failed
    panels_json: Optional[Dict[str, Any]] # the PosterTask snapshot when available
    metadata: RunMetadata
    raw_log_path: Optional[Path] = None

    def save_metadata(self) -> Path:
        out = self.pptx_path.parent / "metadata.json"
        out.write_text(self.metadata.to_json(), encoding="utf-8")
        return out


def ensure_artifact_dir(out_dir: Path, baseline: str, arxiv_id: str) -> Path:
    """Standardised per-cell folder ``out_dir/<baseline>_<arxiv_id>/``."""
    cell = out_dir / f"{baseline}_{arxiv_id}"
    cell.mkdir(parents=True, exist_ok=True)
    return cell


def render_pptx_to_png_via_app(pptx_path: Path) -> Optional[Path]:
    """Convert a PPTX → PNG using the same LibreOffice path the
    production app uses. Lives here so the external baselines
    (Paper2Poster, PosterAgent) can render PNGs without reimplementing
    soffice fallbacks. Returns ``None`` when LibreOffice is unavailable.
    """
    from app.feedback_loop import render_pptx_to_png  # local import

    return render_pptx_to_png(pptx_path, pptx_path.parent)


class BaselineRunner(ABC):
    """Abstract baseline runner.

    Subclasses implement :meth:`run` to take a paper PDF and produce a
    :class:`PosterArtifact`. The base class supplies a small bit of
    plumbing: enabling ``POSTER_EXPERIMENT_MODE`` for the child process
    (or in-process for HTTP-bound runners) and writing a stub metadata
    file even on failure so D3 can count it.
    """

    name: str = "baseline"
    config: Dict[str, Any] = {}

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self.config = config or {}

    @abstractmethod
    def run(
        self,
        paper_path: Path,
        out_dir: Path,
        *,
        timeout_s: int = 1800,
    ) -> PosterArtifact: ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def _begin(self, paper_path: Path, out_dir: Path) -> tuple[Path, Path, RunMetadata, float]:
        arxiv_id = paper_path.stem
        cell_dir = ensure_artifact_dir(out_dir, self.name, arxiv_id)
        log_path = cell_dir / "experiment_log.jsonl"
        # Per-cell env so each subprocess writes to its own JSONL.
        os.environ["POSTER_EXPERIMENT_MODE"] = "1"
        os.environ["POSTER_EXPERIMENT_LOG"] = str(log_path)

        meta = RunMetadata(
            baseline=self.name,
            arxiv_id=arxiv_id,
            started_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            config={k: v for k, v in self.config.items() if k != "secrets"},
        )
        return cell_dir, log_path, meta, time.perf_counter()

    def _finish(
        self,
        cell_dir: Path,
        meta: RunMetadata,
        t0: float,
        *,
        pptx_path: Optional[Path],
        panels_json: Optional[Dict[str, Any]],
        log_path: Optional[Path],
        exit_code: int = 0,
        error: str = "",
    ) -> PosterArtifact:
        meta.finished_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        meta.total_latency_ms = (time.perf_counter() - t0) * 1000
        meta.exit_code = exit_code
        meta.error = error

        png_path: Optional[Path] = None
        if pptx_path is not None and pptx_path.exists():
            png_path = render_pptx_to_png_via_app(pptx_path)

        artifact = PosterArtifact(
            pptx_path=pptx_path or (cell_dir / "poster.pptx"),
            png_path=png_path,
            panels_json=panels_json,
            metadata=meta,
            raw_log_path=log_path if (log_path and log_path.exists()) else None,
        )
        artifact.save_metadata()
        if panels_json is not None:
            # Persist the PosterTask snapshot alongside metadata.json so the
            # A1/A3/A4/B1 metrics can read it without going back through the
            # baseline runner. compute_metrics.py reads cell_dir/panels.json.
            (cell_dir / "panels.json").write_text(
                json.dumps(panels_json, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return artifact
