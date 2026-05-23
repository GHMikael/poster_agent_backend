"""Match Dify-produced ``input.json`` files to PDFs and import them into ``planner_cache/``.

Why this exists
---------------

Your production setup runs each paper through a Dify workflow which
ultimately POSTs a fully-assembled :class:`~app.models.PosterTask`
payload to ``/generate_ppt``. The backend archives that payload as
``outputs/runs/<run_folder>/input.json``.

For the experiment matrix to faithfully reproduce production quality,
the ``OursSVFPRunner`` and ``OursNoSVFPRunner`` baselines need access
to those exact payloads — keyed by the **PDF file name** the runner
sees on disk (e.g. ``RAG_0816F4.pdf``), not by paper title.

The catch
---------

* The PDF filename (``RAG_0816F4``) does NOT contain the paper title.
* The Dify run folder (``20260523_043749_Agentic_Context_Engineering_Ev_…``)
  is named after a *slug* of the title, not the PDF.
* So we cannot pair the two by filename alone.

Matching strategy
-----------------

For each ``input.json`` we:

1. Read its ``poster_title`` field.
2. Normalise it (lowercase, strip whitespace).
3. For each PDF in ``experiments/datasets/papers/``, extract the
   first-page text via PyMuPDF and normalise the same way.
4. **Strong match**: the first 30 normalised chars of the title appear
   verbatim in the normalised page-1 text. Empirically, this finds the
   correct PDF for English-titled papers ~all the time without false
   positives.
5. **Weak match**: ≥ 4 words of length ≥ 5 from the title appear in
   the page text. Used only to *report* ambiguity to the user.

Resolution rules:

* Exactly one strong match  → import.
* Multiple strong matches    → record as ambiguous; user resolves manually.
* No strong match            → record as unmatched; user resolves
  manually OR re-runs the paper through Dify after renaming the PDF.

Output
------

Writes ``experiments/datasets/planner_cache/<pdf_stem>.json`` per match.
Refuses to overwrite an existing cache entry unless ``--force`` is set.
Prints a summary table and (with ``--report-path``) a JSON report.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import fitz  # PyMuPDF


DEFAULT_RUNS_DIR = Path("outputs/runs")
DEFAULT_PAPERS_DIR = Path("experiments/datasets/papers")
DEFAULT_CACHE_DIR = Path("experiments/datasets/planner_cache")


def _normalise(s: str) -> str:
    """Collapse whitespace + lowercase. Used for substring matching."""
    return "".join((s or "").lower().split())


def _first_page_text(pdf_path: Path, *, max_chars: int = 4000) -> str:
    """Extract the first page's text. Best-effort; returns '' on failure."""
    try:
        doc = fitz.open(str(pdf_path))
        try:
            text = doc[0].get_text("text") if len(doc) > 0 else ""
        finally:
            doc.close()
        return (text or "")[:max_chars]
    except Exception:
        return ""


def _find_input_jsons(runs_dir: Path) -> List[Path]:
    if not runs_dir.exists():
        return []
    return sorted(runs_dir.glob("*/input.json"))


def _classify_match(
    title: str,
    pdf_first_page: str,
    *,
    strong_prefix_chars: int = 30,
    weak_min_words: int = 4,
    weak_word_min_len: int = 5,
) -> str:
    """Return ``"strong"`` | ``"weak"`` | ``"none"`` for one (title, pdf) pair."""
    title_norm = _normalise(title)
    text_norm = _normalise(pdf_first_page)
    if not title_norm or not text_norm:
        return "none"
    if len(title_norm) >= strong_prefix_chars and title_norm[:strong_prefix_chars] in text_norm:
        return "strong"
    # Also count a strong match if the whole short title is contained
    # (papers with very short titles).
    if 10 <= len(title_norm) < strong_prefix_chars and title_norm in text_norm:
        return "strong"
    long_words = [w for w in title.split() if len(w) >= weak_word_min_len]
    hits = sum(1 for w in long_words if w.lower() in pdf_first_page.lower())
    if hits >= weak_min_words:
        return "weak"
    return "none"


def _match_one(
    input_json_path: Path,
    pdf_texts: Sequence[Tuple[Path, str]],
) -> Dict[str, Any]:
    """Return a match record for one input.json.

    Record schema::

        {
          "input_json": "<path>",
          "title": "<title>",
          "strong_matches": [<pdf_path>, …],
          "weak_matches":   [<pdf_path>, …],
          "decision":       "import" | "ambiguous" | "unmatched" | "error",
          "matched_pdf":    "<path or None>",
          "error":          "<message or empty>"
        }
    """
    try:
        data = json.loads(input_json_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "input_json": str(input_json_path),
            "title": "",
            "strong_matches": [],
            "weak_matches": [],
            "decision": "error",
            "matched_pdf": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    title = (data.get("poster_title") or "").strip()
    record: Dict[str, Any] = {
        "input_json": str(input_json_path),
        "title": title,
        "strong_matches": [],
        "weak_matches": [],
        "decision": "unmatched",
        "matched_pdf": None,
        "error": "",
    }
    if not title:
        record["error"] = "input.json has no poster_title"
        return record
    for pdf_path, page1 in pdf_texts:
        kind = _classify_match(title, page1)
        if kind == "strong":
            record["strong_matches"].append(str(pdf_path))
        elif kind == "weak":
            record["weak_matches"].append(str(pdf_path))
    if len(record["strong_matches"]) == 1:
        record["decision"] = "import"
        record["matched_pdf"] = record["strong_matches"][0]
    elif len(record["strong_matches"]) > 1:
        record["decision"] = "ambiguous"
    return record


def _interactive_resolve(record: Dict[str, Any]) -> Optional[Path]:
    """Prompt the user to pick a PDF for an ambiguous / unmatched record.

    Returns the chosen Path or ``None`` to skip.
    """
    candidates: List[str] = list(record["strong_matches"]) + list(record["weak_matches"])
    if not candidates:
        print(f"\n  no candidates for title: {record['title']!r}")
        return None
    print(f"\n  title: {record['title']!r}")
    print(f"  candidates:")
    for i, c in enumerate(candidates, 1):
        tag = "STRONG" if c in record["strong_matches"] else "weak  "
        print(f"    [{i}] {tag}  {c}")
    print(f"    [0] skip (do not import)")
    while True:
        choice = input("  pick [0-{}]: ".format(len(candidates))).strip()
        if not choice:
            continue
        try:
            n = int(choice)
        except ValueError:
            print("    invalid; enter a number")
            continue
        if n == 0:
            return None
        if 1 <= n <= len(candidates):
            return Path(candidates[n - 1])
        print("    out of range")


def _import_one(
    record: Dict[str, Any],
    cache_dir: Path,
    *,
    force: bool,
) -> Tuple[bool, str]:
    """Write the input.json into ``cache_dir/<pdf_stem>.json``.

    Returns ``(ok, message)``. ``ok=False`` when the cache entry already
    exists and ``force`` is False, or when the matched_pdf is missing.
    """
    matched = record.get("matched_pdf")
    if not matched:
        return False, "no matched PDF"
    pdf_path = Path(matched)
    cache_path = cache_dir / f"{pdf_path.stem}.json"
    if cache_path.exists() and not force:
        return False, f"exists (use --force to overwrite): {cache_path}"
    cache_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(record["input_json"], cache_path)
    return True, str(cache_path)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Import Dify-produced PosterTask JSONs into planner_cache/, "
                    "auto-matching by first-page text.",
    )
    p.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    p.add_argument("--papers-dir", type=Path, default=DEFAULT_PAPERS_DIR)
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    p.add_argument("--dry-run", action="store_true", help="Report matches but do not copy.")
    p.add_argument("--force", action="store_true", help="Overwrite existing cache entries.")
    p.add_argument("--interactive", action="store_true",
                   help="Prompt for ambiguous/unmatched records instead of skipping.")
    p.add_argument("--report-path", type=Path, default=None,
                   help="Optional path to write the per-run match report as JSON.")
    args = p.parse_args(argv)

    input_jsons = _find_input_jsons(args.runs_dir)
    if not input_jsons:
        print(f"[import_dify_runs] no input.json found under {args.runs_dir}", file=sys.stderr)
        return 1

    pdfs = sorted(args.papers_dir.glob("*.pdf"))
    if not pdfs:
        print(f"[import_dify_runs] no PDFs under {args.papers_dir}", file=sys.stderr)
        return 1

    print(f"[import_dify_runs] {len(input_jsons)} Dify run(s) × {len(pdfs)} PDF(s)")
    print(f"[import_dify_runs] extracting first-page text from PDFs...")
    pdf_texts: List[Tuple[Path, str]] = [(p, _first_page_text(p)) for p in pdfs]

    records: List[Dict[str, Any]] = []
    counts = {"import": 0, "ambiguous": 0, "unmatched": 0, "error": 0, "skipped_cached": 0, "written": 0}
    for ij in input_jsons:
        rec = _match_one(ij, pdf_texts)
        records.append(rec)
        decision = rec["decision"]
        title_short = (rec["title"] or "")[:60]

        if decision == "import":
            if args.dry_run:
                print(f"  [dry] would import : {Path(rec['matched_pdf']).name:<40} ← {title_short!r}")
                counts["import"] += 1
                continue
            ok, msg = _import_one(rec, args.cache_dir, force=args.force)
            if ok:
                print(f"  imported          : {Path(rec['matched_pdf']).name:<40} ← {title_short!r}")
                counts["written"] += 1
            else:
                if "exists" in msg:
                    print(f"  skipped (cached)  : {Path(rec['matched_pdf']).name:<40} — {msg}")
                    counts["skipped_cached"] += 1
                else:
                    print(f"  FAILED            : {msg}")
                    counts["error"] += 1
            counts["import"] += 1
            continue

        if decision == "ambiguous":
            counts["ambiguous"] += 1
            print(f"  ambiguous         : {len(rec['strong_matches'])} strong matches for {title_short!r}")
            for c in rec["strong_matches"]:
                print(f"      strong: {c}")
            if args.interactive:
                chosen = _interactive_resolve(rec)
                if chosen and not args.dry_run:
                    rec["matched_pdf"] = str(chosen)
                    ok, msg = _import_one(rec, args.cache_dir, force=args.force)
                    if ok:
                        print(f"    → imported (manual): {msg}")
                        counts["written"] += 1
                    else:
                        print(f"    → not imported: {msg}")
            continue

        if decision == "unmatched":
            counts["unmatched"] += 1
            print(f"  unmatched         : {title_short!r}")
            if rec["weak_matches"]:
                print(f"      weak candidates ({len(rec['weak_matches'])}): "
                      + ", ".join(Path(c).name for c in rec["weak_matches"][:5]))
            if args.interactive:
                chosen = _interactive_resolve(rec)
                if chosen and not args.dry_run:
                    rec["matched_pdf"] = str(chosen)
                    ok, msg = _import_one(rec, args.cache_dir, force=args.force)
                    if ok:
                        print(f"    → imported (manual): {msg}")
                        counts["written"] += 1
                    else:
                        print(f"    → not imported: {msg}")
            continue

        counts["error"] += 1
        print(f"  ERROR             : {rec.get('error', '')}")

    print()
    print(f"[import_dify_runs] summary:")
    for k, v in counts.items():
        print(f"    {k:<18}: {v}")
    if args.report_path is not None:
        args.report_path.parent.mkdir(parents=True, exist_ok=True)
        args.report_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"    report           : {args.report_path}")

    return 0 if counts["error"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
