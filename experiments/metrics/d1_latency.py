"""D1 — End-to-end latency.

Reads ``experiment_log.jsonl`` and reports total wall-clock time,
per-stage totals (summed across iterations), per-iteration breakdown,
and the stage with the highest 95th-percentile latency.

Fallback chain when the experiment log is missing:

1. ``experiment_log.jsonl``                 — preferred (fresh rerun)
2. ``metadata.json::total_latency_ms``      — wrapper recorded by ``BaselineRunner``
3. ``run_report.json::started_at/finished_at`` — for cells ingested from a Dify run

Headline ``score`` is total wall-clock latency in **milliseconds**.
"""

from __future__ import annotations

import json
from datetime import datetime
from math import ceil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry
from experiments.tools.jsonl_io import read_jsonl


_WRAPPER_STAGES = {"run_total", "iteration_total"}


@MetricRegistry.register
class D1Latency(Metric):
    metric_id = "d1_latency"
    description = "Total wall-clock latency in milliseconds, with per-stage and per-iteration breakdown."

    def compute(self, ctx: MetricContext) -> MetricResult:
        events = read_jsonl(ctx.experiment_log_path)
        stage_events = [ev for ev in events if ev.get("kind") == "stage"]

        if not stage_events:
            # log is missing OR contains only llm_call events (e.g. the
            # gpt4o_zeroshot baseline emits a single llm_call, no stages).
            # Fall back to wall-clock from metadata.json / run_report.json.
            fallback = _fallback_from_disk(ctx.artifact_dir)
            if fallback is not None:
                total_ms, source = fallback
                return MetricResult(
                    metric_id=self.metric_id,
                    score=total_ms,
                    extra={"source": source, "per_stage_ms": {}, "per_iteration_ms": [], "n_iterations": 0},
                )
            return self._skip("no stage events, metadata.json, or run_report.json")

        per_stage_totals: Dict[str, float] = {}
        per_stage_samples: Dict[str, List[float]] = {}
        per_iteration: Dict[int, Dict[str, Any]] = {}
        run_total: Optional[float] = None

        for ev in stage_events:
            stage = str(ev.get("stage") or "unknown")
            lat = float(ev.get("latency_ms") or 0.0)
            extra = ev.get("extra") or {}
            iteration = extra.get("iteration")

            if stage == "run_total":
                run_total = lat
                continue

            per_stage_totals[stage] = per_stage_totals.get(stage, 0.0) + lat
            per_stage_samples.setdefault(stage, []).append(lat)

            if isinstance(iteration, int):
                row = per_iteration.setdefault(iteration, {"iteration": iteration, "stages_ms": {}, "total_ms": 0.0})
                if stage == "iteration_total":
                    row["total_ms"] = lat
                else:
                    row["stages_ms"][stage] = lat

        if run_total is None:
            # Sub-stages sum gives wall-clock since iterations run sequentially
            # and the inner stages are linear within each iteration.
            run_total = sum(v for k, v in per_stage_totals.items() if k not in _WRAPPER_STAGES)

        p95_stage = _argmax_p95(per_stage_samples)
        ordered_iterations = [per_iteration[k] for k in sorted(per_iteration.keys())]

        return MetricResult(
            metric_id=self.metric_id,
            score=float(run_total),
            extra={
                "source": "experiment_log",
                "per_stage_ms": {k: v for k, v in per_stage_totals.items() if k not in _WRAPPER_STAGES},
                "per_iteration_ms": ordered_iterations,
                "n_iterations": len(per_iteration),
                "p95_stage": p95_stage,
            },
        )


def _argmax_p95(samples: Dict[str, List[float]]) -> Optional[Dict[str, float]]:
    """Return ``{"stage": name, "p95_ms": value}`` for the stage with the
    largest 95th-percentile latency, or ``None`` if no samples."""
    best: Optional[Tuple[str, float]] = None
    for stage, xs in samples.items():
        if stage in _WRAPPER_STAGES or not xs:
            continue
        p95 = _percentile(xs, 95.0)
        if best is None or p95 > best[1]:
            best = (stage, p95)
    if best is None:
        return None
    return {"stage": best[0], "p95_ms": round(best[1], 3)}


def _percentile(xs: List[float], pct: float) -> float:
    """Nearest-rank percentile. With n=1 we just return xs[0]; with n<20
    (typical here — at most 4 iterations) higher-percent quantiles
    degenerate to the max, which is the correct conservative reading."""
    if not xs:
        return 0.0
    s = sorted(xs)
    k = max(1, ceil(pct / 100.0 * len(s)))
    return s[k - 1]


def _fallback_from_disk(artifact_dir: Path) -> Optional[Tuple[float, str]]:
    """Try metadata.json, then run_report.json. Returns (latency_ms, source)
    or None when neither is usable."""
    meta_path = artifact_dir / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            total = float(meta.get("total_latency_ms") or 0.0)
            if total > 0:
                return total, "metadata.json"
        except Exception:
            pass

    report_path = artifact_dir / "run_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            t0 = report.get("started_at")
            t1 = report.get("finished_at")
            if t0 and t1:
                delta_ms = (_parse_iso(t1) - _parse_iso(t0)).total_seconds() * 1000.0
                if delta_ms > 0:
                    return delta_ms, "run_report.json"
        except Exception:
            pass

    return None


def _parse_iso(s: str) -> datetime:
    # ``run_report.json`` records ISO-8601 without timezone (local).
    # ``fromisoformat`` handles both forms in 3.11+.
    return datetime.fromisoformat(s)
