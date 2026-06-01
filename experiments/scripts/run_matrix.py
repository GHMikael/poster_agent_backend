"""M3 main experiment orchestrator — 30 papers × 4 baselines.

Walks the paper manifest × the enabled baselines, fans out cells across
``--workers`` processes via ``concurrent.futures.ProcessPoolExecutor``,
and writes each cell's artifacts to
``experiments/results/artifacts/<baseline>_<arxiv_id>/``.

A cell that has already produced a non-empty ``poster.pptx`` is skipped
unless ``--rerun`` is passed (idempotent reruns after partial failures).

Per-cell ``POSTER_EXPERIMENT_MODE=1`` + ``POSTER_EXPERIMENT_LOG=<cell>/experiment_log.jsonl``
are set in the child env so the production hooks emit telemetry.

Usage::

    .venv312/bin/python -m experiments.scripts.run_matrix \\
        --papers experiments/configs/papers_30.json \\
        --baselines all \\
        --workers 4
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import importlib
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml  # type: ignore


def _load_yaml(path: Path) -> Dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _select_baselines(arg: str, baselines_yaml: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = _load_yaml(baselines_yaml).get("baselines", [])
    if not arg or arg.lower() == "all":
        return rows
    wanted = {s.strip() for s in arg.split(",") if s.strip()}
    return [r for r in rows if r.get("name") in wanted]


def _load_papers(manifest_path: Path) -> List[Dict[str, Any]]:
    if not manifest_path.exists():
        raise SystemExit(f"manifest not found: {manifest_path}; run prepare_dataset.py first")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def _cell_dir(out_root: Path, baseline_name: str, arxiv_id: str) -> Path:
    return out_root / f"{baseline_name}_{arxiv_id}"


def _is_done(cell_dir: Path) -> bool:
    pptx = cell_dir / "poster.pptx"
    meta = cell_dir / "metadata.json"
    return pptx.exists() and pptx.stat().st_size > 5_000 and meta.exists()


def _run_one_cell(
    baseline_row: Dict[str, Any],
    paper_row: Dict[str, Any],
    out_root_str: str,
    timeout_s: int,
) -> Dict[str, Any]:
    """Subprocess entry: do not touch process-wide state aside from env vars."""
    try:
        out_root = Path(out_root_str)
        out_root.mkdir(parents=True, exist_ok=True)

        module = importlib.import_module(baseline_row["module"])
        cls = getattr(module, baseline_row["class"])
        runner = cls(config=baseline_row)

        paper_path = Path(paper_row["source_pdf"])
        if not paper_path.exists():
            return {"ok": False, "baseline": baseline_row["name"], "arxiv_id": paper_row["arxiv_id"], "error": f"pdf missing: {paper_path}"}

        artifact = runner.run(paper_path, out_root, timeout_s=timeout_s)
        return {
            "ok": artifact.metadata.exit_code == 0,
            "baseline": baseline_row["name"],
            "arxiv_id": paper_row["arxiv_id"],
            "latency_ms": artifact.metadata.total_latency_ms,
            "exit_code": artifact.metadata.exit_code,
            "error": artifact.metadata.error,
        }
    except Exception as exc:
        return {
            "ok": False,
            "baseline": baseline_row["name"],
            "arxiv_id": paper_row["arxiv_id"],
            "error": f"{type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(limit=4),
        }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="M3 main experiment: 30 papers × 4 baselines.")
    p.add_argument("--papers", type=Path, default=Path("experiments/configs/papers_30.json"))
    p.add_argument("--baselines", default="all", help="Comma-separated baseline ids or 'all'.")
    p.add_argument("--baselines-yaml", type=Path, default=Path("experiments/configs/baselines.yaml"))
    p.add_argument("--out", type=Path, default=Path("experiments/results/artifacts"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--rerun", action="store_true", help="Re-execute cells that already have artifacts.")
    p.add_argument("--limit", type=int, default=0, help="Run only the first N cells (for testing).")
    args = p.parse_args(argv)

    baselines = _select_baselines(args.baselines, args.baselines_yaml)
    papers = _load_papers(args.papers)
    args.out.mkdir(parents=True, exist_ok=True)

    cells: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    skipped = 0
    for b in baselines:
        for p_row in papers:
            cd = _cell_dir(args.out, b["name"], p_row["arxiv_id"])
            if _is_done(cd) and not args.rerun:
                skipped += 1
                continue
            cells.append((b, p_row))
    if args.limit > 0:
        cells = cells[: args.limit]

    print(f"[run_matrix] {len(baselines)} baselines × {len(papers)} papers = {len(baselines) * len(papers)} cells")
    print(f"  pending : {len(cells)}")
    print(f"  skipped : {skipped} (already done; pass --rerun to redo)")
    if not cells:
        return 0

    summary = {"total": len(cells), "ok": 0, "failed": 0, "per_cell": []}
    if args.workers <= 1:
        for b, paper_row in cells:
            res = _run_one_cell(b, paper_row, str(args.out), args.timeout)
            summary["per_cell"].append(res)
            if res.get("ok"):
                summary["ok"] += 1
                print(f"  ok   [{res['baseline']:>15}] {res['arxiv_id']} ({res.get('latency_ms', 0):.0f} ms)")
            else:
                summary["failed"] += 1
                print(f"  FAIL [{res['baseline']:>15}] {res['arxiv_id']}: {res.get('error', '')[:120]}")

        summary_path = args.out / "_run_matrix_summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n[run_matrix] done. ok={summary['ok']} failed={summary['failed']}. summary at {summary_path}")
        return 0 if summary["failed"] == 0 else 1

    with cf.ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(_run_one_cell, b, paper_row, str(args.out), args.timeout): (b["name"], paper_row["arxiv_id"])
            for (b, paper_row) in cells
        }
        for fut in cf.as_completed(futures):
            name = futures[fut]
            try:
                res = fut.result()
            except Exception as exc:
                res = {"ok": False, "baseline": name[0], "arxiv_id": name[1], "error": f"{type(exc).__name__}: {exc}"}
            summary["per_cell"].append(res)
            if res.get("ok"):
                summary["ok"] += 1
                print(f"  ok   [{res['baseline']:>15}] {res['arxiv_id']} ({res.get('latency_ms', 0):.0f} ms)")
            else:
                summary["failed"] += 1
                print(f"  FAIL [{res['baseline']:>15}] {res['arxiv_id']}: {res.get('error', '')[:120]}")

    summary_path = args.out / "_run_matrix_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[run_matrix] done. ok={summary['ok']} failed={summary['failed']}. summary at {summary_path}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
