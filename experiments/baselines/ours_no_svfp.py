"""Ours — ablation: same renderer, no SVFP feedback loop.

Pipeline: PDF → planner → ``generate_dashboard_pptx`` (single shot, no
feedback). Used to quantify the contribution of the SVFP loop in
isolation from the planner and renderer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.ppt_renderer import generate_dashboard_pptx
from experiments.baselines.base import BaselineRunner, PosterArtifact
from experiments.baselines._planner_shared import cached_plan, extract_assets, heuristic_plan


__all__ = ["OursNoSVFPRunner"]


class OursNoSVFPRunner(BaselineRunner):
    name = "ours_no_svfp"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)

    def run(
        self,
        paper_path: Path,
        out_dir: Path,
        *,
        timeout_s: int = 1800,
    ) -> PosterArtifact:
        cell_dir, log_path, meta, t0 = self._begin(paper_path, out_dir)
        try:
            task = cached_plan(paper_path)
            planner_source = "dify_cache"
            if task is None:
                assets = extract_assets(paper_path)
                meta.paper_title = assets.title
                task = heuristic_plan(assets)
                planner_source = "heuristic"
            else:
                meta.paper_title = task.poster_title

            task.use_commenter = False
            task.max_iterations = 1
            meta.config["planner_source"] = planner_source

            pptx_buf = generate_dashboard_pptx(task)
            dest_pptx = cell_dir / "poster.pptx"
            dest_pptx.write_bytes(pptx_buf.getvalue())

            return self._finish(
                cell_dir=cell_dir,
                meta=meta,
                t0=t0,
                pptx_path=dest_pptx,
                panels_json=task.model_dump(),
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
