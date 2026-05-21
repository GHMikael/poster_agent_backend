# Paper-to-Poster Backend

This is a decoupled backend for a Dify paper-to-poster workflow:

1. `/extract_pdf_assets` extracts PDF text preview and figure images.
2. Dify agents parse text, analyze figures and plan poster panels.
3. `/generate_ppt` renders a dashboard-style editable PPTX poster.

## Setup

```bash
cd /Users/mikaelsnow/Documents/ECNU/Paper_Comment_Poster/poster_agent_backend
/Users/mikaelsnow/anaconda3/bin/python -m venv .venv312
source .venv312/bin/activate
pip install -r requirements.txt
```

Use Python 3.12 for this project. Python 3.13 may force `PyMuPDF==1.24.9` to compile from source on macOS, which is slow and can fail.

## Run

```bash
python -m app.main
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Test PDF Asset Extraction

```bash
curl -X POST "http://127.0.0.1:8000/extract_pdf_assets" \
  -F "file=@/Users/mikaelsnow/Documents/ECNU/Paper_Comment_Poster/2604.05005v2.pdf"
```

By default this endpoint does not return base64 images. It stores extracted figures under
`static/assets/{asset_token}/` and returns lightweight metadata plus `image_url` and
`thumbnail_url`. Keep `include_images=false` for Dify workflows.

## Test PPT Generation

```bash
curl -X POST "http://127.0.0.1:8000/generate_ppt" \
  -H "Content-Type: application/json" \
  -d @tests/test_payload.json \
  --output outputs/generated_poster.pptx
```

## Visual Feedback Loop

Set `use_commenter=true` and `max_iterations` in the Planner JSON to enable the
generate-check-repair loop:

```json
{
  "use_commenter": true,
  "max_iterations": 3
}
```

Recommended endpoint:

```bash
curl -X POST "http://127.0.0.1:8000/generate_ppt_file" \
  -H "Content-Type: application/json" \
  -d @outputs/test_payload_feedback.json
```

The response contains a PPTX download URL and feedback trace:

```json
{
  "status": "completed",
  "download_url": "/download/feedback_refined_xxx.pptx",
  "feedback": {
    "best_score": 8.8,
    "iterations": 3,
    "history_path": "static/feedback_xxx/feedback_history.json"
  }
}
```

The current loop has two stages:

- Stage 1: render a fast Pillow preview PNG, then run structured VLM feedback or
  heuristic fallback.
- Stage 2: if LibreOffice/soffice is installed, render the real PPTX to PNG and
  run another feedback pass.

If `DASHSCOPE_API_KEY` is empty or the VLM is unavailable, the loop falls back to
heuristic checks for text overflow, dense content, empty space, figure/layout
mismatch and missing emphasis.

Archived runs are written under `outputs/runs/<timestamp_title_runid>/`.
Use the analyser to inspect convergence and repeated failure modes:

```bash
python -m app.run_analysis outputs/runs/<run_folder>/run_report.json
```

The analyser reports score/issue curves, repeated VLM issues, action counts
and concrete suggestions, which is useful for paper experiments and ablations.

The renderer supports visual-quality controls in the Planner JSON:

```json
{
  "template": "template_dashboard",
  "color_theme": "academic_blue"
}
```

Available template names:

- `template_dashboard`: six-zone dashboard poster with a strong center panel.
- `template_classic`: balanced academic poster with equal column sections.
- `template_storyflow`: horizontal six-step narrative poster.
- `template_minimal`: quiet, high-whitespace card poster.

Available color themes: `academic_blue`, `engineering_green`, `warm_orange`,
`minimal_gray`.

Suggested PlannerAgent rules:

- Method-heavy or benchmark papers: `template_dashboard`
- Standard experiment papers: `template_classic`
- Pipeline/workflow/system papers: `template_storyflow`
- Conceptual or clean summary posters: `template_minimal`

For Dify cloud, expose the local service with `ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000`, then use the public URL in HTTP nodes.
