# INTERNAL_EXPERIMENT_GUIDE

> 当前 PosterCSP/SVFP 代码库的内部实验操作手册。
> 本文档已按 v5.3 / `RESEARCH_DIRECTION_v3.md` 对齐：先完成技术改革，
> 再做 preflight，最后才跑正式 n=30。

所有命令默认在仓库根目录执行：

```bash
cd /Users/mikaelsnow/Documents/ECNU/Paper_Comment_Poster/poster_agent_backend
source .venv312/bin/activate
```

---

## 0. 当前基线状态

项目已经不再是 v2 阶段的“先修技术再跑 E1”状态。以下改革已经落到代码中：

| 模块 | 当前状态 |
|---|---|
| B1 图复用 pipeline | PDF 抽图已过滤低信息图片，并记录 `source_xref` / `bbox` / `extraction_note`；planner cache 中的不安全图引用已清理。 |
| B2 A3 hallucination | neutral / abstain 不再误算为 hallucination；已拆出 `unsupported_rate` 和 `contradicted_rate`。 |
| B3 天花板指标 | `a4_section_coverage` 和当前 `c1_paperquiz` 已降为 appendix-only。 |
| B4 延迟控制 | VLM hard timeout、SDK 隐式重试关闭、收敛参数可配置。 |
| B5 / E1 | `ours_freeform` 已作为“不崩溃的失败模式 baseline”跑通；protocol metrics 可聚合。 |
| B6 内容保留修复 | `reduce_bullet_count` 现在会合并尾部 bullet，而不是静默丢内容。 |
| E2 cross-planner | `gpt4o_zeroshot_svfp` 已实现，用于验证 SVFP 不只依赖单一 planner。 |
| 视觉 preflight | `visual_smoke_check` 已新增，用于在 n=30 前自动发现文本溢出、省略过多和顶部重叠。 |
| 图复用指标 | `figure_reuse_rate` 已新增，用于量化有效原图被 panel 复用的比例。 |

不要因为旧指标不是主表指标就删除 `experiments/metrics/` 下的实现。它们还承担 pilot 复现、appendix sanity、人评入口的作用。当前做法是通过 `experiments/configs/metrics.yaml` 里的 `report_scope` 和表格脚本控制是否进入主表。

---

## 1. 环境准备

先确认核心依赖可用：

```bash
python -c "import fitz, openai, yaml, scipy, matplotlib; print('deps ok')"
```

推荐的实验环境变量：

```bash
export POSTER_EXPERIMENT_MODE=1
export POSTER_LLM_TIMEOUT_S=10
export POSTER_LLM_MAX_RETRIES=1
export POSTER_VLM_ALLOW_FALLBACK=0
export POSTER_VLM_WALL_TIMEOUT_S=12
export POSTER_CONVERGENCE_PATIENCE=1
```

| 变量 | 含义 |
|---|---|
| `POSTER_LLM_TIMEOUT_S` | 文本 LLM / VLM SDK 请求超时时间。 |
| `POSTER_LLM_MAX_RETRIES` | 实验 LLM client 的显式重试次数。 |
| `POSTER_VLM_ALLOW_FALLBACK` | 设为 `0` 时，不再触发第二次非 JSON 模式的长 VLM fallback。 |
| `POSTER_VLM_WALL_TIMEOUT_S` | SVFP VLM review 的总墙钟时间上限。 |
| `POSTER_CONVERGENCE_*` | `ConvergenceDetector` 的运行时配置。 |

如果需要 LibreOffice 渲染，但本地 sandbox 阻止 `soffice`，需要在 sandbox 外或通过授权运行。

---

## 2. 数据与产物层级

| 层级 | 路径 | 作用 |
|---|---|---|
| L0 | `experiments/datasets/papers/*.pdf` | 原始候选论文 PDF。 |
| L1 | `outputs/runs/*/input.json` | Dify 生成的 `PosterTask` 快照。 |
| L2 | `experiments/datasets/planner_cache/*.json` | ours 系列 baseline 使用的冻结 plan。 |
| L3 | `experiments/configs/papers_5.json`, `papers_30.json` | 论文清单。 |
| L4 | `experiments/results/artifacts*/<baseline>_<paper>/` | PPTX、PNG、metadata、panels、日志。 |
| L5 | `experiments/results/metrics*/<baseline>_<paper>.json` | 单个实验 cell 的指标。 |
| L6 | `experiments/results/aggregate*/` | 聚合结果与 pairwise 统计表。 |

preflight 和正式实验必须使用不同输出目录：

| 用途 | Artifacts | Metrics | Aggregate |
|---|---|---|---|
| E1 smoke | `artifacts_e1_preflight` | `metrics_e1_preflight` | `aggregate_e1_preflight` |
| E2 smoke | `artifacts_e2_preflight` | `metrics_e2_preflight` | 可选 |
| 正式 n=30 | `artifacts` | `metrics` | `aggregate` |

不要把 preflight 产物混进正式 `experiments/results/artifacts`。

---

## 3. n=30 前的 Preflight Gate

### 3.1 单元测试和 smoke 测试

```bash
python -m pytest experiments/tests tests
```

当前期望：全部通过。本文档更新时最近一次完整相关测试结果为 `86 passed`。

### 3.2 图 pipeline 审计

```bash
python -m experiments.scripts.audit_figures --dry-run
python -m experiments.scripts.audit_figures
python -m experiments.scripts.clean_planner_cache
```

验收标准：

- `broken = 0`
- `missing_image_file = 0`
- `clean_planner_cache` dry-run 显示 `0 unsafe figures`

抽图器现在能过滤明显垃圾图，但仍可能存在语义错配。因此正式矩阵前仍要保留图审计。

### 3.3 E1 Smoke：三臂协议对照

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

随后计算和聚合指标：

```bash
python -m experiments.scripts.compute_metrics \
  --artifact experiments/results/artifacts_e1_preflight \
  --metrics action_executability,convergence_rate,mean_iters_to_converge,per_iter_visual_gain,figure_reuse_rate,visual_smoke_check,d1_latency,d2_cost,d3_failure_rate \
  --papers-manifest experiments/configs/papers_5.json \
  --out experiments/results/metrics_e1_preflight

python -m experiments.scripts.aggregate_stats \
  --metrics-dir experiments/results/metrics_e1_preflight \
  --out experiments/results/aggregate_e1_preflight \
  --reference ours_svfp
```

验收标准：

- 5 篇 × 3 臂 = 15 个 cell 都存在，且 `exit_code=0`。
- `ours_freeform` 的不可执行反馈应体现在较低或为 0 的 `action_executability`，而不是程序崩溃。
- `ours_svfp` 在产生 issue 时应能输出可执行 closed-set action。
- `visual_smoke_check` 接近 1，且 `n_likely_overflow=0`、`n_top_overlaps=0`。
- `figure_reuse_rate` 用作诚实报告：为 0 时说明该 poster 没有实际复用原图，不要把图复用写成强 claim。
- D1 延迟不出现无限等待。

### 3.4 E2 Smoke：Cross-Planner 验证

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

验收标准：

- `exit_code=0`
- `metadata.config.cross_planner=true`
- `planner_source` 以 `llm_zeroshot:` 开头
- `experiment_log.jsonl` 显示 VLM 阶段受到墙钟超时限制

---

## 4. 正式 n=30 矩阵

只有所有 preflight gate 通过后，才开始正式 n=30。

推荐的第一版正式矩阵：

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
```

随后计算、聚合、打印论文表：

```bash
python -m experiments.scripts.compute_metrics \
  --all \
  --metrics all \
  --papers-manifest experiments/configs/papers_30.json \
  --out experiments/results/metrics

python -m experiments.scripts.aggregate_stats \
  --metrics-dir experiments/results/metrics \
  --out experiments/results/aggregate \
  --reference ours_svfp

python -m experiments.scripts.print_paper_table
```

只有在本地 LibreOffice 和 API 限流都确认稳定后，才考虑 `--workers > 1`。单 worker 慢一些，但更容易审计和复现。

---

## 5. 指标策略

| 指标组 | ID | 报告策略 |
|---|---|---|
| Content headline | `a1`, `a2`, `a3` | 自动主表。 |
| Visual headline | `b1`, `b2` | 可进自动主表，但最终论文强 claim 前最好补独立 VLM 或人评验证。 |
| Visual support / preflight | `figure_reuse_rate`, `visual_smoke_check` | 图复用诚实统计和 n=30 前成图质量 gate。 |
| Protocol headline | `action_executability`, `convergence_rate`, `mean_iters_to_converge`, `per_iter_visual_gain` | E1 的核心证据。 |
| Engineering headline | `d1`, `d2`, `d3` | 工程/Pareto 表。 |
| Appendix sanity | `a4`, 当前 `c1` | 保留为 sanity / appendix，不进主结论。 |
| Pending human/expert | `b3`, `c2`, `c3` | 需要 CSV 数据后才启用。 |

除非已经迁移历史结果、表格脚本和论文 appendix，否则不要删除 `experiments/metrics/` 下的 metric 文件。更稳妥的方式是在 `metrics.yaml` 中禁用或降级显示。

---

## 6. 常见失败模式

| 现象 | 可能原因 | 处理方式 |
|---|---|---|
| `ProcessPoolExecutor` permission error | macOS sandbox 对 semaphore 的限制 | 使用 `--workers 1`；`run_matrix` 已有顺序执行路径。 |
| `soffice SIGABRT` / 无 PNG | LibreOffice 被 sandbox 或 Gatekeeper 阻止 | 在 sandbox 外或授权后运行；检查 `/Applications/LibreOffice.app/.../soffice`。 |
| VLM 阶段超过 30 秒 | SDK 重试、fallback 或远端阻塞 | 设置 `POSTER_VLM_ALLOW_FALLBACK=0`、`POSTER_VLM_WALL_TIMEOUT_S=12`；代码中已设置 `max_retries=0`。 |
| free-form baseline 超时或应用失败 | E1 中预期会暴露的失败模式 | 应产生 `exit_code=0` 和 `action_executability=0`，不应让 cell crash。 |
| `a2` 跳过 zero-shot | zero-shot plan 没有 `figure_id` 绑定 | 若要公平比较 A2，需要给 zero-shot 增加 figure assignment；否则报告为 not applicable。 |

---

## 7. 写结果前的人工核对

正式写实验结果前，至少手动检查：

```bash
find experiments/results/artifacts -maxdepth 2 -name metadata.json | wc -l
python -m experiments.scripts.audit_figures
sed -n '1,80p' experiments/results/aggregate/aggregate.tsv
sed -n '1,80p' experiments/results/aggregate/pairwise.tsv
```

论文 claim 边界：

- 不要在 n=30 证明前声称 content fidelity 一定优于所有 baseline。
- 不要声称 100% 原图复用；使用实际 figure reuse / audit 统计。
- 不要把系统描述成高效或轻量；更合理的写法是 quality-latency Pareto。
- 不显著的结果只写 trend，不写强结论。
