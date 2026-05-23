"""M2 acceptance smoke: run one paper through one baseline and dump metrics.

Usage::

    .venv312/bin/python -m experiments.scripts.run_one_paper \\
        --paper experiments/datasets/papers/2405.12345.pdf \\
        --baseline ours_svfp \\
        --out experiments/results/artifacts/_smoke

Designed for the M2 gate (6/01): produces poster.pptx + poster.png +
experiment_log.jsonl + metadata.json under a single cell directory in
under ~8 minutes wall-clock. The full 12-metric pass over the cell is
delegated to ``compute_metrics.py`` so this script stays focused on
artifact generation.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml  # type: ignore


def _load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _resolve_baseline_class(baseline_id: str, baselines_yaml: Path) -> tuple[type, Dict[str, Any]]:
    rows: List[Dict[str, Any]] = _load_yaml(baselines_yaml).get("baselines", [])
    row = next((r for r in rows if r.get("name") == baseline_id), None)
    if row is None:
        raise SystemExit(f"Unknown baseline '{baseline_id}'. Defined: {[r['name'] for r in rows]}")
    module = importlib.import_module(row["module"])
    cls = getattr(module, row["class"])
    return cls, row


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="M2 smoke: one paper × one baseline.")
    p.add_argument("--paper", required=True, type=Path, help="Path to a PDF.")
    p.add_argument("--baseline", required=True, help="Baseline id from configs/baselines.yaml.")
    p.add_argument(
        "--out",
        type=Path,
        default=Path("experiments/results/artifacts/_smoke"),
        help="Output directory (one subfolder per cell will be created inside).",
    )
    p.add_argument(
        "--baselines-yaml",
        type=Path,
        default=Path("experiments/configs/baselines.yaml"),
    )
    p.add_argument(
        "--timeout",
        type=int,
        default=1800,
        help="Per-cell timeout in seconds.",
    )
    args = p.parse_args(argv)

    if not args.paper.exists():
        print(f"[run_one_paper] paper not found: {args.paper}", file=sys.stderr)
        return 2

    cls, row = _resolve_baseline_class(args.baseline, args.baselines_yaml)
    args.out.mkdir(parents=True, exist_ok=True)

    runner = cls(config=row)
    print(f"[run_one_paper] paper={args.paper.name} baseline={args.baseline}")
    artifact = runner.run(args.paper, args.out, timeout_s=args.timeout)

    print(f"[run_one_paper] done.")
    print(f"  exit_code: {artifact.metadata.exit_code}")
    print(f"  latency_ms: {artifact.metadata.total_latency_ms:.0f}")
    print(f"  pptx: {artifact.pptx_path}")
    print(f"  png:  {artifact.png_path or '(soffice unavailable / failed)'}")
    print(f"  log:  {artifact.raw_log_path or '(no experiment log written)'}")
    if artifact.metadata.error:
        print(f"  error: {artifact.metadata.error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
