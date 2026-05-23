"""B2 — Text Readability.

For each text shape in the rendered PPTX:

* font size (pt) read from ``python-pptx``
* min stroke-vs-background ΔL* on the rendered PNG (CIELAB)
* per-text-frame character density (chars / cm²) given the frame's area

Aggregated score::

    R = 0.4·min(1, mean_pt/24) + 0.4·min(1, delta_L/50) + 0.2·(1 - density_norm)

where ``density_norm`` clips the mean character density to a 1500 chars/cm²
ceiling. Higher = more readable.

The three components are also exposed in ``extra`` so the paper can show
which component each baseline wins on.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any, Dict, List, Optional

from experiments.judges.layout_geom import TextSpan, extract_text_spans
from experiments.metrics.base import Metric, MetricContext, MetricResult, MetricRegistry


# Constants used in the composite formula. Pinned here so the paper can
# cite exact values.
_TARGET_FONT_PT = 24.0
_TARGET_DELTA_L = 50.0
_DENSITY_CEILING_CHARS_PER_CM2 = 1500.0
_EMU_PER_INCH = 914_400.0
_INCH_PER_CM = 1 / 2.54


@MetricRegistry.register
class B2Readability(Metric):
    metric_id = "b2_readability"
    description = "Composite readability: mean font size, foreground/background ΔL*, and character density."

    def compute(self, ctx: MetricContext) -> MetricResult:
        cfg = ctx.config or {}
        if not cfg.get("enabled", True):
            return self._skip("disabled in metrics.yaml")
        if not ctx.pptx_path.exists():
            return self._skip(f"pptx missing: {ctx.pptx_path}")

        spans = extract_text_spans(ctx.pptx_path)
        if not spans:
            return self._skip("no text spans extracted from pptx")

        font_pts = [s.font_pt for s in spans if s.font_pt > 0]
        mean_pt = statistics.mean(font_pts) if font_pts else 0.0

        density = _mean_char_density(spans)

        delta_l: Optional[float] = None
        if ctx.png_path is not None and ctx.png_path.exists():
            delta_l = _foreground_background_delta_l(ctx.png_path)

        # Composite — fall back to a neutral 0.5 weight contribution when
        # delta_l is unavailable, so the score remains comparable across
        # baselines that don't render via soffice (Paper2Poster sometimes).
        font_component = min(1.0, mean_pt / _TARGET_FONT_PT)
        delta_component = min(1.0, (delta_l or _TARGET_DELTA_L) / _TARGET_DELTA_L) if delta_l is not None else 0.5
        density_component = 1.0 - min(1.0, density / _DENSITY_CEILING_CHARS_PER_CM2)

        score = 0.4 * font_component + 0.4 * delta_component + 0.2 * density_component

        return MetricResult(
            metric_id=self.metric_id,
            score=round(float(score), 4),
            extra={
                "n_spans": len(spans),
                "mean_font_pt": round(mean_pt, 2),
                "min_font_pt": round(min(font_pts), 2) if font_pts else 0.0,
                "max_font_pt": round(max(font_pts), 2) if font_pts else 0.0,
                "delta_l_star": round(delta_l, 2) if delta_l is not None else None,
                "char_density_per_cm2": round(density, 2),
                "components": {
                    "font_pt": round(font_component, 4),
                    "delta_l": round(delta_component, 4),
                    "density": round(density_component, 4),
                },
            },
            notes=("delta_l unavailable (no png); contribution defaulted to 0.5" if delta_l is None else ""),
        )


def _mean_char_density(spans: List[TextSpan]) -> float:
    """Mean characters per cm² across spans whose container area is known.

    EMU → in → cm conversion: 914400 EMU/in × (1/2.54) in/cm = 360000 EMU/cm
    """
    densities: List[float] = []
    for s in spans:
        if s.container_area_emu <= 0:
            continue
        # EMU² → cm²:  cm = EMU / (914400 × 2.54) — but we already have area, so:
        emu_per_cm = _EMU_PER_INCH / 2.54  # EMU per centimetre
        area_cm2 = s.container_area_emu / (emu_per_cm * emu_per_cm)
        if area_cm2 <= 0:
            continue
        densities.append(len(s.text) / area_cm2)
    if not densities:
        return 0.0
    return statistics.mean(densities)


def _foreground_background_delta_l(png_path: Path, *, sample_stride: int = 4) -> Optional[float]:
    """Approximate minimum text-vs-background ΔL* over the poster.

    Method: bucket pixels by L* (luminance) into bright (>70) and dark
    (<30) populations. The dark population is treated as "text strokes"
    (poster body text on light background) and the bright population as
    "background". ΔL* = median(L*_bright) − median(L*_dark).

    This is a coarse approximation — the per-shape stroke colour is in
    the pptx but tying it back to the rendered PNG is brittle. The
    bucketed approach is robust to dark figures (which add some dark
    pixels but don't dominate) and works on all our templates.

    Returns ``None`` if the image can't be loaded.
    """
    try:
        from PIL import Image
        img = Image.open(str(png_path)).convert("RGB")
    except Exception:
        return None

    # CIELAB L* approximation via the standard sRGB→linear→L* formula.
    # Sampling every Nth pixel to keep this metric snappy.
    w, h = img.size
    px = img.load()
    bright: List[float] = []
    dark: List[float] = []
    for y in range(0, h, sample_stride):
        for x in range(0, w, sample_stride):
            r, g, b = px[x, y][:3]
            l_star = _srgb_to_lstar(r, g, b)
            if l_star > 70:
                bright.append(l_star)
            elif l_star < 30:
                dark.append(l_star)
    if not bright or not dark:
        return None
    return float(statistics.median(bright) - statistics.median(dark))


def _srgb_to_lstar(r: int, g: int, b: int) -> float:
    """sRGB 0-255 → CIELAB L* (0-100). Standard piecewise transform."""
    def _linearise(c: float) -> float:
        c = c / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    rl, gl, bl = _linearise(r), _linearise(g), _linearise(b)
    # Y under D65 illuminant
    y = 0.2126 * rl + 0.7152 * gl + 0.0722 * bl
    # L* = 116 f(Y/Yn) − 16 ; Yn = 1.0
    if y > 0.008856:
        fy = y ** (1.0 / 3.0)
    else:
        fy = 7.787 * y + 16.0 / 116.0
    return 116.0 * fy - 16.0
