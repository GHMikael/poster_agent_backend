import json
import threading
import uuid

import requests
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from app.asset_store import hydrate_task_image_sources, persist_extracted_figures, strip_heavy_image_sources
from app.config import ASSET_PATH, PORT
from app.feedback_loop import VisualFeedbackLoop
from app.job_store import JOBS, now_iso
from app.models import PosterTask
from app.pdf_assets import extract_pdf_assets_from_bytes
from app.ppt_renderer import generate_dashboard_pptx
from app.run_archive import RUNS_ROOT, RunArchive, slugify, update_runs_index


app = FastAPI(title="Paper-to-Poster Backend", version="5.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok", "service": "paper-to-poster-backend"}


def _pptx_filename_for_run(run_folder: str) -> str:
    """Derive a paper-title-based PPTX filename for a run folder.

    Reads ``input.json`` for the full ``poster_title`` and slugifies it.
    Falls back to parsing the folder name (``<ts>_<slug>_<runid>``) and
    finally to ``poster.pptx`` so the download always has a name.
    """

    input_path = RUNS_ROOT / run_folder / "input.json"
    if input_path.exists():
        try:
            data = json.loads(input_path.read_text(encoding="utf-8"))
            title = (data.get("poster_title") or "").strip()
            if title:
                return f"{slugify(title, max_len=60)}.pptx"
        except Exception:
            pass

    parts = run_folder.split("_")
    if len(parts) >= 4:
        slug = "_".join(parts[2:-1]).strip("_")
        if slug:
            return f"{slug}.pptx"
    return "poster.pptx"


@app.post("/extract_pdf_assets")
async def extract_pdf_assets(
    request: Request,
    file: UploadFile = File(None),
    pdf_url: str = Form(None),
    include_images: bool = Form(False),
):
    try:
        if file is not None:
            pdf_bytes = await file.read()
        elif pdf_url:
            resp = requests.get(pdf_url, timeout=30)
            if resp.status_code != 200:
                raise HTTPException(status_code=400, detail="Failed to download PDF from pdf_url")
            pdf_bytes = resp.content
        else:
            raise HTTPException(status_code=422, detail="Please provide file or pdf_url")

        text_preview, figures = extract_pdf_assets_from_bytes(pdf_bytes)
        asset_token = persist_extracted_figures(figures)
        base_url = str(request.base_url).rstrip("/")

        return JSONResponse(
            content={
                "asset_token": asset_token,
                "text_preview": text_preview,
                "figures": strip_heavy_image_sources(figures, asset_token, base_url, include_images=include_images),
            }
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"extract_pdf_assets failed: {exc}")


async def _parse_poster_task(request: Request) -> PosterTask:
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        return PosterTask(**await request.json())
    form = await request.form()
    raw = form.get("payload") or form.get("task") or form.get("json")
    if not raw:
        raise HTTPException(status_code=422, detail="Missing payload/task/json form field")
    return PosterTask(**json.loads(raw))


def _run_poster_job(job_id: str, task: PosterTask, base_url: str) -> None:
    JOBS.update(job_id, status="running", started_at=now_iso())
    try:
        if task.use_commenter:
            outcome = VisualFeedbackLoop().run(task)
            run_folder = outcome["run_folder"]
            result = {
                "run_folder": run_folder,
                "filename": _pptx_filename_for_run(run_folder),
                "download_url": f"{base_url}/download/run/{run_folder}",
                "best_score": outcome["best_score"],
                "iterations": outcome["iterations"],
                "converged": outcome["converged"],
                "convergence_reason": outcome["convergence_reason"],
            }
        else:
            ppt_buf = generate_dashboard_pptx(task)
            archive = RunArchive.create(uuid.uuid4().hex[:12], task.poster_title)
            archive.save_input(task.model_dump())
            archive.save_final_pptx_bytes(ppt_buf.getvalue())
            archive.save_report(
                input_task=task.model_dump(),
                summary={
                    "best_score": None,
                    "iterations": 0,
                    "converged": True,
                    "convergence_reason": "no_feedback_loop",
                    "score_curve": [],
                },
                iterations=[],
            )
            try:
                update_runs_index()
            except Exception as exc:
                print(f"update_runs_index failed: {exc}")
            result = {
                "run_folder": archive.folder_name,
                "filename": _pptx_filename_for_run(archive.folder_name),
                "download_url": f"{base_url}/download/run/{archive.folder_name}",
                "best_score": None,
                "iterations": 0,
                "converged": True,
                "convergence_reason": "no_feedback_loop",
            }

        JOBS.update(job_id, status="completed", finished_at=now_iso(), result=result)
    except Exception as exc:
        JOBS.update(
            job_id,
            status="failed",
            finished_at=now_iso(),
            error=f"{type(exc).__name__}: {exc}",
        )


@app.post("/generate_ppt")
async def generate_ppt(request: Request):
    """Kick off PPT generation in the background and return a job_id.

    The feedback-loop path runs 60-180s per poster. A synchronous HTTP
    response would exceed Dify's HTTP-node timeout, the client would
    retry, and the retries would queue at FastAPI producing duplicate
    runs. Returning a job_id immediately and letting the caller poll
    GET /jobs/{job_id} sidesteps that entirely.
    """

    try:
        task = await _parse_poster_task(request)
        task = hydrate_task_image_sources(task)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=422, detail=f"Invalid task payload: {exc}")

    job = JOBS.create()
    base_url = str(request.base_url).rstrip("/")
    threading.Thread(
        target=_run_poster_job,
        args=(job.job_id, task, base_url),
        daemon=True,
        name=f"poster-job-{job.job_id}",
    ).start()

    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.job_id,
            "status": "pending",
            "status_url": f"/jobs/{job.job_id}",
        },
    )


@app.get("/jobs/{job_id}")
def get_job(job_id: str, wait: float = 20):
    """Return job status. Long-polls up to `wait` seconds by default.

    Pass ?wait=0 to disable and get an immediate snapshot. The default
    blocks server-side until the job reaches a terminal state
    (completed/failed) or the timeout elapses. This lets clients that
    lack per-iteration delays (e.g. Dify's loop node) self-pace via the
    response itself — 20s is well under Dify's ~60s HTTP node timeout.
    """
    wait = max(0.0, min(wait, 50.0))
    if wait > 0:
        job = JOBS.wait_for_terminal(job_id, timeout=wait)
    else:
        job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job.to_dict()


@app.post("/generate_ppt_file")
async def generate_ppt_file(request: Request):
    try:
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type:
            task = PosterTask(**await request.json())
        else:
            form = await request.form()
            raw = form.get("payload") or form.get("task") or form.get("json")
            if not raw:
                raise HTTPException(status_code=422, detail="Missing payload/task/json form field")
            task = PosterTask(**json.loads(raw))

        task = hydrate_task_image_sources(task)
        if task.use_commenter:
            result = VisualFeedbackLoop().run(task)
            return {
                "status": "completed",
                "download_url": f"/download/run/{result['run_folder']}",
                "filename": _pptx_filename_for_run(result["run_folder"]),
                "run_folder": result["run_folder"],
                "feedback": {
                    "run_id": result["run_id"],
                    "best_score": result["best_score"],
                    "iterations": result["iterations"],
                    "history": result["history"],
                },
            }

        ppt_buf = generate_dashboard_pptx(task)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"generate_ppt_file failed: {exc}")

    task_id = uuid.uuid4().hex[:12]
    archive = RunArchive.create(task_id, task.poster_title)
    archive.save_input(task.model_dump())
    archive.save_final_pptx_bytes(ppt_buf.getvalue())
    archive.save_report(
        input_task=task.model_dump(),
        summary={
            "best_score": None,
            "iterations": 0,
            "converged": True,
            "convergence_reason": "no_feedback_loop",
            "score_curve": [],
        },
        iterations=[],
    )
    try:
        update_runs_index()
    except Exception as exc:
        print(f"update_runs_index failed: {exc}")

    return {
        "status": "completed",
        "download_url": f"/download/run/{archive.folder_name}",
        "filename": _pptx_filename_for_run(archive.folder_name),
        "run_folder": archive.folder_name,
    }


@app.get("/download/run/{run_folder}")
def download_run(run_folder: str):
    # Block path traversal: a folder name is a single segment.
    if "/" in run_folder or ".." in run_folder:
        raise HTTPException(status_code=400, detail="Invalid run folder")
    path = RUNS_ROOT / run_folder / "final.pptx"
    if not path.exists():
        raise HTTPException(status_code=404, detail="PPTX not found")
    return FileResponse(
        path,
        media_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        filename=_pptx_filename_for_run(run_folder),
    )


@app.get("/assets/{asset_token}/{filename}")
def get_asset(asset_token: str, filename: str):
    if "/" in filename or ".." in filename or ".." in asset_token:
        raise HTTPException(status_code=400, detail="Invalid asset path")

    path = ASSET_PATH / asset_token / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Asset not found")

    media_type = "image/jpeg" if path.suffix.lower() in [".jpg", ".jpeg"] else "image/png"
    return FileResponse(path, media_type=media_type)


if __name__ == "__main__":
    import os
    import uvicorn

    # Default experiment-mode ON when launched via ``python -m app.main``
    # so each run automatically drops outputs/runs/<id>/experiment_log.jsonl
    # next to input.json and run_report.json. Operators who want a clean
    # run (no telemetry) can override with POSTER_EXPERIMENT_MODE=0.
    os.environ.setdefault("POSTER_EXPERIMENT_MODE", "1")

    print(f"Poster backend running at http://localhost:{PORT}")
    print(f"  experiment mode: {os.environ.get('POSTER_EXPERIMENT_MODE')}  (set POSTER_EXPERIMENT_MODE=0 to disable)")
    uvicorn.run("app.main:app", host="0.0.0.0", port=PORT, reload=False)
