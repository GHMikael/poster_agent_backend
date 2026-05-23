"""M2 deliverable — build the 30-paper benchmark manifest.

Sources:

* **15 from Paper2Poster benchmark** — gold figures + curated structure
  (when their repo is bootstrapped under ``baselines/_vendor/Paper2Poster``).
* **15 from arXiv 2024-2025** — top-cited via Semantic Scholar
  (or a hand-curated seed list passed via ``--seed-list``).

Filters:
  * 6 ≤ page_count ≤ 30
  * Category balance: 10 cs.CV + 10 cs.CL + 10 cs.LG/AI (configurable)

The script is rerun-safe and idempotent. PDFs are stored under
``experiments/datasets/papers/`` (gitignored). Manifest is written to
``experiments/configs/papers_30.json``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Build the 30-paper benchmark manifest.")
    p.add_argument("--out-manifest", type=Path, default=Path("experiments/configs/papers_30.json"))
    p.add_argument("--out-papers", type=Path, default=Path("experiments/datasets/papers"))
    p.add_argument("--out-gold", type=Path, default=Path("experiments/datasets/gold"))
    p.add_argument("--seed-list", type=Path, default=None, help="Optional JSON list of arxiv_ids to seed.")
    p.add_argument("--target-n", type=int, default=30)
    p.add_argument("--source", choices=["paper2poster", "arxiv", "mixed"], default="mixed")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    args.out_papers.mkdir(parents=True, exist_ok=True)
    args.out_gold.mkdir(parents=True, exist_ok=True)

    # M3 outline:
    #  1. If --source includes paper2poster: scan
    #     experiments/baselines/_vendor/Paper2Poster/data/<manifest>.csv
    #     and copy their PDFs + figures into our gold layout.
    #  2. If --source includes arxiv: read --seed-list (or query Semantic
    #     Scholar for top-cited 2024-2025 papers per category), download
    #     PDFs via the `arxiv` package, run PyMuPDF figure extraction.
    #  3. Validate page_count and dedupe.
    #  4. Balance categories per configs/default.yaml `dataset.categories`.
    #  5. Write manifest JSON.

    if args.dry_run:
        print(f"[prepare_dataset] dry-run: target {args.target_n} papers, source={args.source}")
        print(f"  manifest will be written to {args.out_manifest}")
        print(f"  PDFs will live under {args.out_papers}")
        print(f"  gold figures under {args.out_gold}")
        return 0

    print("[prepare_dataset] full acquisition is an M2/M3 deliverable.")
    print("Status check:")
    n_pdfs = len(list(args.out_papers.glob("*.pdf")))
    print(f"  PDFs present : {n_pdfs}")
    print(f"  Manifest    : {'EXISTS' if args.out_manifest.exists() else 'MISSING'} ({args.out_manifest})")
    if n_pdfs == 0:
        print()
        print("Next steps:")
        print(f"  1. Drop arXiv PDFs into {args.out_papers}/<arxiv_id>.pdf")
        print(f"  2. Provide a seed list with --seed-list, or wait for the M3 implementation")
        print(f"     (Semantic Scholar query) to land in this script.")
        return 0

    # Minimal manifest stub for whatever PDFs are already on disk —
    # downstream scripts can begin smoke-running before M3 lands.
    rows: List[Dict[str, Any]] = []
    for pdf in sorted(args.out_papers.glob("*.pdf")):
        arxiv_id = pdf.stem
        rows.append({
            "arxiv_id": arxiv_id,
            "title": "",
            "authors": [],
            "category": "cs.AI",
            "year": 2026,
            "page_count": None,
            "source_pdf": str(pdf),
            "source_url": "",
            "license": "arXiv non-exclusive",
            "gold_figure_count": 0,
            "gold_figures": [],
            "gold_sections": [],
            "gold_claims_path": "",
            "from_paper2poster_bench": False,
        })
    args.out_manifest.parent.mkdir(parents=True, exist_ok=True)
    args.out_manifest.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[prepare_dataset] wrote stub manifest with {len(rows)} entries to {args.out_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
