"""Ours — full SVFP feedback loop baseline.

Pipeline: PDF → :func:`heuristic_plan` (M2) or :func:`gpt4o_plan` (M3) →
:class:`VisualFeedbackLoop` (use_commenter=True, max_iterations=4).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.feedback_loop import VisualFeedbackLoop
from experiments.baselines.base import BaselineRunner, PosterArtifact
from experiments.baselines._planner_shared import cached_plan, extract_assets, heuristic_plan


__all__ = ["OursSVFPRunner"]


class OursSVFPRunner(BaselineRunner):
    name = "ours_svfp"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.max_iterations = int(self.config.get("max_iterations", 4))
        self.use_gpt4o_planner = bool(self.config.get("use_gpt4o_planner", False))

    def run(
        self,
        paper_path: Path,
        out_dir: Path,
        *,
        timeout_s: int = 1800,
    ) -> PosterArtifact:
        cell_dir, log_path, meta, t0 = self._begin(paper_path, out_dir)
        try:
            # Planner priority:
            #   1. planner_cache/<arxiv_id>.json  (Dify production replay)
            #   2. heuristic_plan() (M2 fallback)
            # The M3 GPT-4o planner becomes a third tier when use_gpt4o_planner=True.
            task = cached_plan(paper_path)
            planner_source = "dify_cache"
            if task is None:
                assets = extract_assets(paper_path)
                meta.paper_title = assets.title
                task = heuristic_plan(assets)
                planner_source = "heuristic"
            else:
                meta.paper_title = task.poster_title

            task.use_commenter = True
            task.max_iterations = self.max_iterations
            meta.config["planner_source"] = planner_source

            result = VisualFeedbackLoop().run(task)

            # Copy the run's final pptx into the cell dir so all baselines
            # share a uniform on-disk layout for compute_metrics.
            src_pptx = Path(result["final_path"])
            dest_pptx = cell_dir / "poster.pptx"
            if src_pptx.exists():
                dest_pptx.write_bytes(src_pptx.read_bytes())

            panels_json = result.get("task")  # PosterTask object
            return self._finish(
                cell_dir=cell_dir,
                meta=meta,
                t0=t0,
                pptx_path=dest_pptx if dest_pptx.exists() else None,
                panels_json=panels_json.model_dump() if hasattr(panels_json, "model_dump") else None,
                log_path=log_path,
            )
        except Exception as exc:
            return self._finish(
                cell_dir=cell_dir,
                meta=meta,
                t0=t0,
                pptx_path=None,
                panels_json=None,
                log_path=log_path,
                exit_code=1,
                error=f"{type(exc).__name__}: {exc}",
            )
