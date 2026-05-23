"""C3 — Time Saving Ratio.

Within-subject (counterbalanced order) pilot:

* Condition A: participant uses *our* poster (Ours-SVFP) to answer 5
  comprehension questions about the paper.
* Condition B: participant uses the paper PDF only.

Per condition, we time to completion. Time-saving ratio:

    TSR = (t_paper - t_poster) / t_paper

Reported as median + IQR per baseline; Wilcoxon signed-rank for the
significance of (t_paper - t_poster) > 0 (one-sided).

CSV columns expected:

    participant_id, arxiv_id, baseline, condition, seconds, correct_count
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


@MetricRegistry.register
class C3TimeSaving(Metric):
    metric_id = "c3_time_saving"
    description = "Median time-saving ratio vs. paper-only condition (pilot scale)."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        csv_path = Path(cfg.get("source_csv") or "experiments/results/user_study/timing.csv")
        if not csv_path.exists():
            return self._skip(f"user study timing not collected: {csv_path}")

        baseline = ctx.artifact_dir.name.split("_")[0]
        arxiv_id = ctx.paper_meta.get("arxiv_id") or ctx.artifact_dir.name.split("_", 1)[1]

        # (participant_id) -> (t_paper, t_poster)
        rows: Dict[str, Dict[str, float]] = {}
        with csv_path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for r in reader:
                if r.get("arxiv_id") != arxiv_id:
                    continue
                if r.get("baseline") not in {baseline, "paper_only"}:
                    continue
                try:
                    secs = float(r["seconds"])
                except (KeyError, ValueError):
                    continue
                pid = r["participant_id"]
                rows.setdefault(pid, {})[r.get("condition") or r.get("baseline")] = secs

        ratios: List[float] = []
        for pid, row in rows.items():
            t_paper = row.get("paper_only")
            t_poster = row.get(baseline) or row.get("poster")
            if t_paper and t_poster and t_paper > 0:
                ratios.append((t_paper - t_poster) / t_paper)

        if not ratios:
            return self._skip(f"no paired timing rows yet for {baseline}/{arxiv_id}")

        ratios_sorted = sorted(ratios)
        n = len(ratios_sorted)
        median = ratios_sorted[n // 2] if n % 2 else (ratios_sorted[n // 2 - 1] + ratios_sorted[n // 2]) / 2
        return MetricResult(
            metric_id=self.metric_id,
            score=median,
            extra={"n_pairs": n, "per_pair_ratio": ratios},
        )
