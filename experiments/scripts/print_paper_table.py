#!/usr/bin/env python3
"""一键生成最终的论文数据 markdown 表 + Cohen's d 摘要。

跑在所有 metric 计算完成之后,直接读 results/aggregate/aggregate.tsv +
results/aggregate/pairwise.tsv,输出一份贴进论文的表格。
"""
from __future__ import annotations
import csv
import sys
from pathlib import Path


def _fmt(s: str, prec: int = 4) -> str:
    if not s or s == "nan":
        return "—"
    try:
        v = float(s)
        if abs(v) >= 1000:
            return f"{v:,.0f}"
        return f"{v:.{prec}f}"
    except ValueError:
        return s


def main() -> int:
    agg_path = Path("experiments/results/aggregate/aggregate.tsv")
    pair_path = Path("experiments/results/aggregate/pairwise.tsv")
    if not agg_path.exists():
        print(f"missing {agg_path}", file=sys.stderr); return 1

    rows = list(csv.DictReader(agg_path.open(), delimiter="\t"))
    pairs = list(csv.DictReader(pair_path.open(), delimiter="\t")) if pair_path.exists() else []

    metrics = sorted({r["metric"] for r in rows})
    baselines = ["ours_no_svfp", "gpt4o_zeroshot", "ours_svfp"]

    print("# 论文最终数据 (n=5 papers, 3 baselines, 10 metrics)")
    print()
    print("## 主结果表")
    print()
    header = "| metric | " + " | ".join(baselines) + " |"
    print(header)
    print("|" + "---|" * (len(baselines) + 1))
    for m in metrics:
        line = [m]
        for b in baselines:
            r = next((r for r in rows if r["metric"] == m and r["baseline"] == b), None)
            if r is None:
                line.append("—")
            else:
                mean = _fmt(r["mean"])
                lo = _fmt(r.get("ci_low", ""), prec=3)
                hi = _fmt(r.get("ci_high", ""), prec=3)
                if lo == "—" or hi == "—" or mean == lo:
                    line.append(mean)
                else:
                    line.append(f"{mean} [{lo}, {hi}]")
        print("| " + " | ".join(line) + " |")

    print()
    print("## 配对效应量 (ours_svfp vs)")
    print()
    print("| metric | vs | n | Cohen's d | rank-biserial r |")
    print("|---|---|---|---|---|")
    for p in pairs:
        m = p["metric"]; vs = p["vs"]; n = p["n_pairs"]
        d = _fmt(p.get("cohens_d", ""))
        rb = _fmt(p.get("rank_biserial_r", ""))
        print(f"| {m} | {vs} | {n} | {d} | {rb} |")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
