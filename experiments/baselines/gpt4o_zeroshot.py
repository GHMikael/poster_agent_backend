"""LLM zero-shot baseline — single LLM call → PosterTask → our renderer.

Design choice (must be explicit in the paper): the renderer is shared
with Ours, so this baseline isolates *content planning quality* from
*rendering quality*. An LLM cannot reliably emit valid PPTX XML, and
even if it could, comparing LLM-generated PPTX against our PPTX would
conflate two confounds. By giving the LLM the same renderer we give
Ours, the difference between ``ours_no_svfp`` and this baseline is
purely the planner prompt (``ours_no_svfp`` uses our domain-specific
planner from Dify; this baseline uses a generic one-shot prompt).

Despite the file name, the underlying LLM is *configurable*. The default
is Qwen2.5-72B-Instruct via SiliconFlow (matches the rest of the project
and works with reviewers who don't have an OpenAI key). Set
``model: gpt-4o-2024-11-20`` + ``api_key_env: OPENAI_API_KEY`` +
``base_url: https://api.openai.com/v1`` in baselines.yaml to use true
GPT-4o. The paper should cite the exact model id used.

Prompt template lives at ``configs/prompts/gpt4o_zeroshot_plan.txt``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from app.ppt_renderer import generate_dashboard_pptx
from experiments.baselines.base import BaselineRunner, PosterArtifact
from experiments.baselines._planner_shared import extract_assets


__all__ = ["GPT4oZeroShotRunner"]


class GPT4oZeroShotRunner(BaselineRunner):
    name = "gpt4o_zeroshot"

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(config)
        self.model = self.config.get("model", "Qwen/Qwen3-32B")
        self.temperature = float(self.config.get("temperature", 0.2))
        self.api_key_env = self.config.get("api_key_env", "DASHSCOPE_API_KEY")
        self.base_url = self.config.get("base_url")  # None → default SiliconFlow

    def run(
        self,
        paper_path: Path,
        out_dir: Path,
        *,
        timeout_s: int = 600,
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

            # The env vars were set by self._begin() — pick up the per-cell logger.
            logger = get_logger_from_env(run_id=f"{self.name}_{paper_path.stem}")

            task = plan_poster(
                assets,
                model=self.model,
                temperature=self.temperature,
                api_key_env=self.api_key_env,
                base_url=self.base_url,
                experiment_logger=logger,
            )
            task.use_commenter = False
            task.max_iterations = 1
            meta.config["planner_source"] = f"llm_zeroshot:{self.model}"

            pptx_buf = generate_dashboard_pptx(task)
            dest_pptx = cell_dir / "poster.pptx"
            dest_pptx.write_bytes(pptx_buf.getvalue())

            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=dest_pptx,
                panels_json=task.model_dump(),
                log_path=log_path,
            )
        except Exception as exc:
            return self._finish(
                cell_dir=cell_dir, meta=meta, t0=t0,
                pptx_path=None, panels_json=None, log_path=log_path,
                exit_code=1, error=f"{type(exc).__name__}: {exc}",
            )
