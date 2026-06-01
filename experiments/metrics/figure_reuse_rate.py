"""Figure reuse rate for the poster artifact.

The metric measures whether a generated poster reuses valid original figures
from the planner/PDF asset bundle. It is intentionally structural: it does not
claim semantic alignment, which remains the role of A2 / figure audit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Set, Tuple

from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


_UNSAFE_AUDIT_STATUSES = {"broken", "missing_image_file", "missing_figure_entry"}


def _has_renderable_source(fig: Dict[str, Any]) -> bool:
    src = str(fig.get("image_source") or "").strip()
    if src:
        return Path(src).exists()
    return bool(str(fig.get("image_url") or fig.get("thumbnail_url") or "").strip())


def _is_valid_figure(fig: Dict[str, Any]) -> bool:
    if str(fig.get("audit_status") or "").strip() in _UNSAFE_AUDIT_STATUSES:
        return False
    return _has_renderable_source(fig)


def _figure_sources(fig: Dict[str, Any]) -> Set[str]:
    return {
        str(fig.get(key) or "").strip()
        for key in ("image_source", "image_url", "thumbnail_url")
        if str(fig.get(key) or "").strip()
    }


def _referenced_figures(panels_json: Dict[str, Any], source_to_id: Dict[str, str]) -> Tuple[Set[str], Set[str]]:
    refs: Set[str] = set()
    unresolved_sources: Set[str] = set()
    for panel in panels_json.get("panels") or []:
        if not isinstance(panel, dict):
            continue
        fid = str(panel.get("figure_id") or "").strip()
        if fid:
            refs.add(fid)
        direct = str(panel.get("figure") or "").strip()
        if direct:
            if direct in source_to_id:
                refs.add(source_to_id[direct])
            else:
                unresolved_sources.add(direct)
    return refs, unresolved_sources


@MetricRegistry.register
class FigureReuseRate(Metric):
    metric_id = "figure_reuse_rate"
    description = "Fraction of valid source figures reused by poster panels."

    def compute(self, ctx: MetricContext) -> MetricResult:
        if not ctx.config.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if not ctx.panels_json:
            return self._skip("panels.json missing")

        figures = ctx.panels_json.get("figures") or {}
        if not isinstance(figures, dict):
            return self._skip("figures is not a dict")

        valid_ids: Set[str] = set()
        source_to_id: Dict[str, str] = {}
        for fid, fig in figures.items():
            if not isinstance(fig, dict):
                continue
            fid_str = str(fid)
            if _is_valid_figure(fig):
                valid_ids.add(fid_str)
            for source in _figure_sources(fig):
                source_to_id[source] = fid_str
        if not valid_ids:
            return self._skip("no valid source figures")

        refs, unresolved_sources = _referenced_figures(ctx.panels_json, source_to_id)
        reused_valid = sorted(refs & valid_ids)
        missing_or_invalid = sorted(refs - valid_ids)
        unused_valid = sorted(valid_ids - refs)
        score = len(reused_valid) / max(len(valid_ids), 1)

        return MetricResult(
            metric_id=self.metric_id,
            score=score,
            extra={
                "n_valid_figures": len(valid_ids),
                "n_reused_valid_figures": len(reused_valid),
                "n_referenced_figures": len(refs),
                "reused_valid_figure_ids": reused_valid,
                "unused_valid_figure_ids": unused_valid,
                "missing_or_invalid_references": missing_or_invalid,
                "unresolved_direct_figure_sources": sorted(unresolved_sources),
            },
            notes="Structural reuse only; semantic caption/figure alignment is measured separately by A2/audit.",
        )
