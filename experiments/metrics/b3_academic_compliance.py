"""B3 — Academic Compliance (expert-rated, no auto-compute).

Loads ``experiments/results/expert_ratings.csv`` produced by 2-3 expert
raters scoring each (baseline × paper) cell on a 5-item Likert rubric:

* title accuracy
* author completeness
* data fidelity
* citation compliance
* section order

Mean per cell is the metric score; aggregate code in
``experiments/scripts/aggregate_stats.py`` also reports Krippendorff's α
across raters.

For the pilot (n ≤ 15 raters, 30-60 sampled cells), only descriptives
are reported in the paper.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


@MetricRegistry.register
class B3AcademicCompliance(Metric):
    metric_id = "b3_academic_compliance"
    description = "Mean of 5 Likert items (1-5) from 2-3 expert raters per cell."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        csv_path = Path(cfg.get("source_csv") or "experiments/results/expert_ratings.csv")
        if not csv_path.exists():
            return self._skip(f"expert ratings not yet collected at {csv_path}")

        # Find rows matching this cell.
        baseline = ctx.artifact_dir.name.split("_")[0]  # rough; full name parsed in M3
        arxiv_id = ctx.paper_meta.get("arxiv_id") or ctx.artifact_dir.name.split("_", 1)[1]
        rows: List[Dict[str, str]] = []
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("baseline") == baseline and r.get("arxiv_id") == arxiv_id:
                    rows.append(r)
        if not rows:
            return self._skip(f"no expert ratings for {baseline} / {arxiv_id}")

        items = cfg.get("rubric_items") or [
            "title_accuracy", "author_completeness", "data_fidelity",
            "citation_compliance", "section_order",
        ]
        per_rater_means: List[float] = []
        for r in rows:
            vals = [float(r[k]) for k in items if r.get(k)]
            if vals:
                per_rater_means.append(sum(vals) / len(vals))
        if not per_rater_means:
            return self._skip("rows present but no rubric values parsed")

        mean_score = sum(per_rater_means) / len(per_rater_means)
        return MetricResult(
            metric_id=self.metric_id,
            score=mean_score,
            extra={"n_raters": len(per_rater_means), "per_rater_means": per_rater_means},
        )
