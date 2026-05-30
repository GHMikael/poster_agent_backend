[English](README.md) | **简体中文**

# PosterCSP — Paper-to-Poster Backend

> **当前版本：v5.2** · FastAPI 后端 + **SVFP**（Structured Visual Feedback Protocol，结构化视觉反馈协议）+ 可复现的 **CS-Poster-30** 评测框架。

给定一篇 CS 论文 PDF，系统通过 Dify **Chatflow**（内容规划）与 Python 渲染器（可选 **SVFP 闭环**：VLM 批评 → 确定性修复 → 收敛留痕）生成可编辑的 A3 学术海报 PPTX。长耗时任务采用 **异步 HTTP + 服务端长轮询**，适配 Dify。

**研究方向（v2）：** 一主（**SVFP 协议**）两从（**CS-Poster-30 基准** + **CS 垂直实例化**）。5 篇试点数据后的方向修正详见 [`RESEARCH_DIRECTION_v2.md`](RESEARCH_DIRECTION_v2.md)。

---

## 项目定位（能说什么 / 不能说什么）

| | |
|---|---|
| **是** | 一种 **planner-agnostic** 的结构化视觉反馈协议（4 类 issue × 9 个原子动作），可挂在任意 poster planner 之后 |
| **是** | 可复现的 **CS-Poster-30** 流水线（30 份冻结规划快照、12 项指标套件、L0→L8 脚本） |
| **不是** | 「结构化规划在内容召回上优于 zero-shot」（试点：a1 低于 gpt4o_zeroshot） |
| **不是** | 「轻量高效系统」（试点：SVFP ~160 s vs 无闭环 38 ms vs zero-shot 23 s） |
| **不是** | 「100% 原图复用」的已证结论（图 pipeline 审计 B1 进行中） |

**一句话定位（论文用）：**

> 我们提出 **SVFP**——将 VLM 视觉批评约束到 `{4 类问题 × 9 个动作}` 封闭 schema，并由确定性 `FeedbackApplier` 执行，使反馈可执行、可收敛。在 CS-Poster-30 上，SVFP 在视觉质量（B1/B2，n=5 时 Cohen's d ≈ 1.8）取得大效应提升，并诚实报告内容 precision–recall trade-off。

---

## 版本概览（v5.2）

| 模块 | 能力 |
|------|------|
| **SVFP 协议** | 4 类 root-cause issue × 9 确定性 action；FSM 式收敛；**仅改排版、不改 bullet 文本**（设计决定） |
| **E1 基线** | `ours_freeform` — 自由文本 VLM 批评 + LLM best-effort 改写（closed-set vs free-form 对照臂） |
| **A3 修复** | NLI 幻觉：中立/弃权不再误判为幻觉；拆分 `contradicted_rate` 与 `unsupported_rate` |
| **图污染体检** | `audit_figures.py` — VLM 对齐扫描 planner_cache 中的错图（B1 诊断） |
| **Planner 缓存** | 30 份冻结 `PosterTask`；错图引用清理；`clean_planner_cache.py` |
| **Dify Chatflow** | 三 Agent 流水线；Prompt 见 `dify/prompts/` |
| **批跑** | `batch_dify_runs.py` 通过 API 批量触发 Chatflow |
| **渲染器** | 4 模板 × 4 主题；六模块 CS domain prior；异步 Job + 运行归档 |
| **实验** | 基线：`ours_svfp` · `ours_no_svfp` · `ours_freeform` · `gpt4o_zeroshot` · 外部 SOTA（可选） |

**演进主线**

- **v4.1**：SVFP 协议、异步 Job、布局质量守卫
- **v5.0**：实验框架、5 篇试点、JSONL 遥测
- **v5.1**：Dify 批跑、30 份 planner 快照、L0→L8 文档
- **v5.2**：研究重锚（PosterCSP / SVFP 脊柱）、E1 free-form 基线、A3 指标修复、图审计 + planner 清理

---

## 试点结论（n=5，诚实摘要）

来自 15 个 metrics JSON（5 篇 × 3 方法）的均值。**n=5 下 BH-FDR 校正后无一显著**——仅作方向性参考。

| 类别 | 指标 | gpt4o_zeroshot | ours_no_svfp | ours_svfp | 解读 |
|------|------|----------------|--------------|-----------|------|
| 内容 | A1 信息保留 | **0.544** | 0.448 | 0.448 | 结构化规划牺牲召回 |
| 内容 | A3 幻觉率 | 0.117 | **0.100** | 0.117 | 无明显赢家（v5.2 已修 A3 逻辑） |
| 视觉 | B1 布局 | 0.745 | 0.766 | **0.781** | SVFP 最清晰赢点 |
| 视觉 | B2 可读性 | 0.748 | 0.748 | **0.782** | 同 B1 模式 |
| 工程 | D1 延迟 (ms) | 23,025 | **38** | 160,612 | 质量–延迟 trade-off |
| 工程 | D2 成本 ($) | **0.004** | 0 | 0.012 | 多轮 VLM 成本 |

**设计事实：** `ours_svfp` 与 `ours_no_svfp` 唯一区别是 `use_commenter`，所有修复**只改排版**，故二者在内容指标上逐篇完全相同——这是设计决定，不是 bug。

**待做实验：** E1 三臂（无反馈 / 自由反馈 / SVFP）、E4 n=30 矩阵、B1 图 pipeline 修复。完整 backlog 见 [`RESEARCH_DIRECTION_v2.md`](RESEARCH_DIRECTION_v2.md)。

---

## 架构

```mermaid
flowchart TB
  PDF[PDF 论文] --> Extract["/extract_pdf_assets"]
  Extract --> Dify[Dify Chatflow<br/>Text / Visual / Planner]
  Dify --> Task[PosterTask JSON]
  Task --> Render[PPTX 渲染器]
  Render --> SVFP{SVFP 闭环?}
  SVFP -->|是| VLM[VLM 封闭 schema 批评]
  VLM --> Apply[FeedbackApplier]
  Apply --> Render
  SVFP -->|否| Out[final.pptx + run_report]
  Render --> Out
  Dify -.-> Cache[planner_cache/]
  Cache -.->|run_matrix 回放| Task
```

1. **`/extract_pdf_assets`** — 文本预览 + 插图元数据。
2. **Dify Chatflow** — 三 Agent 输出 `PosterTask` JSON。
3. **渲染器 + 可选 SVFP** — 确定性布局修复闭环。
4. **实验** — 回放冻结规划，各基线在**相同规划**上对比。

---

## 项目结构

```
poster_agent_backend/
├── app/                         # 生产 FastAPI + SVFP + 渲染器
├── dify/                        # Chatflow 设计与 Agent Prompt
├── experiments/
│   ├── baselines/               # ours_svfp, ours_no_svfp, ours_freeform, …
│   ├── metrics/                 # A1–A4, B1–B3, C1–C3, D1–D3
│   ├── scripts/                 # batch_dify_runs, run_matrix, audit_figures, …
│   └── datasets/planner_cache/  # 30 份冻结 PosterTask 快照
├── RESEARCH_DIRECTION_v2.md     # 研究方向与实验 backlog（必读）
├── INTERNAL_EXPERIMENT_GUIDE.md # L0→L8 逐步操作手册
└── .env.example
```

---

## 快速开始

```bash
cd poster_agent_backend
python3.12 -m venv .venv312 && source .venv312/bin/activate
pip install -r requirements.txt
cp .env.example .env          # 填写 DASHSCOPE_API_KEY、DIFY_*（批跑时）
python -m app.main
curl http://127.0.0.1:8000/health
```

---

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/health` | 服务状态 |
| `POST` | `/extract_pdf_assets` | PDF → `asset_token` + 插图 URL |
| `POST` | `/generate_ppt` | 异步生成（202 + `job_id`） |
| `GET` | `/jobs/{job_id}?wait=20` | 长轮询任务状态 |
| `POST` | `/generate_ppt_file` | 同步生成（调试） |
| `GET` | `/download/run/{run_folder}` | 下载 `final.pptx` |
| `GET` | `/assets/{asset_token}/{filename}` | 提取的插图 |

---

## SVFP 协议

在 Planner JSON 中开启：

```json
{ "use_commenter": true, "max_iterations": 3 }
```

| Issue | 典型确定性修复 |
|-------|----------------|
| `overlapping_elements` | 减少 bullet、缩小字号 |
| `empty_space` | 放大字号、重平衡留白 |
| `low_contrast` | 切换配色（2 色守卫） |
| `figure_too_small` | 纵向面板 → `image_focus` |

单次运行分析：

```bash
python -m experiments.tools.run_analysis outputs/runs/<run_folder>/run_report.json
```

---

## 实验

**基线对照**

| 名称 | 隔离变量 |
|------|----------|
| `ours_svfp` | 完整 SVFP 闭环 |
| `ours_no_svfp` | 同渲染器、无反馈（布局消融） |
| `ours_freeform` | 自由文本 VLM 批评 + LLM 改写（E1 臂） |
| `gpt4o_zeroshot` | 仅 LLM 规划，同渲染器与同模板 |

**完整矩阵（本地）**

```bash
python -m experiments.scripts.run_matrix \
  --papers experiments/configs/papers_30.json \
  --baselines ours_svfp,ours_no_svfp,ours_freeform,gpt4o_zeroshot
python -m experiments.scripts.compute_metrics --all
python -m experiments.scripts.aggregate_stats --out experiments/results/aggregate/
python -m experiments.scripts.print_paper_table
```

**图污染体检（B1 诊断）**

```bash
python experiments/scripts/audit_figures.py --dry-run   # 不调 API
python experiments/scripts/audit_figures.py --limit 3   # 小规模验证
```

详见 [`experiments/README.md`](experiments/README.md) · [`INTERNAL_EXPERIMENT_GUIDE.md`](INTERNAL_EXPERIMENT_GUIDE.md)

---

## 环境变量

| 变量 | 说明 |
|------|------|
| `DASHSCOPE_API_KEY` | Qwen-VL 评审 + Judge |
| `OPENAI_API_KEY` | 指标 Judge（OpenAI 兼容） |
| `POSTER_EXPERIMENT_MODE` | `1` = 每次运行写 JSONL 遥测 |
| `DIFY_API_KEY` / `DIFY_BASE_URL` | 批量触发 Chatflow |
| `DIFY_WORKFLOW_INPUT_NAME` | Start 节点 PDF 变量名（默认 `paper`） |

完整列表见 [`.env.example`](.env.example)。

---

## 测试

```bash
python -m pytest tests/ -q
python -m pytest experiments/tests/ -q
```

---

## 文档地图

| 文档 | 读者 | 内容 |
|------|------|------|
| **README**（本文） | 新克隆者 | 概览、快速开始、诚实试点摘要 |
| [`RESEARCH_DIRECTION_v2.md`](RESEARCH_DIRECTION_v2.md) | 论文作者 | 定位、指标 v2、实验 backlog |
| [`INTERNAL_EXPERIMENT_GUIDE.md`](INTERNAL_EXPERIMENT_GUIDE.md) | 操作者 | L0→L8 命令与避坑 |
| [`dify/DIFY_WORKFLOW_AND_PAPER_DESIGN.md`](dify/DIFY_WORKFLOW_AND_PAPER_DESIGN.md) | 方法章节 | Chatflow 拓扑与 Agent 设计 |

---

## GitHub 说明

**不会提交：** `.env`、`outputs/`、PDF、`experiments/.cache/`、metrics/aggregate/artifacts、`PAPER_DRAFT_v0.md`、内部对话记录。

**会提交：** 源码、`dify/prompts/`、`planner_cache/`（30 份快照）、`RESEARCH_DIRECTION*.md`、configs、tests。
