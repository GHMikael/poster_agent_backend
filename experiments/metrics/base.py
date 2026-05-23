"""Metric abstract base class and the per-cell output schema.

Every concrete metric (A1, A2, …, D3) inherits :class:`Metric` and
implements :meth:`compute`. The orchestrator
``experiments/scripts/compute_metrics.py`` walks
``results/artifacts/<baseline>_<arxiv_id>/`` cells, instantiates each
enabled metric, and writes a single
``results/metrics/<baseline>_<arxiv_id>.json`` with all values keyed by
``metric_id``.

A metric is a *pure function of an artifact*. It MUST NOT mutate the
artifact and SHOULD be idempotent (so reruns are cheap).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


__all__ = ["Metric", "MetricResult", "MetricContext", "MetricRegistry"]


@dataclass
class MetricResult:
    """Standardised output for ``compute_metrics`` aggregation."""

    metric_id: str
    score: Optional[float]                   # primary scalar, None if not applicable / skipped
    extra: Dict[str, Any] = field(default_factory=dict)
    notes: str = ""
    skipped: bool = False
    skip_reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MetricContext:
    """Everything a metric may want, packaged so callers don't re-derive
    paths inside each metric. ``paper_meta`` carries the manifest row
    (title, gold_figures, gold_claims_path, …)."""

    artifact_dir: Path                        # results/artifacts/<baseline>_<arxiv_id>/
    pptx_path: Path                           # poster.pptx
    png_path: Optional[Path]                  # poster.png (None if soffice failed)
    panels_json: Optional[Dict[str, Any]]     # PosterTask snapshot (None for opaque baselines)
    experiment_log_path: Optional[Path]       # experiment_log.jsonl
    paper_path: Path                          # original PDF
    paper_meta: Dict[str, Any]                # from configs/papers_30.json row
    config: Dict[str, Any]                    # per-metric config slice from configs/metrics.yaml


class Metric(ABC):
    """All metrics carry a stable identifier and a one-line description."""

    metric_id: str = "metric_unknown"
    description: str = ""

    @abstractmethod
    def compute(self, ctx: MetricContext) -> MetricResult: ...

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _skip(self, reason: str) -> MetricResult:
        return MetricResult(metric_id=self.metric_id, score=None, skipped=True, skip_reason=reason)


# ---------------------------------------------------------------------------
# Registry — populated by metrics/__init__.py lazily (avoids circular imports)
# ---------------------------------------------------------------------------


class MetricRegistry:
    _registered: Dict[str, type] = {}

    @classmethod
    def register(cls, metric_cls: type) -> type:
        cls._registered[metric_cls.metric_id] = metric_cls
        return metric_cls

    @classmethod
    def all(cls) -> Dict[str, type]:
        return dict(cls._registered)

    @classmethod
    def get(cls, metric_id: str) -> Optional[type]:
        return cls._registered.get(metric_id)
