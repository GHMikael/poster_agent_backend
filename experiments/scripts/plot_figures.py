"""M4 — produce paper figures (PDF + PNG) from aggregate TSVs.

Outputs to ``experiments/results/figures/``:

* ``fig01_quality_bars``   — headline quality metrics in [0,1]
* ``fig02_d1_log_bar``     — wall-clock latency on a log axis
* ``fig03_d2_cost_bar``    — USD per poster, grouped by baseline
* ``fig04_d1_d2_pareto``   — cost-vs-latency scatter (Pareto view)
* ``fig05_per_paper_b2``   — per-paper B2 readability strip-plot

Each figure is < 1 page and uses the same paper-friendly style.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


_BASELINE_ORDER = ["ours_no_svfp", "gpt4o_zeroshot", "gpt4o_zeroshot_svfp", "ours_freeform", "ours_svfp"]
_BASELINE_COLORS = {
    "ours_no_svfp": "#888888",
    "gpt4o_zeroshot": "#5b8def",
    "gpt4o_zeroshot_svfp": "#9467bd",
    "ours_freeform": "#ff9f1c",
    "ours_svfp": "#2ca02c",
}
_QUALITY_METRICS = [
    "a1_information_retention",
    "a2_figure_text_alignment",
    "a3_hallucination",
    "b1_layout_rationality",
    "b2_readability",
]


def _read_tsv(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _read_metric_jsons(metrics_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Returns ``{cell_name: {metric_id: result_dict}}``."""
    out: Dict[str, Dict[str, Any]] = {}
    for p in sorted(metrics_dir.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        out[p.stem] = data.get("metrics", {})
    return out


def _split_cell(name: str) -> Tuple[str, str]:
    for candidate in _BASELINE_ORDER + ["paper2poster", "posteragent"]:
        if name.startswith(candidate + "_"):
            return candidate, name[len(candidate) + 1:]
    return "", name


def _style() -> None:
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.dpi": 150,
        "savefig.dpi": 300,
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": True,
        "grid.alpha": 0.3,
        # Prefer a CJK-capable font for Chinese paper ids; falls through to
        # DejaVu Sans on systems without the listed fonts.
        "font.family": ["Heiti TC", "PingFang SC", "Noto Sans CJK SC", "DejaVu Sans"],
        "axes.unicode_minus": False,
    })


def _safe_float(v: Any) -> Optional[float]:
    if v in (None, "", "nan"):
        return None
    try:
        f = float(v)
        return f if not math.isnan(f) else None
    except (TypeError, ValueError):
        return None


def _grouped_bar(
    aggregate_rows: List[Dict[str, str]],
    metrics: List[str],
    *,
    title: str,
    ylabel: str,
    out_path: Path,
    y_log: bool = False,
    y_max: Optional[float] = None,
    fmt: str = ".3f",
) -> Optional[Path]:
    import matplotlib.pyplot as plt
    import numpy as np

    baselines = [b for b in _BASELINE_ORDER if any(
        r["baseline"] == b and r["metric"] in metrics for r in aggregate_rows
    )]
    if not baselines or not metrics:
        return None

    fig, ax = plt.subplots(figsize=(max(5, 1.8 * len(metrics) + 1), 4.6))
    width = 0.8 / max(1, len(baselines))
    for i, b in enumerate(baselines):
        means: List[float] = []
        errs_lo: List[float] = []
        errs_hi: List[float] = []
        for m in metrics:
            row = next((r for r in aggregate_rows if r["metric"] == m and r["baseline"] == b), None)
            mean = _safe_float(row.get("mean") if row else None)
            if mean is None:
                means.append(0.0); errs_lo.append(0.0); errs_hi.append(0.0)
                continue
            lo = _safe_float(row.get("ci_low")) or mean
            hi = _safe_float(row.get("ci_high")) or mean
            means.append(mean)
            errs_lo.append(max(0.0, mean - lo))
            errs_hi.append(max(0.0, hi - mean))
        x = np.arange(len(metrics)) + (i - len(baselines) / 2 + 0.5) * width
        ax.bar(
            x, means, width=width * 0.95, label=b,
            color=_BASELINE_COLORS.get(b, "#777777"),
            yerr=[errs_lo, errs_hi], capsize=2.5, edgecolor="black", linewidth=0.4,
        )
        for xi, m in zip(x, means):
            if m > 0:
                # Put label just above the error bar (which extends to mean+hi),
                # so it doesn't collide with the bar top or the legend.
                ax.text(xi, m + (y_max or 1.0) * 0.01, format(m, fmt),
                        ha="center", va="bottom", fontsize=7.5)

    ax.set_xticks(np.arange(len(metrics)))
    ax.set_xticklabels([m.replace("_", "\n", 1) for m in metrics], rotation=0, fontsize=8.5)
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    if y_log:
        ax.set_yscale("log")
    if y_max is not None:
        # Give a bit of headroom so the value labels don't get clipped.
        ax.set_ylim(0, y_max * 1.08)
    # Place the legend OUTSIDE the axes so it never overlaps bar values.
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.15),
              fontsize=9, ncol=len(baselines), framealpha=0.9)
    fig.tight_layout()
    fig.savefig(out_path)
    fig.savefig(out_path.with_suffix(".png"))
    plt.close(fig)
    return out_path


def fig_quality_bars(rows: List[Dict[str, str]], out_dir: Path) -> Optional[Path]:
    return _grouped_bar(
        rows, _QUALITY_METRICS,
        title="Quality metrics (higher = better)",
        ylabel="score",
        out_path=out_dir / "fig01_quality_bars.pdf",
        y_max=1.05, fmt=".3f",
    )


def fig_d1_latency_log(rows: List[Dict[str, str]], out_dir: Path) -> Optional[Path]:
    return _grouped_bar(
        rows, ["d1_latency"],
        title="End-to-end latency (log scale, lower = better)",
        ylabel="latency (ms)",
        out_path=out_dir / "fig02_d1_latency_log.pdf",
        y_log=True, fmt=".0f",
    )


def fig_d2_cost(rows: List[Dict[str, str]], out_dir: Path) -> Optional[Path]:
    return _grouped_bar(
        rows, ["d2_cost"],
        title="API cost per poster (USD, lower = better)",
        ylabel="USD",
        out_path=out_dir / "fig03_d2_cost.pdf",
        fmt=".4f",
    )


def fig_pareto(
    per_cell: Dict[str, Dict[str, Any]],
    out_dir: Path,
) -> Optional[Path]:
    """Cost (D2) vs latency (D1) scatter, marker per baseline.
    A baseline that lies down-left dominates others."""
    import matplotlib.pyplot as plt

    points: Dict[str, List[Tuple[float, float]]] = {}
    for cell, ms in per_cell.items():
        baseline, _ = _split_cell(cell)
        if not baseline:
            continue
        d1 = _safe_float((ms.get("d1_latency") or {}).get("score"))
        d2 = _safe_float((ms.get("d2_cost") or {}).get("score"))
        if d1 is None or d2 is None:
            continue
        points.setdefault(baseline, []).append((d1, d2))

    if not points:
        return None

    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    for b in _BASELINE_ORDER:
        pts = points.get(b) or []
        if not pts:
            continue
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        ax.scatter(xs, ys, label=b, s=80, color=_BASELINE_COLORS.get(b, "#777777"),
                   edgecolors="black", linewidths=0.5)
    ax.set_xscale("log")
    ax.set_xlabel("latency (ms, log)")
    ax.set_ylabel("cost (USD)")
    ax.set_title("Cost / latency Pareto view\n(down-left dominates)")
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
    fig.tight_layout()
    out = out_dir / "fig04_d1_d2_pareto.pdf"
    fig.savefig(out); fig.savefig(out.with_suffix(".png"))
    plt.close(fig)
    return out


def fig_per_paper_b2(per_cell: Dict[str, Dict[str, Any]], out_dir: Path) -> Optional[Path]:
    """Strip-plot of B2 readability per paper, baselines side-by-side."""
    import matplotlib.pyplot as plt
    import numpy as np

    # Collect {paper_id: {baseline: score}}
    by_paper: Dict[str, Dict[str, float]] = {}
    for cell, ms in per_cell.items():
        baseline, paper = _split_cell(cell)
        if not baseline:
            continue
        score = _safe_float((ms.get("b2_readability") or {}).get("score"))
        if score is None:
            continue
        by_paper.setdefault(paper, {})[baseline] = score
    if not by_paper:
        return None

    papers = sorted(by_paper.keys())
    baselines = [b for b in _BASELINE_ORDER if any(b in by_paper[p] for p in papers)]
    fig, ax = plt.subplots(figsize=(max(6, 1.3 * len(papers) + 1), 4.5))
    width = 0.8 / max(1, len(baselines))
    for i, b in enumerate(baselines):
        scores = [by_paper[p].get(b, float("nan")) for p in papers]
        x = np.arange(len(papers)) + (i - len(baselines) / 2 + 0.5) * width
        ax.bar(x, scores, width=width * 0.95, label=b,
               color=_BASELINE_COLORS.get(b, "#777777"),
               edgecolor="black", linewidth=0.4)
    ax.set_xticks(np.arange(len(papers)))
    # Truncate long paper ids
    ax.set_xticklabels([p[:18] for p in papers], rotation=18, ha="right", fontsize=8)
    ax.set_ylabel("B2 readability score")
    ax.set_title("B2 readability per paper")
    ax.set_ylim(0.6, 0.85)
    ax.legend(loc="upper left", fontsize=9, framealpha=0.9)
    fig.tight_layout()
    out = out_dir / "fig05_per_paper_b2.pdf"
    fig.savefig(out); fig.savefig(out.with_suffix(".png"))
    plt.close(fig)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Render paper figures from aggregate TSVs.")
    p.add_argument("--aggregate-dir", type=Path, default=Path("experiments/results/aggregate"))
    p.add_argument("--metrics-dir", type=Path, default=Path("experiments/results/metrics"))
    p.add_argument("--out", type=Path, default=Path("experiments/results/figures"))
    args = p.parse_args(argv)

    try:
        _style()
    except ImportError:
        print("[plot_figures] matplotlib not installed", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)

    aggregate_rows = _read_tsv(args.aggregate_dir / "aggregate.tsv")
    per_cell = _read_metric_jsons(args.metrics_dir)

    figures: List[Optional[Path]] = []
    figures.append(fig_quality_bars(aggregate_rows, args.out))
    figures.append(fig_d1_latency_log(aggregate_rows, args.out))
    figures.append(fig_d2_cost(aggregate_rows, args.out))
    figures.append(fig_pareto(per_cell, args.out))
    figures.append(fig_per_paper_b2(per_cell, args.out))

    ok = [str(f) for f in figures if f is not None]
    print(f"[plot_figures] wrote {len(ok)} figures:")
    for f in ok:
        print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
