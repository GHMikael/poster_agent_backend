"""B1 — Layout Rationality (auto-geometric + VLM-judge dual report).

Two scalars per poster (the paper reports both):

* ``lr_auto`` — deterministic, computed from panel boxes in the rendered
  PPTX (python-pptx). Weighted sum of 5 components:

    LR_auto = 0.25·(1-overlap) + 0.25·(1-whitespace_outlier)
            + 0.20·grid_alignment + 0.15·(1-aspect_extremes)
            + 0.15·reading_order

* ``lr_vlm`` — GPT-4o rates the rendered PNG on 5 criteria
  (balance / alignment / hierarchy / whitespace / flow), each 1-5,
  mean is normalised to [0,1].

The headline B1 number is ``(lr_auto + lr_vlm) / 2`` only when both are
available; otherwise the present one. Spearman ρ between ``lr_auto`` and
``lr_vlm`` across the full 120-cell matrix is reported in the paper as a
validation that ``lr_auto`` can be used in low-resource settings without
the VLM.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from experiments.judges.layout_geom import (
    PanelBox,
    extract_panel_boxes,
    mad_normalised,
    slide_dimensions_emu,
)
from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


# Grid alignment tolerance: a panel edge is "on grid" if within this many
# EMU of any n-column grid line. 0.1 inch ≈ 91440 EMU. Calibrated so the
# dashboard / classic templates score ~0.9 (their authors deliberately put
# the boxes on a 12-col grid), and a random scatter scores ~0.3.
_GRID_TOLERANCE_EMU = 91_440


@MetricRegistry.register
class B1LayoutRationality(Metric):
    metric_id = "b1_layout_rationality"
    description = "Layout quality: 5-component geometric score + GPT-4o 5-criteria rubric."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if not ctx.pptx_path.exists():
            return self._skip(f"pptx missing: {ctx.pptx_path}")

        lr_auto, components = self._auto_geometric(ctx.pptx_path, cfg.get("auto_geometric") or {})

        lr_vlm: Optional[float] = None
        if ctx.png_path is not None:
            try:
                from experiments.judges.vlm_layout_judge import layout_score_5_criteria
                lr_vlm = layout_score_5_criteria(
                    png_path=ctx.png_path,
                    model=cfg.get("vlm_judge", {}).get("model", "gpt-4o-2024-11-20"),
                    criteria=cfg.get("vlm_judge", {}).get("criteria") or
                              ["balance", "alignment", "hierarchy", "whitespace", "flow"],
                )
            except (ImportError, NotImplementedError):
                lr_vlm = None

        headline = lr_auto if lr_vlm is None else (lr_auto + lr_vlm) / 2.0
        return MetricResult(
            metric_id=self.metric_id,
            score=headline,
            extra={
                "lr_auto": lr_auto,
                "lr_vlm": lr_vlm,
                "components": components,
                "n_panels": components.get("n_panels", 0),
            },
        )

    # ------------------------------------------------------------------
    # Auto-geometric implementation
    # ------------------------------------------------------------------

    def _auto_geometric(self, pptx_path: Path, geom_cfg: Dict[str, Any]) -> Tuple[float, Dict[str, float]]:
        """Compute the 5-component geometric score from python-pptx shapes.

        Components:
        * overlap_ratio        — mean pairwise IoU of panel boxes
        * whitespace_outlier   — MAD/median of panel areas (size unevenness)
        * grid_alignment       — fraction of panel edges within tolerance of a 12-col grid line
        * aspect_extremes      — fraction of panels with w/h > 4 or h/w > 4
        * reading_order_score  — monotonicity of (y, x) tuples in reading order

        Returns ``(lr_auto, components_dict)``. With <2 panels the
        function falls back to neutral 0.5 to avoid distorted scores.
        """
        boxes = extract_panel_boxes(pptx_path)
        slide_w, slide_h = slide_dimensions_emu(pptx_path)
        n = len(boxes)

        if n < 2:
            return 0.5, {
                "n_panels": n,
                "overlap_ratio": 0.0,
                "whitespace_outlier": 0.0,
                "grid_alignment": 0.0,
                "aspect_extremes": 0.0,
                "reading_order_score": 0.0,
                "fallback": "fewer than 2 panel boxes",
            }

        overlap = _mean_pairwise_iou(boxes)
        whitespace_outlier = min(1.0, mad_normalised([float(b.area) for b in boxes]))
        grid_alignment = _grid_alignment_score(boxes, slide_w, slide_h, n_cols=12, tolerance_emu=_GRID_TOLERANCE_EMU)
        aspect_extreme = _aspect_extremes_ratio(boxes, ratio_max=4.0)
        reading_order = _reading_order_monotonicity(boxes)

        components = {
            "n_panels": n,
            "overlap_ratio": round(overlap, 4),
            "whitespace_outlier": round(whitespace_outlier, 4),
            "grid_alignment": round(grid_alignment, 4),
            "aspect_extremes": round(aspect_extreme, 4),
            "reading_order_score": round(reading_order, 4),
        }
        w = geom_cfg.get("weights") or {
            "overlap": 0.25, "whitespace": 0.25, "grid_alignment": 0.20,
            "aspect_extremes": 0.15, "reading_order": 0.15,
        }
        lr = (
            w["overlap"] * (1 - overlap)
            + w["whitespace"] * (1 - whitespace_outlier)
            + w["grid_alignment"] * grid_alignment
            + w["aspect_extremes"] * (1 - aspect_extreme)
            + w["reading_order"] * reading_order
        )
        return float(round(lr, 4)), components


# ---------------------------------------------------------------------------
# Component helpers
# ---------------------------------------------------------------------------


def _iou(a: PanelBox, b: PanelBox) -> float:
    ix1, iy1 = max(a.x, b.x), max(a.y, b.y)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter_w, inter_h = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = inter_w * inter_h
    if inter == 0:
        return 0.0
    union = a.area + b.area - inter
    return inter / union if union > 0 else 0.0


def _mean_pairwise_iou(boxes: List[PanelBox]) -> float:
    n = len(boxes)
    if n < 2:
        return 0.0
    total = 0.0
    pairs = 0
    for i in range(n):
        for j in range(i + 1, n):
            total += _iou(boxes[i], boxes[j])
            pairs += 1
    return total / pairs if pairs else 0.0


def _grid_alignment_score(
    boxes: List[PanelBox],
    slide_w: int,
    slide_h: int,
    *,
    n_cols: int = 12,
    n_rows: int = 8,
    tolerance_emu: int = _GRID_TOLERANCE_EMU,
) -> float:
    """Fraction of panel edges that snap to a regular n_cols × n_rows grid.

    Four edges per panel (left/right/top/bottom). Total = 4n edges.
    """
    if slide_w <= 0 or slide_h <= 0 or not boxes:
        return 0.0
    col_lines = [round(slide_w * k / n_cols) for k in range(n_cols + 1)]
    row_lines = [round(slide_h * k / n_rows) for k in range(n_rows + 1)]
    hits = 0
    total = 0
    for b in boxes:
        for edge_x in (b.x, b.x2):
            total += 1
            if any(abs(edge_x - g) <= tolerance_emu for g in col_lines):
                hits += 1
        for edge_y in (b.y, b.y2):
            total += 1
            if any(abs(edge_y - g) <= tolerance_emu for g in row_lines):
                hits += 1
    return hits / total if total else 0.0


def _aspect_extremes_ratio(boxes: List[PanelBox], *, ratio_max: float = 4.0) -> float:
    if not boxes:
        return 0.0
    extreme = 0
    for b in boxes:
        if b.h == 0 or b.w == 0:
            extreme += 1
            continue
        ratio = max(b.w / b.h, b.h / b.w)
        if ratio > ratio_max:
            extreme += 1
    return extreme / len(boxes)


def _reading_order_monotonicity(boxes: List[PanelBox]) -> float:
    """Reading order = sort by (y_bucket, x). A poster is "well-ordered"
    when the unsorted iteration order matches that sorted order.

    Returns the Spearman-like fraction of inversions removed; perfectly
    ordered = 1.0, fully reverse = 0.0.
    """
    n = len(boxes)
    if n < 2:
        return 1.0
    # Bucket y into rows so jitter within a row doesn't penalise
    y_bucket_size = max(1, int(round(0.5 * 914_400)))  # 0.5 inch
    indexed = [(i, (b.y // y_bucket_size, b.x)) for i, b in enumerate(boxes)]
    sorted_idx = [pair[0] for pair in sorted(indexed, key=lambda t: t[1])]
    # Spearman footrule: sum of |actual_position - sorted_position|, normalised
    pos_actual = list(range(n))
    pos_sorted = {idx: pos for pos, idx in enumerate(sorted_idx)}
    diffs = sum(abs(pos_actual[i] - pos_sorted[i]) for i in range(n))
    # Max possible footrule for n items is ~n^2/2
    max_diff = (n * n) / 2.0
    return max(0.0, 1.0 - diffs / max_diff) if max_diff > 0 else 1.0
