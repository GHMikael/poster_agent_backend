# Experiments

Offline/batch evaluation harness for PosterCSP. Nothing here is imported by
the FastAPI production path except optional telemetry helpers.

## Current Scope

The experiment stack is v5.3/v3-aligned:

- E1 three-arm protocol smoke is implemented:
  `ours_no_svfp` / `ours_freeform` / `ours_svfp`.
- E2 cross-planner baseline is implemented:
  `gpt4o_zeroshot_svfp`.
- Protocol metrics are first-class:
  `action_executability`, `convergence_rate`,
  `mean_iters_to_converge`, `per_iter_visual_gain`.
- Ceiling metrics (`a4`, current `c1`) are appendix-only.
- Human/expert metrics (`b3`, `c2`, `c3`) are implemented but pending CSV data.

## Layout

```text
experiments/
├── configs/          baselines, metrics, papers_5/papers_30 manifests
├── datasets/         PDFs and frozen planner_cache snapshots
├── baselines/        ours_svfp, ours_no_svfp, ours_freeform, gpt4o_zeroshot_svfp, ...
├── metrics/          content, visual, protocol, user/pending, engineering metrics
├── judges/           LLM/VLM-as-judge helpers
├── scripts/          run_matrix, compute_metrics, aggregate_stats, audit_figures, ...
├── results/          artifacts*, metrics*, aggregate*, figures* (local outputs)
├── tools/            telemetry, pricing, JSONL helpers
├── scratch/          ad-hoc scripts, never imported by core
└── tests/            smoke/unit tests
```

## Preflight Before Official n=30

Run tests:

```bash
python -m pytest experiments/tests tests
```

Run figure audit:

```bash
python -m experiments.scripts.audit_figures
python -m experiments.scripts.clean_planner_cache
```

Run E1 smoke:

```bash
POSTER_LLM_TIMEOUT_S=15 \
POSTER_LLM_MAX_RETRIES=1 \
POSTER_VLM_ALLOW_FALLBACK=0 \
POSTER_VLM_WALL_TIMEOUT_S=20 \
python -m experiments.scripts.run_matrix \
  --papers experiments/configs/papers_5.json \
  --baselines ours_no_svfp,ours_freeform,ours_svfp \
  --baselines-yaml experiments/configs/e1_smoke_baselines.yaml \
  --out experiments/results/artifacts_e1_preflight \
  --workers 1 \
  --timeout 300
```

Compute E1 protocol/engineering metrics:

```bash
python -m experiments.scripts.compute_metrics \
  --artifact experiments/results/artifacts_e1_preflight \
  --metrics action_executability,convergence_rate,mean_iters_to_converge,per_iter_visual_gain,d1_latency,d2_cost,d3_failure_rate \
  --papers-manifest experiments/configs/papers_5.json \
  --out experiments/results/metrics_e1_preflight

python -m experiments.scripts.aggregate_stats \
  --metrics-dir experiments/results/metrics_e1_preflight \
  --out experiments/results/aggregate_e1_preflight \
  --reference ours_svfp
```

Run one E2 cross-planner preflight:

```bash
POSTER_LLM_TIMEOUT_S=10 \
POSTER_LLM_MAX_RETRIES=1 \
POSTER_VLM_ALLOW_FALLBACK=0 \
POSTER_VLM_WALL_TIMEOUT_S=12 \
python -m experiments.scripts.run_one_paper \
  --paper experiments/datasets/papers/多模态预训练_4DE028.pdf \
  --baseline gpt4o_zeroshot_svfp \
  --baselines-yaml experiments/configs/e1_smoke_baselines.yaml \
  --out experiments/results/artifacts_e2_preflight \
  --timeout 90
```

## Official Matrix Template

Run only after preflight gates pass.

```bash
POSTER_LLM_TIMEOUT_S=10 \
POSTER_LLM_MAX_RETRIES=1 \
POSTER_VLM_ALLOW_FALLBACK=0 \
POSTER_VLM_WALL_TIMEOUT_S=12 \
python -m experiments.scripts.run_matrix \
  --papers experiments/configs/papers_30.json \
  --baselines ours_no_svfp,ours_freeform,ours_svfp,gpt4o_zeroshot_svfp \
  --baselines-yaml experiments/configs/baselines.yaml \
  --out experiments/results/artifacts \
  --workers 1 \
  --timeout 900

python -m experiments.scripts.compute_metrics \
  --all \
  --metrics all \
  --papers-manifest experiments/configs/papers_30.json \
  --out experiments/results/metrics

python -m experiments.scripts.aggregate_stats \
  --metrics-dir experiments/results/metrics \
  --out experiments/results/aggregate \
  --reference ours_svfp
```

## Metric Policy

| Scope | Metrics | Notes |
|---|---|---|
| Headline content | `a1`, `a2`, `a3` | A3 uses contradiction-only hallucination. |
| Headline visual | `b1`, `b2` | Needs independent/human validation before final claim strength. |
| Headline protocol | `action_executability`, `convergence_rate`, `mean_iters_to_converge`, `per_iter_visual_gain` | E1 main evidence. |
| Headline engineering | `d1`, `d2`, `d3` | Use Pareto framing, not "fast" claims. |
| Appendix sanity | `a4`, current `c1` | Kept for reproducibility, excluded from headline. |
| Pending data | `b3`, `c2`, `c3` | Require expert/user-study CSVs. |

Do not delete metric files just because they are appendix or pending. Use
`experiments/configs/metrics.yaml` and table scripts to control reporting.

## Instrumentation

`POSTER_EXPERIMENT_MODE=1` makes baselines write per-cell
`experiment_log.jsonl`. `d1_latency` and `d2_cost` read that log directly.

Recommended latency guards:

```bash
export POSTER_LLM_TIMEOUT_S=10
export POSTER_LLM_MAX_RETRIES=1
export POSTER_VLM_ALLOW_FALLBACK=0
export POSTER_VLM_WALL_TIMEOUT_S=12
```

## Statistical Analysis

`aggregate_stats.py` reports:

- mean + bootstrap CI per metric/baseline
- Wilcoxon signed-rank pairwise tests vs reference
- Bonferroni and BH-FDR fields
- Cohen's d and rank-biserial r

At n=5, treat all inferential stats as smoke/trend only. Official claims need
n=30 plus independent visual validation.
