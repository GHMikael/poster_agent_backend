"""Batch-trigger Dify CHATFLOW runs for N papers, one PDF per run.

Why this script exists
----------------------
Each paper in our experiment matrix needs a *planner snapshot* — the
``PosterTask`` JSON your Dify chatflow produces, archived locally as
``outputs/runs/<folder>/input.json``. The downstream baselines
(``ours_svfp`` / ``ours_no_svfp``) replay these snapshots through the
renderer so that all baselines are compared on **identical plans**,
removing planner non-determinism as a confound.

Without this script you would have to upload each PDF through the
Dify UI by hand. With 25–30 papers that is ~2 hours of clicking.

What the script does
--------------------
For each PDF in the selected list, in series:
  1. Upload the PDF to Dify         (POST /v1/files/upload)
  2. Trigger your chatflow          (POST /v1/chat-messages, streaming)
  3. Consume the SSE stream         (until ``message_end`` or
                                     ``workflow_finished`` arrives)
  4. Persist a per-paper record     (status, run_id, timing, error)

Because your Dify chatflow's HTTP nodes call back into your local
FastAPI backend, the side effect of a successful run is a new
``outputs/runs/<folder>/`` with ``input.json``. This script does NOT
manage that side channel — it just orchestrates the upstream trigger.
After all papers finish, run ``import_dify_runs.py`` to match those
runs to PDF filenames.

Prerequisites (before running)
------------------------------
1. Your FastAPI backend is up and reachable from Dify.
   (Self-hosted Dify on the same machine can hit it via
   ``http://host.docker.internal:8000`` or your LAN IP.)
   Test:  curl <url-Dify-uses>/health   →  {"status":"ok"}
2. Your Dify chatflow is deployed and you have its app API key (the
   key shown on the chatflow's "Access API" tab, starts with ``app-``).
3. ``.env`` contains:

       DIFY_API_KEY=app-xxxxxxxxxxxxxxxxxxxxxxxx
       DIFY_BASE_URL=http://localhost/v1        # self-host default
       DIFY_WORKFLOW_INPUT_NAME=paper            # your Start node variable name
       DIFY_USER_ID=experiment-batch
       DIFY_QUERY=Generate a conference-style poster from this paper.

   ``DIFY_WORKFLOW_INPUT_NAME`` must match the input *variable* name
   in your chatflow's Start node — open the chatflow in Dify and look
   at the Start node's variable list.

Suggested workflow
------------------
Dry-run first to confirm which PDFs will be selected::

    python -m experiments.scripts.batch_dify_runs --limit 25 --skip-cached --dry-run

Then run for real::

    python -m experiments.scripts.batch_dify_runs --limit 25 --skip-cached

Output
------
Writes ``experiments/results/batch_dify_report.json`` after each paper
(so a crash mid-batch doesn't lose progress).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PAPERS_DIR = REPO_ROOT / "experiments" / "datasets" / "papers"
DEFAULT_CACHE_DIR = REPO_ROOT / "experiments" / "datasets" / "planner_cache"
DEFAULT_REPORT = REPO_ROOT / "experiments" / "results" / "batch_dify_report.json"


def _load_env() -> None:
    """Minimal ``.env`` loader. Tolerates an absent file."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _select_pdfs(
    papers_dir: Path,
    limit: Optional[int],
    papers_list: Optional[Path],
    skip_cached: bool,
    cache_dir: Path,
) -> List[Path]:
    """Pick which PDFs to run."""
    if papers_list:
        names = papers_list.read_text(encoding="utf-8").splitlines()
        names = [n.strip() for n in names if n.strip() and not n.startswith("#")]
        pdfs = [papers_dir / n for n in names]
    else:
        pdfs = sorted(papers_dir.glob("*.pdf"))

    missing = [p for p in pdfs if not p.exists()]
    if missing:
        print(f"[batch_dify_runs] warning: {len(missing)} PDFs not found:")
        for m in missing[:5]:
            print(f"    {m}")
    pdfs = [p for p in pdfs if p.exists()]

    if skip_cached:
        cached_stems = {p.stem for p in cache_dir.glob("*.json")}
        before = len(pdfs)
        pdfs = [p for p in pdfs if p.stem not in cached_stems]
        print(f"[batch_dify_runs] --skip-cached: pruned {before - len(pdfs)} already-cached PDFs")

    if limit is not None and len(pdfs) > limit:
        pdfs = pdfs[:limit]
    return pdfs


def _upload_file(
    session: requests.Session,
    base_url: str,
    api_key: str,
    pdf_path: Path,
    user_id: str,
) -> str:
    """Upload PDF to Dify, return file_id."""
    url = f"{base_url.rstrip('/')}/files/upload"
    with pdf_path.open("rb") as fh:
        files = {"file": (pdf_path.name, fh, "application/pdf")}
        data = {"user": user_id}
        headers = {"Authorization": f"Bearer {api_key}"}
        r = session.post(url, headers=headers, files=files, data=data, timeout=120)
    if r.status_code not in (200, 201):
        raise RuntimeError(f"file upload failed [{r.status_code}]: {r.text[:300]}")
    file_id = r.json().get("id")
    if not file_id:
        raise RuntimeError(f"file upload returned no id: {r.text[:300]}")
    return file_id


def _run_chatflow_streaming(
    session: requests.Session,
    base_url: str,
    api_key: str,
    file_id: str,
    *,
    input_name: str,
    user_id: str,
    query: str,
    max_wait_sec: int,
) -> Dict[str, Any]:
    """Trigger a CHATFLOW run in streaming mode; consume SSE until end-of-run.

    Chatflow apps expose `/v1/chat-messages` (not `/v1/workflows/run`). The
    request body must include a `query` field even when the real input is
    the PDF file passed via `inputs.<variable_name>`.

    We listen for whichever of these arrives first:
      * ``message_end``       — chat-level end-of-conversation signal
      * ``workflow_finished`` — emitted when a chatflow's internal workflow
                                node completes (carries the final `status`)
    """
    url = f"{base_url.rstrip('/')}/chat-messages"
    payload = {
        "inputs": {
            input_name: {
                # Use "custom" when the chatflow's Start node accepts
                # "Other file types" (the common config). Using "document"
                # triggers Dify's stricter document-type validation and
                # returns 400 "File validation failed" for PDFs in many
                # workflow configurations. See Dify issues #10637, #11671.
                "type": "custom",
                "transfer_method": "local_file",
                "upload_file_id": file_id,
            }
        },
        "query": query,
        "response_mode": "streaming",
        "conversation_id": "",
        "user": user_id,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    deadline = time.monotonic() + max_wait_sec
    started_at = time.time()
    run_id: Optional[str] = None
    status: Optional[str] = None
    outputs: Any = None
    finished_at: Optional[float] = None
    saw_message_end = False
    last_error: Optional[str] = None

    with session.post(
        url, headers=headers, json=payload, stream=True,
        timeout=(30, max_wait_sec + 60),
    ) as r:
        if r.status_code != 200:
            raise RuntimeError(f"chat-messages call failed [{r.status_code}]: {r.text[:300]}")
        for raw_line in r.iter_lines(decode_unicode=True):
            if time.monotonic() > deadline:
                raise TimeoutError(f"chatflow exceeded {max_wait_sec}s")
            if not raw_line:
                continue
            if not raw_line.startswith("data:"):
                continue
            chunk = raw_line[5:].strip()
            if not chunk or chunk == "[DONE]":
                continue
            try:
                event = json.loads(chunk)
            except json.JSONDecodeError:
                continue
            ev = event.get("event") or event.get("type")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            # The chatflow stream emits many node-level events; we only care
            # about workflow_started / workflow_finished / message_end / error.
            if ev == "workflow_started":
                run_id = data.get("id") or run_id
            elif ev == "workflow_finished":
                run_id = data.get("id") or run_id
                status = data.get("status") or status
                outputs = data.get("outputs") or outputs
                finished_at = time.time()
                # Keep reading in case message_end comes after — but we have
                # enough to call it done if workflow_finished succeeded.
                if status == "succeeded":
                    break
            elif ev == "message_end":
                saw_message_end = True
                finished_at = finished_at or time.time()
                break
            elif ev == "error":
                # Chat-level error event (distinct from per-node failures).
                last_error = json.dumps(event)[:300]
                raise RuntimeError(f"dify error event: {last_error}")

    if finished_at is None:
        raise RuntimeError("stream ended without message_end or workflow_finished")

    # When only message_end fired (no workflow_finished), treat as success —
    # the chatflow finished without an explicit workflow-status payload.
    final_status = status or ("succeeded" if saw_message_end else "unknown")

    return {
        "workflow_run_id": run_id,
        "status": final_status,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_s": round(finished_at - started_at, 2),
        "outputs": outputs,
    }


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Batch-trigger Dify workflow for N papers.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--papers-dir", type=Path, default=DEFAULT_PAPERS_DIR,
                   help="Directory of PDFs (default: experiments/datasets/papers).")
    p.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR,
                   help="Where existing planner_cache lives (used by --skip-cached).")
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT,
                   help="Per-paper outcomes (written after each iteration).")
    p.add_argument("--limit", type=int, default=25,
                   help="Max papers to run (default 25 — leave 5 already-cached pilot papers alone).")
    p.add_argument("--papers-list", type=Path, default=None,
                   help="Optional: text file with one PDF filename per line, "
                        "overrides papers-dir ordering.")
    p.add_argument("--skip-cached", action="store_true",
                   help="Skip PDFs whose <stem>.json already exists in planner_cache.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print selected PDFs and exit without calling Dify.")
    p.add_argument("--max-wait-sec", type=int, default=600,
                   help="Per-paper max wait for workflow_finished (default 600s).")
    p.add_argument("--inter-run-sleep-sec", type=float, default=2.0,
                   help="Pause between Dify runs (default 2s).")
    args = p.parse_args(argv)

    _load_env()
    api_key = os.environ.get("DIFY_API_KEY", "").strip()
    base_url = os.environ.get("DIFY_BASE_URL", "http://localhost/v1").strip()
    input_name = os.environ.get("DIFY_WORKFLOW_INPUT_NAME", "paper").strip()
    user_id = os.environ.get("DIFY_USER_ID", "experiment-batch").strip()
    query = os.environ.get(
        "DIFY_QUERY",
        "Generate a conference-style poster from this paper.",
    ).strip()

    pdfs = _select_pdfs(
        args.papers_dir, args.limit, args.papers_list,
        args.skip_cached, args.cache_dir,
    )
    print(f"[batch_dify_runs] selected {len(pdfs)} PDF(s) from {args.papers_dir}")
    for i, pdf in enumerate(pdfs, 1):
        print(f"  {i:>3}. {pdf.name}")

    if args.dry_run:
        print("[dry-run] no Dify calls made.")
        return 0
    if not pdfs:
        print("[batch_dify_runs] no PDFs to run, exiting.")
        return 0
    if not api_key:
        print("[batch_dify_runs] DIFY_API_KEY missing in env or .env. Aborting.",
              file=sys.stderr)
        return 2

    print()
    print(f"[batch_dify_runs] Dify base:      {base_url}")
    print(f"[batch_dify_runs] input variable: {input_name}")
    print(f"[batch_dify_runs] user id:        {user_id}")
    print(f"[batch_dify_runs] query:          {query!r}")
    print(f"[batch_dify_runs] max wait/paper: {args.max_wait_sec}s")

    session = requests.Session()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    records: List[Dict[str, Any]] = []
    counts = {"succeeded": 0, "failed": 0, "errored": 0}

    for i, pdf in enumerate(pdfs, 1):
        rec: Dict[str, Any] = {
            "pdf": str(pdf), "stem": pdf.stem,
            "status": None, "workflow_run_id": None,
            "duration_s": None, "error": "",
        }
        print(f"\n[{i}/{len(pdfs)}] {pdf.name}")
        try:
            print("  → upload to Dify...")
            file_id = _upload_file(session, base_url, api_key, pdf, user_id)
            print(f"    file_id = {file_id}")
            print("  → trigger chatflow (streaming)...")
            result = _run_chatflow_streaming(
                session, base_url, api_key, file_id,
                input_name=input_name, user_id=user_id, query=query,
                max_wait_sec=args.max_wait_sec,
            )
            rec.update({
                "status": result["status"],
                "workflow_run_id": result["workflow_run_id"],
                "duration_s": result["duration_s"],
            })
            if result["status"] == "succeeded":
                counts["succeeded"] += 1
                print(f"    ✓ succeeded in {result['duration_s']}s, "
                      f"run_id={result['workflow_run_id']}")
            else:
                counts["failed"] += 1
                print(f"    ✗ status={result['status']} "
                      f"(run_id={result['workflow_run_id']})")
        except Exception as exc:
            rec["status"] = "errored"
            rec["error"] = f"{type(exc).__name__}: {exc}"
            counts["errored"] += 1
            print(f"    ! errored: {rec['error']}")

        records.append(rec)
        args.report.write_text(
            json.dumps({"counts": counts, "records": records},
                       ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if i < len(pdfs):
            time.sleep(args.inter_run_sleep_sec)

    print()
    print(f"[batch_dify_runs] summary: {counts}")
    print(f"[batch_dify_runs] report:  {args.report}")
    print()
    print("Next steps:")
    print("  1. Match Dify outputs to PDFs:")
    print("       python -m experiments.scripts.import_dify_runs")
    print("  2. Verify planner_cache count matches expectations:")
    print("       ls experiments/datasets/planner_cache/ | wc -l")
    print("  3. Run the experiment matrix:")
    print("       python -m experiments.scripts.run_matrix \\")
    print("           --papers experiments/configs/papers_30.json \\")
    print("           --baselines ours_svfp,ours_no_svfp,gpt4o_zeroshot \\")
    print("           --workers 2")

    return 0 if counts["errored"] == 0 and counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
