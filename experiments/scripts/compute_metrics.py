"""Post-hoc metric computation over one or more artifact cells.

Usage (single cell)::

    .venv312/bin/python -m experiments.scripts.compute_metrics \\
        --artifact experiments/results/artifacts/_smoke/ours_svfp_2405.12345 \\
        --metrics d1_latency,d2_cost,d3_failure_rate

Usage (all cells)::

    .venv312/bin/python -m experiments.scripts.compute_metrics --all --metrics all

Outputs one JSON file per cell under ``experiments/results/metrics/``.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import yaml  # type: ignore

from experiments.metrics.base import Metric, MetricContext, MetricRegistry, MetricResult


# Ensures all built-in metrics register themselves at import time.
def _import_all_metrics() -> None:
    for mod in [
        # Content
        "experiments.metrics.a1_information_retention",
        "experiments.metrics.a2_figure_text_alignment",
        "experiments.metrics.a3_hallucination",
        "experiments.metrics.a4_section_coverage",
        # Visual
        "experiments.metrics.b1_layout_rationality",
        "experiments.metrics.b2_readability",
        "experiments.metrics.b3_academic_compliance",
        # User
        "experiments.metrics.c1_paperquiz",
        "experiments.metrics.c2_sus_likert",
        "experiments.metrics.c3_time_saving",
        # Engineering
        "experiments.metrics.d1_latency",
        "experiments.metrics.d2_cost",
        "experiments.metrics.d3_failure_rate",
    ]:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            print(f"[compute_metrics] skipping {mod}: {exc}", file=sys.stderr)


def _load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _build_context(cell_dir: Path, metric_config: Dict[str, Any], papers_manifest: Dict[str, Any]) -> Optional[MetricContext]:
    meta_path = cell_dir / "metadata.json"
    if not meta_path.exists():
        return None
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    arxiv_id = meta.get("arxiv_id", "")
    panels_path = cell_dir / "panels.json"
    panels_json = None
    if panels_path.exists():
        try:
            panels_json = json.loads(panels_path.read_text(encoding="utf-8"))
        except Exception:
            panels_json = None
    pdf_path = Path(papers_manifest.get(arxiv_id, {}).get("source_pdf") or Path("experiments/datasets/papers") / f"{arxiv_id}.pdf")
    log_path = cell_dir / "experiment_log.jsonl"
    png_path = cell_dir / "poster.png"
    return MetricContext(
        artifact_dir=cell_dir,
        pptx_path=cell_dir / "poster.pptx",
        png_path=png_path if png_path.exists() else None,
        panels_json=panels_json,
        experiment_log_path=log_path if log_path.exists() else None,
        paper_path=pdf_path,
        paper_meta=papers_manifest.get(arxiv_id, {}),
        config=metric_config,
    )


def _select_metric_ids(arg: str) -> List[str]:
    if not arg or arg.lower() == "all":
        return list(MetricRegistry.all().keys())
    return [s.strip() for s in arg.split(",") if s.strip()]


def _iter_cells(artifact_arg: Path, all_flag: bool) -> Iterable[Path]:
    if all_flag:
        root = Path("experiments/results/artifacts")
        for cell in sorted(root.glob("*/*")):
            if cell.is_dir() and (cell / "metadata.json").exists():
                yield cell
        return
    if artifact_arg.is_dir():
        # Accept either a single cell or a parent of cells.
        if (artifact_arg / "metadata.json").exists():
            yield artifact_arg
        else:
            for cell in sorted(artifact_arg.iterdir()):
                if cell.is_dir() and (cell / "metadata.json").exists():
                    yield cell


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Compute metrics over artifact cells.")
    p.add_argument("--artifact", type=Path, default=None, help="Cell folder or its parent.")
    p.add_argument("--all", action="store_true", help="Scan experiments/results/artifacts/ recursively.")
    p.add_argument("--metrics", default="all", help="Comma-separated metric ids or 'all'.")
    p.add_argument("--metrics-yaml", type=Path, default=Path("experiments/configs/metrics.yaml"))
    p.add_argument("--papers-manifest", type=Path, default=Path("experiments/configs/papers_30.json"))
    p.add_argument("--out", type=Path, default=Path("experiments/results/metrics"))
    p.add_argument("--workers", type=int, default=1,
                   help="Number of cells to process in parallel (uses threads — LLM calls are I/O-bound).")
    args = p.parse_args(argv)

    _import_all_metrics()

    if not args.artifact and not args.all:
        print("[compute_metrics] either --artifact or --all is required", file=sys.stderr)
        return 2

    metric_cfg = _load_yaml(args.metrics_yaml)
    papers_manifest_list = []
    if args.papers_manifest.exists():
        try:
            papers_manifest_list = json.loads(args.papers_manifest.read_text(encoding="utf-8"))
        except Exception:
            papers_manifest_list = []
    papers_manifest = {row["arxiv_id"]: row for row in papers_manifest_list if isinstance(row, dict)}

    metric_ids = _select_metric_ids(args.metrics)
    args.out.mkdir(parents=True, exist_ok=True)

    cells = list(_iter_cells(args.artifact or Path("."), args.all))
    if not cells:
        print(f"[compute_metrics] no cells found (artifact={args.artifact} all={args.all})", file=sys.stderr)
        return 1

    print(f"[compute_metrics] {len(cells)} cell(s) × {len(metric_ids)} metric(s)  (workers={args.workers})")

    def _process_one_cell(cell: Path) -> str:
        out_results: Dict[str, Any] = {"cell": cell.name, "metrics": {}}
        for mid in metric_ids:
            cls = MetricRegistry.get(mid)
            if cls is None:
                continue
            per_metric_cfg = metric_cfg.get(mid) or {}
            ctx = _build_context(cell, per_metric_cfg, papers_manifest)
            if ctx is None:
                continue
            try:
                result: MetricResult = cls().compute(ctx)
            except Exception as exc:
                result = MetricResult(metric_id=mid, score=None, skipped=True, skip_reason=f"{type(exc).__name__}: {exc}")
            out_results["metrics"][mid] = result.to_dict()
        out_path = args.out / f"{cell.name}.json"
        out_path.write_text(json.dumps(out_results, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(out_path)

    if args.workers <= 1:
        for cell in cells:
            print(f"  wrote {_process_one_cell(cell)}")
    else:
        import concurrent.futures as cf
        with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {pool.submit(_process_one_cell, c): c.name for c in cells}
            for fut in cf.as_completed(futures):
                try:
                    print(f"  wrote {fut.result()}")
                except Exception as exc:
                    print(f"  FAIL [{futures[fut]}]: {type(exc).__name__}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
