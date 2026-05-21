"""Per-run output archive for the poster pipeline.

Every invocation of :class:`app.feedback_loop.VisualFeedbackLoop` (and the
non-feedback path in :mod:`app.main`) writes a self-contained folder under
``outputs/runs/`` so future-you can find what produced any given poster
without grepping through ``static/`` debug dumps.

Folder layout (created lazily by :meth:`RunArchive.create`)::

    outputs/runs/
        20260519_205621_paper-slug_d2b94241/    # one folder per run
            input.json          # exact PosterTask payload that went in
            run_report.json     # input + iterations + summary + timing
            final.pptx          # best-scoring pptx (alias of pptx/iter_N.pptx)
            preview/
                iter_1_preview.png
                iter_2_preview.png
                ...
            pptx/
                iter_1.pptx
                iter_2.pptx
                ...
        INDEX.md                # chronological table of all runs

The ``INDEX.md`` file is regenerated from scratch on every
:func:`update_runs_index` call so the table always reflects what's on
disk — no stale references to deleted runs.
"""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.config import OUTPUT_PATH


__all__ = [
    "RunArchive",
    "update_runs_index",
    "slugify",
]


RUNS_ROOT = OUTPUT_PATH / "runs"


def slugify(text: str, max_len: int = 30) -> str:
    """Convert a free-form title into a filesystem-safe short slug.

    Keeps CJK characters and digits, replaces whitespace / punctuation
    with underscores, collapses repeats. Always returns a non-empty
    string (falls back to ``"untitled"``).
    """

    text = (text or "").strip()
    text = re.sub(r"[\s/\\:*?\"<>|\.]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return (text[:max_len] or "untitled")


@dataclass
class RunArchive:
    """A single run's archive folder under :data:`RUNS_ROOT`.

    Construction is done via :meth:`create` so callers don't have to think
    about timestamp formatting or slug derivation.
    """

    run_dir: Path
    run_id: str
    started_at: str
    folder_name: str
    preview_dir: Path = field(init=False)
    pptx_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        self.preview_dir = self.run_dir / "preview"
        self.pptx_dir = self.run_dir / "pptx"
        self.preview_dir.mkdir(parents=True, exist_ok=True)
        self.pptx_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        run_id: str,
        poster_title: str,
        archive_root: Optional[Path] = None,
    ) -> "RunArchive":
        """Create a fresh run folder named ``YYYYMMDD_HHMMSS_<slug>_<runid>``.

        ``archive_root`` defaults to :data:`RUNS_ROOT`; pass a different
        directory (e.g. a tempdir) to keep tests and the in-file demo from
        polluting the production archive.
        """

        now = datetime.now()
        slug = slugify(poster_title)
        folder_name = f"{now.strftime('%Y%m%d_%H%M%S')}_{slug}_{run_id}"
        root = archive_root if archive_root is not None else RUNS_ROOT
        run_dir = root / folder_name
        run_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            run_dir=run_dir,
            run_id=run_id,
            started_at=now.isoformat(timespec="seconds"),
            folder_name=folder_name,
        )

    # ------------------------------------------------------------------
    # Saving artifacts
    # ------------------------------------------------------------------

    def save_input(self, input_task: Dict[str, Any]) -> Path:
        """Persist the validated input payload as ``input.json``."""

        path = self.run_dir / "input.json"
        path.write_text(
            json.dumps(input_task, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    def save_preview(self, src: Path) -> Optional[Path]:
        """Copy an iteration preview PNG into ``preview/``."""

        return self._copy_if_exists(src, self.preview_dir)

    def save_pptx(self, src: Path) -> Optional[Path]:
        """Copy an iteration PPTX into ``pptx/``."""

        return self._copy_if_exists(src, self.pptx_dir)

    def save_final_pptx_bytes(self, data: bytes, name: str = "final.pptx") -> Path:
        """Write the final selected PPTX bytes into the run root."""

        path = self.run_dir / name
        path.write_bytes(data)
        return path

    def save_final_pptx_from(self, src: Path, name: str = "final.pptx") -> Optional[Path]:
        """Copy the chosen final PPTX from elsewhere into the run root."""

        if not src.exists():
            return None
        dest = self.run_dir / name
        shutil.copy2(src, dest)
        return dest

    def save_report(
        self,
        input_task: Dict[str, Any],
        summary: Dict[str, Any],
        iterations: List[Dict[str, Any]],
        extra: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """Write the consolidated ``run_report.json`` and return its path.

        The report is the single file you want to open after the run — it
        embeds input, iteration log and summary so you don't have to chase
        cross-file references.
        """

        report = {
            "run_id": self.run_id,
            "folder": self.folder_name,
            "started_at": self.started_at,
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "input": input_task,
            "summary": summary,
            "iterations": iterations,
        }
        if extra:
            report.update(extra)
        path = self.run_dir / "run_report.json"
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _copy_if_exists(src: Path, dest_dir: Path) -> Optional[Path]:
        if not src or not src.exists():
            return None
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        if src.resolve() == dest.resolve():
            return dest
        shutil.copy2(src, dest)
        return dest


# ---------------------------------------------------------------------------
# Index regeneration
# ---------------------------------------------------------------------------


def update_runs_index(runs_root: Optional[Path] = None) -> Path:
    """Scan ``runs_root`` and (re)write ``INDEX.md`` sorted by start time desc.

    Each row links to the run folder and shows best score, iterations
    and convergence reason. Runs without a valid ``run_report.json`` are
    skipped (they appear under the "incomplete" section).

    The ``runs_root`` argument is resolved at call time (default
    :data:`RUNS_ROOT`) so monkey-patching the module-level constant during
    tests works as expected.
    """

    if runs_root is None:
        runs_root = RUNS_ROOT
    index_path = runs_root / "INDEX.md"
    if not runs_root.exists():
        runs_root.mkdir(parents=True, exist_ok=True)
        index_path.write_text("# Run Archive Index\n\n_No runs yet._\n", encoding="utf-8")
        return index_path

    complete: List[Dict[str, Any]] = []
    incomplete: List[str] = []
    for d in runs_root.iterdir():
        if not d.is_dir():
            continue
        report_path = d / "run_report.json"
        if not report_path.exists():
            incomplete.append(d.name)
            continue
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            incomplete.append(d.name)
            continue
        summary = report.get("summary") or {}
        input_task = report.get("input") or {}
        complete.append(
            {
                "folder": d.name,
                "started_at": report.get("started_at", "0000-00-00T00:00:00"),
                "title": input_task.get("poster_title", "(no title)"),
                "best_score": summary.get("best_score"),
                "iterations": summary.get("iterations"),
                "converged": summary.get("converged"),
                "reason": summary.get("convergence_reason", ""),
                "final_pptx": (d / "final.pptx").name if (d / "final.pptx").exists() else "",
            }
        )

    complete.sort(key=lambda r: r["started_at"], reverse=True)

    lines: List[str] = [
        "# Run Archive Index",
        "",
        f"_Generated: {datetime.now().isoformat(timespec='seconds')}  ·  {len(complete)} run(s)_",
        "",
        "| Time | Poster Title | Best Score | Iters | Converged | Reason | Folder |",
        "|------|--------------|-----------:|------:|:---------:|--------|--------|",
    ]
    for r in complete:
        title = (r["title"] or "")[:40]
        converged_str = "✓" if r["converged"] else "·"
        score_str = "" if r["best_score"] is None else f"{r['best_score']:.2f}"
        iter_str = "" if r["iterations"] is None else str(r["iterations"])
        lines.append(
            f"| {r['started_at']} | {title} | {score_str} | {iter_str} | "
            f"{converged_str} | {r['reason']} | [`{r['folder']}`](./{r['folder']}/run_report.json) |"
        )

    if incomplete:
        lines.append("")
        lines.append("## Incomplete / unparseable runs")
        for name in sorted(incomplete):
            lines.append(f"- `{name}`")

    lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return index_path


# ---------------------------------------------------------------------------
# Example usage
# ---------------------------------------------------------------------------


def _demo() -> Path:
    """Create a fake run in a tempdir so callers can verify the on-disk layout."""

    tmp_root = Path(tempfile.mkdtemp(prefix="run_archive_demo_"))
    archive = RunArchive.create(
        run_id="demoxx", poster_title="Demo Paper", archive_root=tmp_root
    )
    archive.save_input({"poster_title": "Demo Paper", "panels": [{"section": "Intro"}]})
    archive.save_final_pptx_bytes(b"PPTX-CONTENT-PLACEHOLDER")
    archive.save_report(
        input_task={"poster_title": "Demo Paper"},
        summary={
            "best_score": 8.4,
            "iterations": 2,
            "converged": True,
            "convergence_reason": "no_issues",
            "score_curve": [7.0, 8.4],
        },
        iterations=[
            {"iteration": 1, "score": 7.0, "feedback": {"source": "vlm"}},
            {"iteration": 2, "score": 8.4, "feedback": {"source": "vlm"}},
        ],
    )
    index_path = update_runs_index(runs_root=tmp_root)
    print(f"Index regenerated at {index_path}")
    print(f"Run folder: {archive.run_dir}")
    return archive.run_dir


if __name__ == "__main__":
    _demo()
