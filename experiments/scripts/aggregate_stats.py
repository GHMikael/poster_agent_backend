"""M4 — statistical analysis over ``results/metrics/<cell>.json``.

Output: ``results/aggregate/aggregate.tsv`` (per metric × baseline mean ±
95% bootstrap CI) and ``results/aggregate/pairwise.tsv`` (per metric ×
baseline_pair Wilcoxon p-value + Cohen's d + rank-biserial r).

Multiple-comparison correction:

* **Bonferroni** across the 3 baseline comparisons within a single metric
  (α=0.05/3 ≈ 0.0167).
* **Benjamini-Hochberg FDR (q=0.10)** across the 3 × ~12 = ~36 family
  to control the false-discovery rate.

For higher-is-better metrics (A1, A2, A4, B1, B2, B3, C1, C2,
1 - normalized C3, protocol success rates) the Wilcoxon alternative is
one-sided ``Ours > base``. For lower-is-better (A3, D1, D2, D3,
iterations-to-converge) it is ``Ours < base``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple


# Metrics where higher is better
_HIGHER_IS_BETTER = {
    "a1_information_retention", "a2_figure_text_alignment", "a4_section_coverage",
    "b1_layout_rationality", "b2_readability", "b3_academic_compliance",
    "figure_reuse_rate", "visual_smoke_check",
    "c1_paperquiz", "c2_sus_likert", "c3_time_saving",
    "action_executability", "convergence_rate", "per_iter_visual_gain",
}
_LOWER_IS_BETTER = {
    "a3_hallucination", "d1_latency", "d2_cost", "d3_failure_rate",
    "mean_iters_to_converge",
}


def _load_metrics_dir(metrics_dir: Path) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Returns nested ``{baseline: {arxiv_id: {metric_id: result_dict}}}``."""
    by_baseline: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for path in sorted(metrics_dir.glob("*.json")):
        cell_name = path.stem                            # e.g. "ours_svfp_2405.12345"
        baseline, arxiv_id = _split_cell(cell_name)
        if not baseline:
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        by_baseline.setdefault(baseline, {})[arxiv_id] = data.get("metrics", {})
    return by_baseline


def _split_cell(name: str) -> Tuple[str, str]:
    # We don't know baseline names without the config, but they are
    # underscore-free in our naming convention (ours_svfp, ours_no_svfp,
    # gpt4o_zeroshot, paper2poster, posteragent). Heuristic: longest prefix
    # that we know about. Fallback: split on the last underscore-then-digit.
    for candidate in [
        "ours_svfp", "ours_no_svfp", "ours_freeform", "gpt4o_zeroshot",
        "gpt4o_zeroshot_svfp", "paper2poster", "posteragent",
    ]:
        if name.startswith(candidate + "_"):
            return candidate, name[len(candidate) + 1:]
    return "", name


def _bootstrap_ci(values: Sequence[float], *, n_resamples: int = 1000, alpha: float = 0.05) -> Tuple[float, float, float]:
    """BCa-style mean ± 95% CI using ``scipy.stats.bootstrap`` when available
    or a numpy percentile bootstrap fallback. Returns (mean, low, high)."""
    if not values:
        return (float("nan"), float("nan"), float("nan"))
    try:
        import numpy as np
        from scipy import stats
        arr = np.asarray(values, dtype=float)
        res = stats.bootstrap(
            (arr,), statistic=lambda x, axis=0: np.mean(x, axis=axis),
            confidence_level=1 - alpha, n_resamples=n_resamples, method="BCa",
        )
        return float(arr.mean()), float(res.confidence_interval.low), float(res.confidence_interval.high)
    except Exception:
        # Pure-stdlib fallback.
        import random
        random.seed(42)
        means: List[float] = []
        n = len(values)
        for _ in range(n_resamples):
            sample = [values[random.randrange(n)] for _ in range(n)]
            means.append(sum(sample) / n)
        means.sort()
        lo = means[int(n_resamples * alpha / 2)]
        hi = means[int(n_resamples * (1 - alpha / 2))]
        return (sum(values) / n, lo, hi)


def _wilcoxon(a: Sequence[float], b: Sequence[float], *, higher_is_better: bool) -> Dict[str, Any]:
    """One-sided Wilcoxon signed-rank for paired (a, b) where a corresponds
    to Ours and b to the comparison baseline."""
    if len(a) != len(b) or len(a) < 5:
        return {"p_value": float("nan"), "n_pairs": len(a), "rank_biserial_r": float("nan")}
    try:
        from scipy import stats
        alt = "greater" if higher_is_better else "less"
        res = stats.wilcoxon(a, b, alternative=alt, zero_method="pratt")
        # Rank-biserial r approximation: r = 1 - 2*W_min/sum_ranks
        diffs = [x - y for x, y in zip(a, b)]
        n_pos = sum(1 for d in diffs if d > 0)
        n_neg = sum(1 for d in diffs if d < 0)
        r = (n_pos - n_neg) / max(1, (n_pos + n_neg))
        return {"p_value": float(res.pvalue), "n_pairs": len(a), "rank_biserial_r": float(r)}
    except Exception as exc:
        return {"p_value": float("nan"), "n_pairs": len(a), "error": str(exc)}


def _cohens_d(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or len(a) < 2:
        return float("nan")
    import statistics
    diffs = [x - y for x, y in zip(a, b)]
    mean_diff = statistics.fmean(diffs)
    sd = statistics.pstdev(diffs) or 1e-9
    return mean_diff / sd


def _benjamini_hochberg(p_values: List[float], q: float = 0.10) -> List[bool]:
    """Return a list of bools indicating which p-values survive BH-FDR at q."""
    indexed = sorted(enumerate(p_values), key=lambda t: (t[1] if t[1] == t[1] else 1.0))
    m = len(p_values)
    keep = [False] * m
    for k, (i, p) in enumerate(indexed, start=1):
        if p == p and p <= (k / m) * q:
            keep[i] = True
    return keep


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Aggregate per-cell metrics into TSV reports.")
    p.add_argument("--metrics-dir", type=Path, default=Path("experiments/results/metrics"))
    p.add_argument("--out", type=Path, default=Path("experiments/results/aggregate"))
    p.add_argument("--reference", default="ours_svfp", help="Pairwise comparisons treat this baseline as 'Ours'.")
    p.add_argument("--bonferroni-divisor", type=int, default=3, help="Number of baseline comparisons.")
    p.add_argument("--fdr-q", type=float, default=0.10)
    args = p.parse_args(argv)

    if not args.metrics_dir.exists():
        print(f"[aggregate_stats] missing {args.metrics_dir}; run compute_metrics first", file=sys.stderr)
        return 1

    args.out.mkdir(parents=True, exist_ok=True)
    data = _load_metrics_dir(args.metrics_dir)
    if not data:
        print(f"[aggregate_stats] no cells found under {args.metrics_dir}", file=sys.stderr)
        return 1

    baselines = sorted(data.keys())
    arxiv_ids = sorted({a for rows in data.values() for a in rows.keys()})
    metric_ids = sorted({
        m for rows in data.values() for row in rows.values() for m in row.keys()
    })
    print(f"[aggregate_stats] {len(baselines)} baselines × {len(arxiv_ids)} papers × {len(metric_ids)} metrics")

    # ---- aggregate.tsv ----
    agg_rows: List[Dict[str, Any]] = []
    for m in metric_ids:
        for b in baselines:
            values = []
            for a in arxiv_ids:
                v = (data.get(b, {}).get(a, {}).get(m) or {}).get("score")
                if v is not None:
                    values.append(float(v))
            mean, lo, hi = _bootstrap_ci(values)
            agg_rows.append({"metric": m, "baseline": b, "n": len(values), "mean": mean, "ci_low": lo, "ci_high": hi})

    _write_tsv(args.out / "aggregate.tsv", agg_rows, columns=["metric", "baseline", "n", "mean", "ci_low", "ci_high"])

    # ---- pairwise.tsv (Ours vs each baseline) ----
    pair_rows: List[Dict[str, Any]] = []
    all_pvals: List[float] = []
    if args.reference in baselines:
        ours = data[args.reference]
        other_baselines = [b for b in baselines if b != args.reference]
        for m in metric_ids:
            hib = m in _HIGHER_IS_BETTER
            for b in other_baselines:
                paired_o: List[float] = []
                paired_b: List[float] = []
                for a in arxiv_ids:
                    vo = (ours.get(a, {}).get(m) or {}).get("score")
                    vb = (data.get(b, {}).get(a, {}).get(m) or {}).get("score")
                    if vo is not None and vb is not None:
                        paired_o.append(float(vo))
                        paired_b.append(float(vb))
                w = _wilcoxon(paired_o, paired_b, higher_is_better=hib)
                cd = _cohens_d(paired_o, paired_b)
                p_corr = (w["p_value"] * args.bonferroni_divisor) if w["p_value"] == w["p_value"] else float("nan")
                if p_corr == p_corr:
                    p_corr = min(p_corr, 1.0)
                pair_rows.append({
                    "metric": m,
                    "ours": args.reference,
                    "vs": b,
                    "n_pairs": w["n_pairs"],
                    "p_value": w["p_value"],
                    "p_bonferroni": p_corr,
                    "cohens_d": cd,
                    "rank_biserial_r": w.get("rank_biserial_r"),
                })
                all_pvals.append(w["p_value"] if w["p_value"] == w["p_value"] else 1.0)

        survive = _benjamini_hochberg(all_pvals, q=args.fdr_q)
        for row, s in zip(pair_rows, survive):
            row["bh_fdr_survives"] = bool(s)

    _write_tsv(
        args.out / "pairwise.tsv",
        pair_rows,
        columns=["metric", "ours", "vs", "n_pairs", "p_value", "p_bonferroni", "bh_fdr_survives", "cohens_d", "rank_biserial_r"],
    )

    print(f"[aggregate_stats] wrote {args.out / 'aggregate.tsv'} and {args.out / 'pairwise.tsv'}")
    return 0


def _write_tsv(path: Path, rows: List[Dict[str, Any]], *, columns: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(columns)]
    for r in rows:
        lines.append("\t".join("" if r.get(c) is None else str(r.get(c)) for c in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
