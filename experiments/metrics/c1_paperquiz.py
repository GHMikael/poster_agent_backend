"""C1 — Task Success Rate via PaperQuiz (flagship user-side metric).

For each paper:

1. ``judges/paperquiz_generator.py`` calls o3 to produce 5 MCQs (4
   options each, exactly one correct). Self-consistency: o3 answers its
   own questions given the paper text; questions where it fails are
   regenerated.
2. For each (paper, baseline) cell, ``judges/paperquiz_answerer.py``
   asks Qwen2.5-VL-72B and GPT-4o to answer the 5 MCQs from ONLY the
   poster image.

TSR(paper, baseline) = mean over answerers of (correct / 5).

Per-paper MCQs are cached so the same questions are used for all 4
baselines, isolating poster quality from question variance.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


@MetricRegistry.register
class C1PaperQuiz(Metric):
    metric_id = "c1_paperquiz"
    description = "Mean answer accuracy across 2 VLM answerers (Qwen-VL-72B + GPT-4o) on 5 paper-derived MCQs."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if ctx.png_path is None or not ctx.png_path.exists():
            return self._skip(f"poster.png missing (soffice failed?); answerers need an image")
        if not ctx.paper_path.exists():
            return self._skip(f"paper missing: {ctx.paper_path}")

        try:
            from experiments.judges.paperquiz_generator import get_or_generate_mcqs
            from experiments.judges.paperquiz_answerer import answer_mcqs
        except (ImportError, NotImplementedError):
            return self._skip("M3 deliverable: PaperQuiz generator+answerer not yet wired")

        mcqs = get_or_generate_mcqs(
            paper_path=ctx.paper_path,
            cache_dir=Path("experiments/.cache/paperquiz"),
            gen_cfg=cfg.get("question_generator") or {},
        )
        if not mcqs:
            return self._skip("MCQ generation returned empty (self-consistency failed?)")

        per_answerer: Dict[str, float] = {}
        per_question: List[Dict[str, Any]] = []
        for ans_cfg in cfg.get("answerers") or []:
            answers = answer_mcqs(
                png_path=ctx.png_path,
                mcqs=mcqs,
                answerer_cfg=ans_cfg,
            )
            correct = sum(1 for a in answers if a["correct"])
            per_answerer[ans_cfg["id"]] = correct / max(1, len(answers))
            per_question.extend(answers)

        tsr = sum(per_answerer.values()) / max(1, len(per_answerer))
        return MetricResult(
            metric_id=self.metric_id,
            score=tsr,
            extra={
                "per_answerer": per_answerer,
                "n_questions": len(mcqs),
                "per_question": per_question,
            },
        )
