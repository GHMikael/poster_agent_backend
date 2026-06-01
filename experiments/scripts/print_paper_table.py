#!/usr/bin/env python3
"""一键生成论文主表 (markdown) — B3 重设版。

读 results/aggregate/{aggregate,pairwise}.tsv,按 4 个 cluster 组织 headline
主表(内容保真 / 视觉质量 / 协议 SVFP / 工程),并把**无区分度的天花板指标**
(a4_section_coverage 模板强制=1.0、c1_paperquiz 5-MCQ 饱和)与**尚无数据**的
user-study 指标 (c2/c3) 降级到 Appendix——不再污染 headline 表。

Protocol cluster 的指标 (action_executability / convergence_rate / ...) 由 E1
三臂对照产出;在它们出现前脚本自动跳过该 cluster,无需改动。

跑在所有 metric 计算 + aggregate_stats.py 之后。
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path

# Column order for baselines (known ones first, unknown appended).
_BASELINE_ORDER = [
    "ours_no_svfp", "gpt4o_zeroshot", "gpt4o_zeroshot_svfp",
    "paper2poster", "posteragent", "ours_freeform", "ours_svfp",
]

# Headline clusters (v2 §三). Metrics absent from aggregate.tsv are skipped.
_CLUSTERS = [
    ("Content fidelity", ["a1_information_retention", "a2_figure_text_alignment", "a3_hallucination"]),
    ("Visual quality", ["b1_layout_rationality", "b2_readability", "figure_reuse_rate", "visual_smoke_check", "b3_academic_compliance"]),
    ("Protocol (SVFP)", ["action_executability", "convergence_rate", "mean_iters_to_converge", "per_iter_visual_gain"]),
    ("Engineering", ["d1_latency", "d2_cost", "d3_failure_rate"]),
]

# Demoted to appendix with the reason shown to the reader.
_APPENDIX_NOTE = {
    "a4_section_coverage": "ceiling — template guarantees all six sections (=1.0 for every method); no discriminative power",
    "c1_paperquiz": "ceiling — 5-MCQ saturates near 1.0; appendix until redesigned (≥10 harder, figure/number-grounded Qs)",
    "c2_sus_likert": "user study — pending data",
    "c3_time_saving": "user study — pending data",
}


def _fmt(s, prec: int = 4) -> str:
    if s is None or s == "" or s == "nan":
        return "—"
    try:
        v = float(s)
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.{prec}f}"
    except (ValueError, TypeError):
        return str(s)


def _cell(rows, metric: str, baseline: str) -> str:
    r = next((r for r in rows if r["metric"] == metric and r["baseline"] == baseline), None)
    if r is None:
        return "—"
    mean = _fmt(r.get("mean"))
    lo, hi = _fmt(r.get("ci_low"), 3), _fmt(r.get("ci_high"), 3)
    if lo == "—" or hi == "—" or mean == lo:
        return mean
    return f"{mean} [{lo}, {hi}]"


def _table(rows, metrics, baselines) -> str:
    out = ["| metric | " + " | ".join(baselines) + " |",
           "|" + "---|" * (len(baselines) + 1)]
    for m in metrics:
        out.append("| " + m + " | " + " | ".join(_cell(rows, m, b) for b in baselines) + " |")
    return "\n".join(out)


def main() -> int:
    agg_path = Path("experiments/results/aggregate/aggregate.tsv")
    pair_path = Path("experiments/results/aggregate/pairwise.tsv")
    if not agg_path.exists():
        print(f"missing {agg_path}; run aggregate_stats.py first", file=sys.stderr)
        return 1

    rows = list(csv.DictReader(agg_path.open(encoding="utf-8"), delimiter="\t"))
    pairs = list(csv.DictReader(pair_path.open(encoding="utf-8"), delimiter="\t")) if pair_path.exists() else []

    present_metrics = {r["metric"] for r in rows}
    present_baselines = {r["baseline"] for r in rows}
    baselines = ([b for b in _BASELINE_ORDER if b in present_baselines]
                 + sorted(present_baselines - set(_BASELINE_ORDER)))
    n_papers = max((int(r["n"]) for r in rows if str(r.get("n", "")).isdigit()), default=0)

    print(f"# 论文主表 (n={n_papers} papers, {len(baselines)} baselines)")
    print()
    print("> Headline 表按 4 cluster 组织;天花板 / 待补指标见文末 Appendix。")
    print()

    classified = set()
    for title, metric_list in _CLUSTERS:
        ms = [m for m in metric_list if m in present_metrics]
        if not ms:
            continue
        classified.update(ms)
        print(f"## {title}\n")
        print(_table(rows, ms, baselines))
        print()

    other = sorted(present_metrics - classified - set(_APPENDIX_NOTE))
    if other:
        print("## Other (unclassified)\n")
        print(_table(rows, other, baselines))
        print()

    appendix = [m for m in _APPENDIX_NOTE if m in present_metrics]
    if appendix:
        print("## Appendix — excluded from headline table\n")
        print(_table(rows, appendix, baselines))
        print()
        for m in appendix:
            print(f"- `{m}`: {_APPENDIX_NOTE[m]}")
        print()

    if pairs:
        print("## 配对效应量 (ours_svfp vs)\n")
        print("| metric | vs | n | Cohen's d | rank-biserial r | BH-FDR survives |")
        print("|---|---|---|---|---|---|")
        for p in pairs:
            print(f"| {p['metric']} | {p['vs']} | {p['n_pairs']} | "
                  f"{_fmt(p.get('cohens_d'))} | {_fmt(p.get('rank_biserial_r'))} | "
                  f"{p.get('bh_fdr_survives', '')} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
