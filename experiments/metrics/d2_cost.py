"""D2 — API cost (USD per poster).

Sums ``cost_usd(model, prompt_tokens, completion_tokens)`` over every
``llm_call`` event in the cell's ``experiment_log.jsonl``.

The headline score is the total USD. The ``extra`` payload exposes the
breakdowns the paper needs:

* ``per_model``  — calls / tokens / USD / cumulative latency by model
* ``per_stage``  — same, by ``stage`` field (e.g. ``vlm_layout_review``)
* ``retries``    — total retries summed across all calls (re-tries inflate
                   token spend; tracked separately so a high cost driven
                   by retry loops is identifiable)
* ``unknown_models`` — list of model identifiers absent from
                       ``pricing.PRICING``. A non-empty list means the
                       headline USD is an undercount.
"""

from __future__ import annotations

from typing import Any, Dict

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry
from experiments.tools.jsonl_io import read_jsonl
from experiments.tools.pricing import cost_usd, is_known


@MetricRegistry.register
class D2Cost(Metric):
    metric_id = "d2_cost"
    description = "Total USD cost from LLM / VLM API calls, with per-model / per-stage breakdown."

    def compute(self, ctx: MetricContext) -> MetricResult:
        events = read_jsonl(ctx.experiment_log_path)
        llm_events = [ev for ev in events if ev.get("kind") == "llm_call"]
        # No log file (e.g. ours_no_svfp's single-shot renderer never invokes
        # the logger code path) OR log present but no llm_call events. Either
        # way the baseline made zero API calls — report 0, not "skipped",
        # so downstream cost/latency plots can include this baseline.
        if not events or not llm_events:
            reason = "no experiment_log.jsonl" if not events else "log present, no llm_call events"
            return MetricResult(
                metric_id=self.metric_id,
                score=0.0,
                extra={"calls": 0, "retries": 0, "per_model": {}, "per_stage": {}, "unknown_models": {}},
                notes=f"{reason}; cost is zero by construction",
            )

        per_model: Dict[str, Dict[str, float]] = {}
        per_stage: Dict[str, Dict[str, float]] = {}
        unknown_models: Dict[str, int] = {}
        total_usd = 0.0
        total_calls = 0
        total_retries = 0

        for ev in events:
            if ev.get("kind") != "llm_call":
                continue
            model = str(ev.get("model") or "unknown")
            stage = str(ev.get("stage") or "unknown")
            p_tok = int(ev.get("prompt_tokens") or 0)
            c_tok = int(ev.get("completion_tokens") or 0)
            lat_ms = float(ev.get("latency_ms") or 0.0)
            retries = int(ev.get("retries") or 0)

            c = cost_usd(model, p_tok, c_tok)
            total_usd += c
            total_calls += 1
            total_retries += retries
            if not is_known(model):
                unknown_models[model] = unknown_models.get(model, 0) + 1

            for bucket, key in ((per_model, model), (per_stage, stage)):
                row = bucket.setdefault(
                    key,
                    {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "usd": 0.0, "latency_ms": 0.0, "retries": 0},
                )
                row["calls"] += 1
                row["prompt_tokens"] += p_tok
                row["completion_tokens"] += c_tok
                row["usd"] += c
                row["latency_ms"] += lat_ms
                row["retries"] += retries

        return MetricResult(
            metric_id=self.metric_id,
            score=round(total_usd, 6),
            extra={
                "calls": total_calls,
                "retries": total_retries,
                "per_model": per_model,
                "per_stage": per_stage,
                "unknown_models": unknown_models,
            },
            notes=(
                f"undercount risk: unpriced models {sorted(unknown_models)}"
                if unknown_models
                else ""
            ),
        )
