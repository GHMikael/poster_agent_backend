"""C2 — SUS (System Usability Scale) + 5-item domain Likert.

Pilot scale: n=10-15. Loads a single CSV produced by a Google Form,
filters by baseline + paper, returns SUS (0-100, Brooke 1996) and
Likert mean (1-7) per cell.

CSV columns expected:

    participant_id, baseline, arxiv_id, sus_1, …, sus_10,
    likert_clarity, likert_completeness, likert_aesthetics,
    likert_trust, likert_usefulness

SUS scoring rules:
    odd items:  score - 1
    even items: 5 - score
    sum × 2.5  ⇒  [0, 100]
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


_LIKERT_KEYS = ["likert_clarity", "likert_completeness", "likert_aesthetics", "likert_trust", "likert_usefulness"]


@MetricRegistry.register
class C2SUSLikert(Metric):
    metric_id = "c2_sus_likert"
    description = "Per-cell SUS (0-100) and 5-item domain Likert mean from a pilot user study."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        csv_path = Path(cfg.get("source_csv") or "experiments/results/user_study/sus_likert.csv")
        if not csv_path.exists():
            return self._skip(f"user study not yet collected: {csv_path}")

        baseline = ctx.artifact_dir.name.split("_")[0]
        arxiv_id = ctx.paper_meta.get("arxiv_id") or ctx.artifact_dir.name.split("_", 1)[1]
        sus_scores: List[float] = []
        likert_means: List[float] = []
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("baseline") != baseline or r.get("arxiv_id") != arxiv_id:
                    continue
                try:
                    sus = _score_sus([int(r[f"sus_{i}"]) for i in range(1, 11)])
                    likert = sum(float(r[k]) for k in _LIKERT_KEYS) / len(_LIKERT_KEYS)
                except (KeyError, ValueError):
                    continue
                sus_scores.append(sus)
                likert_means.append(likert)

        if not sus_scores:
            return self._skip(f"no responses yet for {baseline} / {arxiv_id}")

        return MetricResult(
            metric_id=self.metric_id,
            score=sum(sus_scores) / len(sus_scores),       # SUS as headline
            extra={
                "n_participants": len(sus_scores),
                "sus_mean": sum(sus_scores) / len(sus_scores),
                "likert_mean": sum(likert_means) / len(likert_means),
                "sus_individual": sus_scores,
                "likert_individual": likert_means,
            },
        )


def _score_sus(items: List[int]) -> float:
    """Standard SUS scoring (Brooke 1996). Items 1-5 valued, returns 0-100."""
    assert len(items) == 10, "SUS requires exactly 10 items"
    total = 0.0
    for i, v in enumerate(items, start=1):
        if i % 2 == 1:        # odd items: subtract 1
            total += (v - 1)
        else:                  # even items: 5 - v
            total += (5 - v)
    return total * 2.5
