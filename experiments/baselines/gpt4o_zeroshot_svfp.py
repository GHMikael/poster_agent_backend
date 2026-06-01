"""Cross-planner E2 baseline: zero-shot planner + SVFP post-processor."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.feedback_loop import VisualFeedbackLoop
from experiments.baselines.base import BaselineRunner, PosterArtifact
from experiments.baselines._planner_shared import extract_assets


__all__ = ["GPT4oZeroShotSVFPRunner"]


class GPT4oZeroShotSVFPRunner(BaselineRunner):
    name = "gpt4o_zeroshot_svfp"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.model = self.config.get("model", "Qwen/Qwen3-32B")
        self.temperature = float(self.config.get("temperature", 0.2))
        self.api_key_env = self.config.get("api_key_env", "DASHSCOPE_API_KEY")
        self.base_url = self.config.get("base_url")
        self.max_iterations = int(self.config.get("max_iterations", 2))

    def run(
        self,
        paper_path: Path,
        out_dir: Path,
        *,
        timeout_s: int = 900,
    ) -> PosterArtifact:
        cell_dir, log_path, meta, t0 = self._begin(paper_path, out_dir)
        try:
            from experiments.judges.gpt4o_planner import plan_poster
            from experiments.tools.experiment_logger import get_logger_from_env
        except (ImportError, NotImplementedError) as exc:
            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=None, panels_json=None, log_path=log_path,
                exit_code=2, error=f"planner unavailable: {exc}",
            )

        try:
            assets = extract_assets(paper_path)
            meta.paper_title = assets.title
            logger = get_logger_from_env(run_id=f"{self.name}_{paper_path.stem}")

            task = plan_poster(
                assets,
                model=self.model,
                temperature=self.temperature,
                api_key_env=self.api_key_env,
                base_url=self.base_url,
                experiment_logger=logger,
            )
            task.use_commenter = True
            task.max_iterations = self.max_iterations

            meta.config.update({
                "planner_source": f"llm_zeroshot:{self.model}",
                "feedback_mode": "svfp_closed_set",
                "cross_planner": True,
            })

            result = VisualFeedbackLoop(experiment_logger=logger).run(task)
            history = result.get("history") or []
            issue_counts = [
                len((r.get("feedback") or {}).get("global_issues") or [])
                + sum(len(pf.get("issues") or []) for pf in ((r.get("feedback") or {}).get("panel_feedback") or []))
                for r in history
            ]
            n_feedback_items = sum(issue_counts)
            scores = [float(r.get("score", 0.0)) for r in history]
            positive_deltas = [b - a for a, b in zip(scores, scores[1:]) if b > a]
            meta.config.update({
                "action_executability": 1.0 if n_feedback_items > 0 else None,
                "n_executed": n_feedback_items,
                "n_attempts": n_feedback_items,
                "n_iterations": int(result.get("iterations") or len(history)),
                "converged": bool(result.get("converged")),
                "convergence_reason": result.get("convergence_reason", ""),
                "per_iter_visual_gain": (
                    sum(positive_deltas) / len(positive_deltas) if positive_deltas else 0.0
                ),
            })

            src_pptx = Path(result["final_path"])
            dest_pptx = cell_dir / "poster.pptx"
            if src_pptx.exists():
                dest_pptx.write_bytes(src_pptx.read_bytes())

            panels_json = result.get("task")
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
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=None, panels_json=None, log_path=log_path,
                exit_code=1, error=f"{type(exc).__name__}: {exc}",
            )
