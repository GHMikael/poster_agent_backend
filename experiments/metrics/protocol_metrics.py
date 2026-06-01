"""Protocol-level metrics for the E1 SVFP vs free-form comparison."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


def _metadata_config(ctx: MetricContext) -> Dict[str, Any]:
    path = ctx.artifact_dir / "metadata.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    cfg = data.get("config") or {}
    return cfg if isinstance(cfg, dict) else {}


def _num(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class _ProtocolMetric(Metric):
    config_key = ""
    description = "Protocol metric derived from baseline metadata."

    def compute(self, ctx: MetricContext) -> MetricResult:
        if not (ctx.config or {}).get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        cfg = _metadata_config(ctx)
        feedback_mode = cfg.get("feedback_mode", "")
        if feedback_mode == "none":
            return self._skip("no feedback arm")
        value = self._value_from_config(cfg)
        if value is None:
            return self._skip(f"missing {self.config_key} in metadata.config")
        return MetricResult(
            metric_id=self.metric_id,
            score=value,
            extra={
                "feedback_mode": feedback_mode,
                "convergence_reason": cfg.get("convergence_reason", ""),
                "n_iterations": cfg.get("n_iterations"),
                "n_attempts": cfg.get("n_attempts"),
                "n_executed": cfg.get("n_executed"),
            },
        )

    def _value_from_config(self, cfg: Dict[str, Any]) -> Optional[float]:
        return _num(cfg.get(self.config_key))


@MetricRegistry.register
class ActionExecutability(_ProtocolMetric):
    metric_id = "action_executability"
    config_key = "action_executability"
    description = "Fraction of VLM feedback items that were executable by the downstream applier."


@MetricRegistry.register
class ConvergenceRate(_ProtocolMetric):
    metric_id = "convergence_rate"
    description = "Whether the feedback loop reached a convergence condition within its iteration budget."

    def _value_from_config(self, cfg: Dict[str, Any]) -> Optional[float]:
        converged = cfg.get("converged")
        if converged is None:
            return None
        reason = str(cfg.get("convergence_reason") or "")
        if reason == "max_iterations_reached" or reason.startswith("stuck:"):
            return 0.0
        return 1.0 if bool(converged) else 0.0


@MetricRegistry.register
class MeanItersToConverge(_ProtocolMetric):
    metric_id = "mean_iters_to_converge"
    config_key = "n_iterations"
    description = "Number of feedback iterations used by the loop; lower is better when quality is comparable."


@MetricRegistry.register
class PerIterVisualGain(_ProtocolMetric):
    metric_id = "per_iter_visual_gain"
    config_key = "per_iter_visual_gain"
    description = "Mean positive per-iteration visual score delta observed inside the feedback loop."
