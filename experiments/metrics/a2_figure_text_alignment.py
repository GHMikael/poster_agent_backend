"""A2 — Figure-Text Alignment.

For each panel that declares a figure, ask a VLM (Qwen-VL by default) to
rate how well the figure aligns with the panel's bullets + caption on a
0-5 scale. Headline score = mean / 5.0 (so [0, 1] range).

This replaces the AltCLIP cosine approach: it doesn't require a 3GB
model download, doesn't depend on per-paper gold figure annotations,
and produces a more directly interpretable number for the paper.

For baselines without panel-figure linkage (Paper2Poster / PosterAgent
opaque PPTX), the metric skips with a clear reason — the OCR fallback
remains M3 work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from experiments.judges.altclip_judge import alignment_score
from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


@MetricRegistry.register
class A2FigureTextAlignment(Metric):
    metric_id = "a2_figure_text_alignment"
    description = "Mean VLM-rated figure-text alignment across panels that declare a figure (0-1, higher better)."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if ctx.panels_json is None:
            return self._skip("no panels_json; opaque baselines are M3 (OCR + figure pairing)")

        panels = (ctx.panels_json or {}).get("panels") or []
        figures = (ctx.panels_json or {}).get("figures") or {}
        if not panels or not figures:
            return self._skip("panels or figures dict empty")

        model = cfg.get("vlm_judge", {}).get("model", "Qwen/Qwen3-VL-32B-Instruct")

        per_panel: List[Dict[str, Any]] = []
        scores: List[float] = []
        missing_images: List[str] = []

        for p in panels:
            fig_id = str(p.get("figure_id") or "").strip()
            if not fig_id:
                continue
            fig = figures.get(fig_id)
            if not fig:
                continue
            img_source = str(fig.get("image_source") or "")
            if not img_source or not Path(img_source).exists():
                missing_images.append(fig_id)
                continue

            bullets = [str(b) for b in (p.get("content") or []) if str(b).strip()]
            result = alignment_score(
                image_path=Path(img_source),
                section=str(p.get("section") or ""),
                bullets=bullets,
                caption=str(p.get("figure_caption") or fig.get("caption") or ""),
                model=model,
            )
            scores.append(result["score"])
            per_panel.append({
                "section": p.get("section"),
                "figure_id": fig_id,
                "score": result["score"],
                "rationale": result["rationale"],
            })

        if not scores:
            return self._skip(
                f"no panel had both a figure_id and a readable image_source "
                f"(missing images: {missing_images[:3]})"
            )

        mean_score_norm = (sum(scores) / len(scores)) / 5.0  # → [0, 1]
        return MetricResult(
            metric_id=self.metric_id,
            score=round(mean_score_norm, 4),
            extra={
                "n_panels_with_figure": len(scores),
                "mean_raw_score_0_5": round(sum(scores) / len(scores), 3),
                "per_panel": per_panel,
                "missing_images": missing_images,
            },
        )
