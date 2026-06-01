# PosterCSP 技术与论文优化方向参考文档 v3

**版本**：v3.0  
**日期**：2026-05-31  
**适用范围**：v2 技术改革后的代码基线审计、最终 n=30 实验前的稳定性判断、下一轮优化规划  
**当前代码基线**：v5.3（E1/E2 preflight 链路已实现；正式 n=30 尚未跑）

> v2 的作用是“把方向从保真系统重锚为 SVFP 方法论”。  
> v3 的作用是“确认技术改革进度、规定正式实验前的操作边界、列出剩余可优化点”。

---

## 0. 一句话结论

**核心技术改革已基本完成，可以进入正式 n=30 前的最后 smoke 阶段；但论文级证据链还没完成。**

具体说：

- 可以说：SVFP/free-form/no-feedback 三臂链路已跑通；协议级指标可聚合；cross-planner baseline 已实现；VLM 延迟有硬边界；图 pipeline 已从机械抽图升级为过滤+审计。
- 不能说：n=30 已完成、视觉质量已由独立人评背书、外部 SOTA 已复现、原图复用率已正式量化。

---

## 1. v2 Backlog 完成状态

| 编号 | 事项 | 当前状态 | 说明 |
|---|---|---|---|
| B1 | 修图复用 pipeline | **基本完成，仍需正式 audit** | 已加 PDF 抽图过滤、bbox/xref 元数据、坏图清洗；还没有把 Figure Reuse Rate 作为正式 metric。 |
| B2 | 修 a3 NLI 弃权误判 | **完成** | neutral/abstain 不再算 hallucination；拆出 unsupported/contradicted。 |
| B3 | 删/降天花板指标 | **完成** | a4、当前 c1 appendix-only；保留实现用于复现和 sanity。 |
| B4 | 延迟优化 | **完成第一阶段** | VLM timeout、hard wall timeout、禁 SDK 隐式重试、禁默认 fallback、收敛参数可配。未做 soffice 并行/缓存。 |
| B5 | free-form baseline | **完成** | free-form 失败记录为不可执行，不 crash；阶段日志可诊断。 |
| B6 | content-preserving repair | **部分完成** | `reduce_bullet_count` 合并尾部 bullets；`truncate_bullets` 仍是截断。 |
| E1 | 三臂对照 | **smoke 完成，正式未跑** | 5 篇 × 3 臂跑通；协议指标可聚合。 |
| E2 | cross-planner | **baseline 完成，preflight 完成** | 新增 `gpt4o_zeroshot_svfp`。 |
| E3 | per-issue ablation | **未完成** | 仍需逐类关闭 issue。 |
| E4 | n=30 | **manifest 完成，正式未跑** | `papers_30.json` 已有；未跑正式矩阵。 |
| E5 | human eval | **未完成** | b1/b2 去循环化、人评、SUS/time saving 都待做。 |

---

## 2. 当前代码架构状态

### 2.1 主生成路径

```text
PDF
  -> /extract_pdf_assets
      - text_preview
      - filtered figures with bbox/xref metadata
  -> Dify Chatflow / cached PosterTask
  -> generate_dashboard_pptx
  -> optional SVFP loop
      - render PNG
      - VLM closed-schema critique
      - deterministic FeedbackApplier
      - ConvergenceDetector
  -> final PPTX + run_report + experiment_log
```

### 2.2 实验路径

```text
planner_cache + papers_5/papers_30
  -> run_matrix
  -> artifacts_<scope>/<baseline>_<paper>/
  -> compute_metrics
  -> aggregate_stats
  -> print_paper_table / plot_figures
```

### 2.3 当前 baseline

| Baseline | 角色 |
|---|---|
| `ours_no_svfp` | 无反馈渲染，隔离 SVFP 贡献。 |
| `ours_freeform` | 自由文本反馈 + LLM best-effort apply，E1 对照臂。 |
| `ours_svfp` | Dify/cached planner + closed-set SVFP。 |
| `gpt4o_zeroshot` | zero-shot planner + same renderer。 |
| `gpt4o_zeroshot_svfp` | zero-shot planner + SVFP，E2 cross-planner。 |
| `paper2poster` / `posteragent` | 外部 SOTA placeholder，尚未正式复现。 |

---

## 3. 指标体系 v3

### 3.1 Headline 自动指标

| Cluster | Metrics | 说明 |
|---|---|---|
| Content | `a1_information_retention`, `a2_figure_text_alignment`, `a3_hallucination` | a3 已修复；a2 需配合 figure audit 解释。 |
| Visual | `b1_layout_rationality`, `b2_readability` | 可做主表，但最终论文应补独立 VLM 或人评相关性。 |
| Protocol | `action_executability`, `convergence_rate`, `mean_iters_to_converge`, `per_iter_visual_gain` | E1 的主战场。 |
| Engineering | `d1_latency`, `d2_cost`, `d3_failure_rate` | 用 Pareto/trade-off frame，不声称轻量。 |

### 3.2 Appendix / Pending 指标

| Metrics | 状态 | 不删除原因 |
|---|---|---|
| `a4_section_coverage` | appendix | 模板强制，没区分度，但可作为 sanity。 |
| 当前 `c1_paperquiz` | appendix | 5-MCQ 天花板，重做前不 headline。 |
| `b3_academic_compliance` | pending human/expert | 需要 expert_ratings.csv。 |
| `c2_sus_likert`, `c3_time_saving` | pending human | 需要 user-study CSV。 |

**结论：不要删除这些 metric 文件。**  
删除会破坏历史 pilot 复现、appendix 表、以及未来人评入口。正确做法是通过 `metrics.yaml` 的 `report_scope` 和表格脚本控制呈现。

---

## 4. n=30 前必须通过的 Gate

### Gate A：测试

```bash
python -m pytest experiments/tests tests
```

当前期望：全部通过（最近一次为 `86 passed`）。

### Gate B：图审计

```bash
python -m experiments.scripts.audit_figures
python -m experiments.scripts.clean_planner_cache
```

接受标准：

- `broken = 0`
- `missing_image_file = 0`
- clean dry-run 显示 `0 unsafe figures`

### Gate C：E1 smoke

5 篇 × 3 臂：

```text
ours_no_svfp / ours_freeform / ours_svfp
```

接受标准：

- 15 个 metadata，全 `exit_code=0`
- free-form 失败被记录为 `action_executability=0`，不是 cell failure
- SVFP 有 feedback 时 `action_executability=1`
- D1 不出现无限等待

### Gate D：E2 smoke

至少 1 篇：

```text
gpt4o_zeroshot_svfp
```

接受标准：

- `exit_code=0`
- `metadata.config.cross_planner = true`
- VLM 阶段被 `POSTER_VLM_WALL_TIMEOUT_S` 约束

---

## 5. 现在还值得做的技术优化

按“正式 n=30 前是否值得做”排序：

| 优先级 | 优化 | 原因 | 建议 |
|---|---|---|---|
| P0 | Figure Reuse Rate metric | v2 的图复用从卖点降为诚实维度，但还没正式 metric | 新增 `figure_reuse_rate`，统计 panel 引用有效原图数 / 可用原图数。 |
| P0 | E3 per-issue ablation | Reviewer 很可能问 4 类 issue 哪个有贡献 | 新增 config 控制 disabled issues，跑小规模后再 n=30。 |
| P1 | b1/b2 去循环化 | 视觉指标容易被质疑自评 | 加独立 VLM judge 或 human correlation。 |
| P1 | `truncate_bullets` 内容保留 | B6 只修了 reduce，truncate 仍可能损内容 | 改为规则压缩或 LLM-free 摘要合并。 |
| P1 | VLM 成功率优化 | timeout 后常降级 heuristic，影响 SVFP 质量 | 缩短 prompt、减少输出 tokens、缓存 PNG hash 对应反馈。 |
| P2 | soffice 渲染缓存 | 多 baseline 重复渲染成本高 | 基于 task hash 缓存 PNG/PPTX。 |
| P2 | 外部 SOTA | 投稿增强 | paper2poster/posteragent 复现难度高，可作为 stretch。 |
| P2 | PaperQuiz 重做 | 当前 c1 天花板 | 10+ harder grounded QA，再考虑回主表。 |

---

## 6. 推荐下一步执行顺序

不直接跑 n=30，先做：

1. 新增 `figure_reuse_rate` metric。
2. 新增 E3 per-issue ablation 配置和 baseline runner。
3. 用 5 篇 smoke 跑 `ours_svfp` + E3 ablations。
4. 如果 E3 正常，再跑 n=30 第一批：`ours_no_svfp / ours_freeform / ours_svfp / gpt4o_zeroshot_svfp`。
5. n=30 后再决定是否投入 human eval / external SOTA。

---

## 7. 论文写作边界

可以写：

- SVFP makes VLM feedback executable through closed actions.
- Free-form critique often fails at the apply stage; this is quantified by action executability.
- SVFP is planner-agnostic in implementation and has a cross-planner baseline path.
- Content recall trade-off is reported honestly.
- Latency is bounded and reported as quality-latency trade-off.

不能写：

- “SVFP improves content fidelity.”
- “The system is lightweight/fast.”
- “100% original figure reuse.”
- “Visual quality is objectively proven” before independent validation.
- “n=30 significant results” before official matrix is actually run.

---

## 8. 与文档的关系

| 文档 | 角色 |
|---|---|
| `RESEARCH_DIRECTION.md` | 历史 v1。 |
| `RESEARCH_DIRECTION_v2.md` | 方向重锚和原始改革 backlog。 |
| `RESEARCH_DIRECTION_v3.md` | 当前代码状态、剩余优化和 n=30 前 gate。 |
| `INTERNAL_EXPERIMENT_GUIDE.md` | 操作手册，按 v3 更新。 |
| `README.md` / `README.zh-CN.md` | 对外项目说明，按 v5.3 更新。 |

---

## 9. 最终判断

**技术改革不是“所有研究工作完成”，但作为正式 n=30 前的工程基线，已经基本够用。**

下一步最有性价比的不是继续大改主系统，而是补两个小但关键的实验能力：

1. `figure_reuse_rate`
2. E3 per-issue ablation

这两个补完后，再跑正式 n=30 会更稳，也更容易写出 reviewer 能接受的实验分析。
