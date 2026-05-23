"""D3 — Failure rate (per-cell binary).

A cell fails when any of these are true:
* ``metadata.json::exit_code`` is non-zero
* ``poster.pptx`` is missing or empty (< 1 KB)
* any ``soffice`` event has ``exit_code != 0``

The metric returns ``1.0`` (failed) or ``0.0`` (succeeded). Aggregated
later by :mod:`experiments.scripts.aggregate_stats` into a per-baseline
failure rate.
"""

from __future__ import annotations

import json
from typing import List

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry
from experiments.tools.jsonl_io import read_jsonl


@MetricRegistry.register
class D3FailureRate(Metric):
    metric_id = "d3_failure_rate"
    description = "Binary 1=failed / 0=ok. Aggregated across the matrix to give the rate."

    def compute(self, ctx: MetricContext) -> MetricResult:
        reasons: List[str] = []

        meta_path = ctx.artifact_dir / "metadata.json"
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if int(meta.get("exit_code") or 0) != 0:
                    reasons.append(f"exit_code={meta.get('exit_code')}: {meta.get('error', '')[:120]}")
            except Exception as exc:
                reasons.append(f"metadata.json unreadable: {exc}")

        if not ctx.pptx_path.exists() or ctx.pptx_path.stat().st_size < 1024:
            reasons.append("missing or empty poster.pptx")

        for ev in read_jsonl(ctx.experiment_log_path):
            if ev.get("kind") == "soffice" and int(ev.get("exit_code") or 0) != 0:
                reasons.append(f"soffice exit_code={ev.get('exit_code')}")
                break

        failed = 1.0 if reasons else 0.0
        return MetricResult(
            metric_id=self.metric_id,
            score=failed,
            extra={"reasons": reasons, "failed": bool(reasons)},
        )
