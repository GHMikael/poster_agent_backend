# Experiments — Paper-to-Poster

Reproduction package for the experiments section. Everything here is **offline / batch**: nothing in this folder is imported by the FastAPI production path.

## Layout

```
experiments/
├── configs/          YAML configs (default, baselines, metrics) + papers_30.json manifest
├── datasets/         30 papers (PDFs gitignored), per-paper gold figures/sections/claims
├── baselines/        4 baselines: ours_svfp, ours_no_svfp, gpt4o_zeroshot, paper2poster, posteragent
├── metrics/          12 metric implementations (A1-A4 content, B1-B3 visual, C1-C3 user, D1-D3 engineering)
├── judges/           LLM/VLM-as-judge: claim extractor, NLI, AltCLIP, layout rubric, PaperQuiz gen+answer
├── scripts/          run_one_paper, run_matrix, compute_metrics, aggregate_stats, plot_figures
├── results/          artifacts/, metrics/, aggregate/, figures/ (all gitignored)
├── notebooks/        exploration + paper-figure prep
├── tools/            shared utilities: experiment_logger, history_logger (moved), pdf_text, pricing
├── scratch/          ad-hoc dev scripts, never imported
└── tests/            smoke tests for metrics + baselines
```

## Milestones

| Acceptance gate | When | How to verify |
|---|---|---|
| **M2** infra ready | 6/01 | `python -m experiments.scripts.run_one_paper --paper <pdf> --baseline ours_svfp` produces poster + all 12 metric outputs in < 8 min |
| **M3** main data done | 6/08 | `run_matrix` over `configs/papers_30.json × 4 baselines` writes 120 artifacts; `compute_metrics --all` fills `results/metrics/`. ~12 h wall-clock |
| **M4** figures done | 6/15 | `aggregate_stats` produces `results/aggregate/{aggregate,pairwise}.tsv`; `plot_figures` produces ≥ 10 PDFs in `results/figures/` |

## Running

Prerequisites:
- The FastAPI backend running at `http://127.0.0.1:8000` (the `Ours*` baselines call it over HTTP).
- `OPENAI_API_KEY`, `DASHSCOPE_API_KEY` for judges and answerers.
- Optional: GPU for DeBERTa-v3 NLI (A3) and AltCLIP (A2) — CPU works but slower.

```bash
# M2 smoke (single paper)
.venv312/bin/python -m experiments.scripts.run_one_paper \
  --paper experiments/datasets/papers/2405.12345.pdf \
  --baseline ours_svfp \
  --out experiments/results/artifacts/_smoke

.venv312/bin/python -m experiments.scripts.compute_metrics \
  --artifact experiments/results/artifacts/_smoke --metrics all

# M3 full matrix
.venv312/bin/python -m experiments.scripts.run_matrix \
  --papers experiments/configs/papers_30.json --baselines all --workers 4
.venv312/bin/python -m experiments.scripts.compute_metrics --all
.venv312/bin/python -m experiments.scripts.aggregate_stats --out experiments/results/aggregate/
.venv312/bin/python -m experiments.scripts.plot_figures --out experiments/results/figures/
```

## Instrumentation

When `POSTER_EXPERIMENT_MODE=1` is set, `app/feedback_loop.py` and `app/vlm_commenter.py` emit per-call JSONL events (token counts, latency, raw VLM responses, soffice exit codes) into `$POSTER_EXPERIMENT_LOG`. With the env unset (production default), the hook is a single `None` check — zero overhead.

## Statistical analysis

See `scripts/aggregate_stats.py`. For each (metric, baseline) cell:

- mean ± 95% bootstrap CI (BCa, n=1000)
- Wilcoxon signed-rank Ours-SVFP vs each of 3 baselines, paired by paper
- Bonferroni α=0.05/3 within a metric; Benjamini-Hochberg FDR q=0.10 across all 36 (metric × baseline) tests
- rank-biserial r and Cohen's d effect sizes (both reported because n=30)

User study (C2/C3) is pilot-scale (n=10-15): descriptives + within-subject paired diffs only, no inferential stats.
