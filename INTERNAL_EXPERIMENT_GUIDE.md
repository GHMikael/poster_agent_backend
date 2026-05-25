# 个人实验运行指南（INTERNAL_EXPERIMENT_GUIDE）

> 本文是**个人参考**用的实验操作手册，不是 README。目标是让你（以及未来回来翻看的自己 / 协作的 AI）一眼看懂：从一堆 PDF 到论文里的最终数据表，中间到底要按什么顺序运行什么命令、每个脚本扮演什么角色、数据是如何一步步在文件系统里流转的。
>
> 全篇按"线性可执行流程"重新组织，所有命令都假设你已经位于仓库根目录：
> `/Users/mikaelsnow/Documents/ECNU/Paper_Comment_Poster/poster_agent_backend`

---

## 0. 顶层视角：整套实验的因果链

整个实验本质上是把一摞 PDF（`experiments/datasets/papers/*.pdf`）变成一张论文里能贴的数据表（`experiments/results/aggregate/aggregate.tsv`）。中间共有 **9 个层级**的产物，每个层级都是下一个层级的输入，缺一不可。

```
[L0] 原始 PDF                experiments/datasets/papers/*.pdf
        │
        ▼  Dify Chatflow（PaperParse + Planner Agent，详见 dify/DIFY_WORKFLOW_AND_PAPER_DESIGN.md）
[L1] PosterTask JSON 草稿     outputs/runs/<timestamp>_<slug>_<runid>/input.json
        │
        ▼  import_dify_runs.py（按首页文本匹配 PDF stem）
[L2] Planner 缓存             experiments/datasets/planner_cache/<stem>.json
        │
        ▼  手工脚本（papers_30 构建器）
[L3] Paper Manifest           experiments/configs/papers_30.json
        │
        ▼  run_matrix.py（fan-out 30 × 3 cells）
[L4] 每 cell 的渲染产物         experiments/results/artifacts/<baseline>_<stem>/poster.pptx
                              ……同目录还有 metadata.json、panels.json、experiment_log.jsonl、poster.png
        │
        ▼  compute_metrics.py（13 个 metric × 90 cells）
[L5] 每 cell 的 metric 分数      experiments/results/metrics/<baseline>_<stem>.json
        │
        ▼  aggregate_stats.py（bootstrap CI + Wilcoxon + Cohen's d）
[L6] 汇总表 + 配对显著性          experiments/results/aggregate/{aggregate,pairwise}.tsv
        │
        ▼  plot_figures.py
[L7] 论文图（PDF/PNG）           experiments/results/figures/fig0{1..5}*.pdf
        │
        ▼  print_paper_table.py
[L8] 论文 Markdown 表（stdout）   贴进 PAPER_DRAFT_v0.md
```

**记住一个原则**：任何一步失败都不要继续往下走——下游脚本会对上游缺失的产物做静默 fallback，最后表里数据会"看起来正常但其实是空的"。每步跑完都按"预期产物"一节去核对文件。

---

## 1. 环境初始化（只做一次，或换机重做）

### 1.1 激活虚拟环境

```bash
cd /Users/mikaelsnow/Documents/ECNU/Paper_Comment_Poster/poster_agent_backend
source .venv312/bin/activate
python -c "import requests, fitz, openai, yaml, scipy, matplotlib; print('deps ok')"
```

- **作用**：所有脚本默认在 `.venv312` 下跑。Python 3.12 是必须的（部分依赖 3.10 跑不动）。
- **避坑**：如果 `fitz` 报错 → `pip install pymupdf`；如果 `scipy` 报错 → 直接 `pip install -r requirements.txt && pip install -r experiments/requirements.txt`。

### 1.2 填写 `.env`

`.env.example` 是模板，复制成 `.env` 后填密钥：

```bash
cp .env.example .env   # 仅在 .env 不存在时
```

`.env` 至少需要这些字段（不要加引号，等号两侧不要有空格）：

```bash
PORT=8000
OUTPUT_DIR=outputs

# —— 后端用 ——
DASHSCOPE_API_KEY=sk-xxx       # Qwen-VL critic（VLM 评图）+ 部分 metric judge
OPENAI_API_KEY=sk-xxx          # metric judge 用（A1/A3/C1 走 OpenAI 兼容协议）

# —— Dify 批跑用，仅当跑 batch_dify_runs.py 时需要 ——
DIFY_API_KEY=app-xxx           # Chatflow 的 Access API key（以 app- 开头）
DIFY_BASE_URL=http://localhost/v1
DIFY_WORKFLOW_INPUT_NAME=paper # Chatflow Start 节点的变量名
DIFY_USER_ID=experiment-batch
DIFY_QUERY=Generate a conference-style poster from this paper.

# —— 实验遥测，run_matrix 会把每个 cell 各自的 log 注入 ——
POSTER_EXPERIMENT_MODE=1
```

- **避坑 1**：`DIFY_WORKFLOW_INPUT_NAME` 必须等于你在 Dify Chatflow Start 节点里设的输入变量名。当前是 `paper`，但凡 Start 节点改名，这里必须同步改。
- **避坑 2**：Dify Chatflow 的 Start 节点把 `paper` 配为 "Other file types"，所以 `batch_dify_runs.py` 上传时用的 `"type": "custom"`，**不是** `"document"`。这个已经在代码里写死，不需要改，但如果 Start 节点改成了 "Document"，则必须把代码里的 `"custom"` 也改回去。
- **避坑 3**：自建 Dify 在 macOS 上必须用 `host.docker.internal` 让 Dify 容器访问宿主机 FastAPI；线上 Dify 则需要 `ngrok` 或 `cloudflared` 隧道。

### 1.3 验证 Dify 能连通

```bash
curl -s -X POST http://localhost/v1/files/upload \
    -H "Authorization: Bearer $(grep '^DIFY_API_KEY=' .env | cut -d= -f2)" \
    -F "file=@experiments/datasets/papers/RAG_0816F4.pdf;type=application/pdf" \
    -F "user=experiment-batch"
```

- **预期**：返回 JSON，包含 `"id": "<uuid>"`。
- **失败处理**：401 → API Key 不对；Connection refused → Dify 没启动；408/超时 → 隧道断了。

---

## 2. 启动后端（实验过程中保持长开）

```bash
python -m app.main
# → INFO: Uvicorn running on http://0.0.0.0:8000
```

- **作用**：拉起 FastAPI（`app/main.py`），提供四个核心端点：
  - `POST /extract_pdf_assets` — PDF → text_preview + figures 轻量元数据（Dify 调）
  - `POST /generate_ppt` — PosterTask JSON → 异步 job（Dify 调）
  - `GET /jobs/{job_id}` — 轮询 job 状态（Dify 调）
  - `GET /download/run/{run_folder}` — 下载最终 PPTX
- **输入**：HTTP 请求。
- **输出**：每完成一个 PosterTask 就在 `outputs/runs/<timestamp>_<slug>_<runid>/` 写入 `input.json`（**这是 L1 产物，是 Planner 缓存的源头**）、`final.pptx`、`run_report.json`、`experiment_log.jsonl`。
- **位置**：在 L0 → L1 之间，是 Dify Chatflow 内部 HTTP 节点的回调目标。
- **避坑**：另开一个 terminal 跑 `curl http://127.0.0.1:8000/health` 确认 `{"status":"ok"}` 才能继续。后续所有步骤都假设这个进程在跑，**不要 Ctrl-C**。

---

## 3. 数据集准备：从 PDF 到 Manifest

### 3.1 把 PDF 丢进数据集目录

```bash
ls experiments/datasets/papers/*.pdf | wc -l
# 当前: 64 篇候选
```

- **作用**：所有 PDF 必须以 `<arxiv_id_or_slug>.pdf` 的形式放在这里。文件名的 stem（去后缀）就是 `arxiv_id`，会贯穿到所有下游产物的文件名里。
- **命名约定**：英文论文用 `<word>_<HEX6>.pdf`（例如 `RAG_0816F4.pdf`），中文论文用 `<中文短语>_<HEX6>.pdf`。注意 stem 里别带空格——`batch_dify_runs.py` 选 PDF 是按 `Path.glob("*.pdf")`，能容忍空格，但 `run_matrix.py` 拼 cell 目录时空格会引发各种麻烦。

### 3.2 `prepare_dataset.py` 的现状

```bash
python -m experiments.datasets.prepare_dataset --dry-run
```

- **作用（设计）**：从 Paper2Poster benchmark + arXiv 拉取 30 篇，按类别（cs.CV / cs.CL / cs.LG 各 10）和页数（6–30）筛选，生成 `papers_30.json`。
- **当前实现状态**：**只生成 stub manifest**——把 `experiments/datasets/papers/` 里现有的 PDF 全部列进 manifest，不做 arXiv 抓取、不做类别平衡，只填 `arxiv_id`、`source_pdf` 等最小字段，其它字段（title、authors、page_count）留空待补。完整逻辑（Semantic Scholar 查询 + 自动下载）是 M3 的 TODO，目前还没落地。
- **输入**：`experiments/datasets/papers/*.pdf`
- **输出**：`experiments/configs/papers_30.json`（**stub 版**）
- **位置**：L0 → L3 的短路，**实际工作流里不直接用这个脚本**，因为它没生成 `poster_title`，下游计算指标会缺字段。线上跑实验用的是 **第 6 步** 的内联 Python 脚本（基于 planner_cache 里已有的 `poster_title` 反向构建 manifest），更可靠。

---

## 4. 跑 Dify Chatflow，批量生成 Planner 草稿

### 4.1 先 dry-run，确认 PDF 列表

```bash
python -m experiments.scripts.batch_dify_runs --limit 23 --skip-cached --dry-run
```

- **作用**：扫描 `papers/`，剔除 `planner_cache/` 里已存在 stem 的 PDF（避免重复跑），按字母序取前 N 个，**只打印不实际调用**。
- **预期输出**：

```
[batch_dify_runs] --skip-cached: pruned 8 already-cached PDFs
[batch_dify_runs] selected 23 PDF(s) from .../experiments/datasets/papers
    1.  LISTENING_907E2D.pdf
    ...
   23. RAG_<...>.pdf
[dry-run] no Dify calls made.
```

- **避坑**：如果列表不是你想要的，做一份白名单：

```bash
cat > experiments/configs/papers_25.txt <<'EOF'
APPLICATIONS_712E26.pdf
LISTENING_907E2D.pdf
# ...
EOF

python -m experiments.scripts.batch_dify_runs \
    --papers-list experiments/configs/papers_25.txt --dry-run
```

### 4.2 实跑

```bash
python -m experiments.scripts.batch_dify_runs --limit 23 --skip-cached \
    2>&1 | tee experiments/results/batch_dify_console.log
```

- **作用**：脚本对每个 PDF 串行执行：① 上传 PDF 到 Dify (`POST /v1/files/upload`)；② 触发 Chatflow (`POST /v1/chat-messages`, streaming 模式)；③ 消费 SSE 流，等 `workflow_finished` 或 `message_end`；④ 写一行记录到 `batch_dify_report.json`。
- **关键侧效应**：Dify Chatflow 内部的 HTTP 节点会回调 FastAPI 的 `/extract_pdf_assets`（一次）→ `/generate_ppt`（一次）→ `/jobs/{job_id}`（轮询），最后落盘到 `outputs/runs/<dir>/input.json`。**这个 input.json 就是 L1 产物**，本身是一个完整的 `PosterTask` JSON。
- **输入**：`experiments/datasets/papers/*.pdf`
- **输出**：
  - `experiments/results/batch_dify_report.json`（每篇跑完即追加，含 status / run_id / duration）
  - `outputs/runs/<timestamp>_<slug>_<runid>/input.json`（**每篇一份**，是 Planner JSON 草稿）
- **位置**：L0 → L1
- **耗时**：单篇约 145s（M3 Mac），23 篇约 50 分钟。
- **避坑清单**：

| 现象 | 原因 | 处置 |
| --- | --- | --- |
| 全部 `[401]` | `DIFY_API_KEY` 失效 | 去 Dify > Access API 重新复制 |
| 全部 `[400]` chat-messages | Start 节点变量名对不上 | 打开 Chatflow 确认变量名，改 `.env` |
| 全部 `[404]` chat-messages | 应用类型是 Workflow 不是 Chatflow | 把 endpoint 切回 `/workflows/run` |
| 流卡死无 `message_end` | Chatflow 内某个 HTTP 节点卡死 | 多半是 FastAPI 挂了，回到第 2 步 |
| 单篇 `TimeoutError exceeded 600s` | 偶发慢 | 单跑：`--max-wait-sec 1200 --papers-list <(echo X.pdf)` |
| `succeeded` 但 `outputs/runs/` 没新目录 | Chatflow 没真正调到 `/generate_ppt` | 去 Dify UI 查 run log，看哪个 HTTP 节点 fail |

- **断点续跑**：脚本每跑完一篇就刷新 `batch_dify_report.json`，崩溃后重启同样命令即可（`--skip-cached` 会保护已 import 的；尚未 import 的会重跑，但 Dify 端是幂等的）。

---

## 5. 把 Planner 草稿导入 planner_cache

```bash
python -m experiments.scripts.import_dify_runs \
    --report-path experiments/results/import_report.json
```

- **作用**：扫描 `outputs/runs/*/input.json`，对每份草稿做 PDF 匹配——读出 `poster_title`，用 PyMuPDF 抽每份 PDF 首页文本，做字符串包含判断（强匹配：标题前 30 字符出现在首页；弱匹配：长度≥5 的关键词命中≥4 个）。强匹配唯一时直接复制 `input.json` 到 `planner_cache/<pdf_stem>.json`。
- **为什么需要这一步**：Dify 的 run folder 是按 `poster_title` slug 命名的，PDF 是按 `<arxiv_id>` 命名的，两者无法通过文件名直接对齐。`import_dify_runs.py` 是这两个命名空间之间的桥。
- **输入**：
  - `outputs/runs/*/input.json`（L1）
  - `experiments/datasets/papers/*.pdf`（L0，用于首页文本匹配）
- **输出**：
  - `experiments/datasets/planner_cache/<pdf_stem>.json`（**L2 产物**）
  - `experiments/results/import_report.json`（每份草稿的匹配结果）
- **位置**：L1 → L2
- **预期输出**：

```
[import_dify_runs] summary:
    import            : 23     # 成功匹配的数量
    ambiguous         : 0      # 一篇 input.json 强匹配多个 PDF
    unmatched         : 0      # 一篇 input.json 找不到匹配 PDF
    error             : 0
    skipped_cached    : 8      # planner_cache 里已存在，跳过
    written           : 23     # 实际写入的新文件数
```

- **避坑**：
  - `ambiguous > 0` → `--interactive` 手动挑选，或事后直接 `cp outputs/runs/<dir>/input.json experiments/datasets/planner_cache/<stem>.json`。
  - `unmatched > 0` → 通常是中文标题 PDF：Chatflow 把标题翻译/重写过，与 PDF 首页文本不再吻合。直接手工 `cp` 即可（你比脚本更知道哪份对应哪份）。
  - 想覆盖已有缓存 → `--force`。

---

## 6. 构建 30 篇 Manifest

`prepare_dataset.py` 现在还不实用（见 §3.2），所以用 **反向构建法**：以 `planner_cache/` 里已有的 N 份 JSON 为锚点构 manifest，因为每份 cache JSON 都已经携带 `poster_title`，下游 metric 不会缺字段。

```bash
python - <<'EOF'
import json
from pathlib import Path

cache_dir = Path("experiments/datasets/planner_cache")
papers_dir = Path("experiments/datasets/papers")
out = Path("experiments/configs/papers_30.json")

# 排除孤儿条目（有 cache 但没 PDF 或没历史指标）
DROP = {"上下文工程_2F73CE"}

records = []
for js in sorted(cache_dir.glob("*.json")):
    stem = js.stem
    if stem in DROP:
        continue
    data = json.loads(js.read_text(encoding="utf-8"))
    pdf = papers_dir / f"{stem}.pdf"
    records.append({
        "arxiv_id": stem,
        "title": data.get("poster_title", ""),
        "authors": [],
        "category": "",
        "year": 2026,
        "page_count": None,
        "source_pdf": str(pdf),
        "source_url": "",
        "license": "manual_dify_upload",
        "gold_figure_count": 0,
        "gold_figures": [],
        "gold_sections": [],
        "gold_claims_path": "",
        "from_paper2poster_bench": False,
    })

out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"wrote {len(records)} papers to {out}")
EOF
```

- **作用**：从 `planner_cache/*.json` 反推 manifest，确保每条 manifest 记录都有对应的 cache JSON 和 PDF。
- **输入**：`experiments/datasets/planner_cache/*.json` + `experiments/datasets/papers/*.pdf`
- **输出**：`experiments/configs/papers_30.json`（**L3 产物**）
- **位置**：L2 → L3
- **避坑**：
  - 当前 `planner_cache/` 有 30 份 → 把 `DROP` 清空，直接 n=30。
  - 想跑 n=31 留孤儿 → 同上，不删任何 stem。
  - manifest 的 `arxiv_id` 必须严格等于 PDF stem，下游 `run_matrix.py` 是按这个字段拼 cell 目录的。

---

## 7. 跑实验矩阵：30 篇 × 3 baseline = 90 cell

```bash
python -m experiments.scripts.run_matrix \
    --papers experiments/configs/papers_30.json \
    --baselines ours_svfp,ours_no_svfp,gpt4o_zeroshot \
    --workers 2 \
    2>&1 | tee experiments/results/run_matrix_console.log
```

- **作用**：实验矩阵编排器。`ProcessPoolExecutor(max_workers=2)` 并发跑 cell（每个 cell = 一篇 PDF × 一个 baseline）。已完成（`poster.pptx` 存在且非空 + `metadata.json` 存在）的 cell 默认跳过，加 `--rerun` 强制重跑。每个子进程被注入 `POSTER_EXPERIMENT_MODE=1` 和 `POSTER_EXPERIMENT_LOG=<cell>/experiment_log.jsonl`，让生产链路的遥测钩子写到 cell 自己的日志里。
- **三个 baseline 的差别**（重要！这是论文里的对照设计）：

| baseline | planner 来源 | 渲染路径 | 用途 |
| --- | --- | --- | --- |
| `ours_svfp` | `planner_cache/<stem>.json`（Dify 真实产物）→ 设 `use_commenter=True, max_iterations=4` | `VisualFeedbackLoop().run(task)` 走完整 SVFP 反馈循环 | 论文主推方法 |
| `ours_no_svfp` | 同上，但强制 `use_commenter=False, max_iterations=1` | `generate_dashboard_pptx(task)` 一次渲染 | 消融实验：去掉 SVFP，证明反馈循环的增量贡献 |
| `gpt4o_zeroshot` | 单次 LLM 调用（默认 Qwen3-32B）生成 PosterTask | `generate_dashboard_pptx(task)` 一次渲染 | 对照：相同渲染器，不同 planner，证明 Dify Planner 提示词的贡献 |

- **设计要点**：三个 baseline **共用同一个 renderer**（`generate_dashboard_pptx` 和 `VisualFeedbackLoop`），这样 `ours_svfp` vs `ours_no_svfp` 的差异 = SVFP 反馈循环的贡献；`ours_no_svfp` vs `gpt4o_zeroshot` 的差异 = Planner 提示词 + 图文匹配的贡献。**绝对不要让某个 baseline 用自家 renderer**，否则混淆 planner 与 renderer 两个变量。
- **输入**：
  - `papers_30.json`（L3）
  - `planner_cache/*.json`（L2，仅 ours 系列读）
  - `baselines.yaml`（声明 module/class 映射）
- **输出（每 cell 一组）**：`experiments/results/artifacts/<baseline>_<arxiv_id>/`
  - `poster.pptx` — 最终海报
  - `poster.png` — LibreOffice 转成的位图（B1/B2 metric 用）
  - `panels.json` — 该 cell 的 PosterTask 快照（A1/A3/A4 用）
  - `metadata.json` — 时延、exit_code、planner_source 等
  - `experiment_log.jsonl` — 每次 LLM 调用、每次渲染步骤的逐条日志（D1/D2 用）
- **位置**：L3 → L4
- **耗时**：90 cell × 平均 3 分钟 ≈ 4.5 小时（`--workers 2` 并发后约 2–4h）。
- **避坑**：
  - **`--workers` 不要开太大**。SVFP 会调 Qwen-VL 评图，每个 cell 起一个新的 LibreOffice 子进程渲染 PPTX → PNG，3–4 个并发就能把 16GB Mac 顶到 90% 内存。
  - **failed cell 不会让整批挂掉**，结尾打印 `failed: N` 时再针对性 rerun。
  - **rerun 单 cell** 用 `run_one_paper.py`：

```bash
python -m experiments.scripts.run_one_paper \
    --paper experiments/datasets/papers/<paper>.pdf \
    --baseline ours_svfp \
    --out experiments/results/artifacts
# 注意 --out 是 *父目录*；脚本会在里面建 ours_svfp_<paper>/ 子目录
```

  - `gpt4o_zeroshot` 默认走 SiliconFlow + Qwen3-32B。想换真 GPT-4o：在 `baselines.yaml` 里把它的 `model/api_key_env/base_url` 一并改掉。

---

## 8. 计算 metric

```bash
python -m experiments.scripts.compute_metrics --all \
    2>&1 | tee experiments/results/compute_metrics_console.log
```

- **作用**：对 `experiments/results/artifacts/` 下每个 cell，遍历 13 个 metric 类，逐一打分；结果以一份 JSON / cell 落盘。
- **13 个 metric 分组**：

| 类 | id | 简述 | 依赖 |
| --- | --- | --- | --- |
| Content | A1 information_retention | LLM 抽 claim → NLI 蕴含检验 | LLM judge |
| | A2 figure_text_alignment | AltCLIP 嵌入计算图文余弦相似度 | 本地模型 |
| | A3 hallucination | NLI 反查 panel 文本能否被原文蕴含 | LLM + DeBERTa NLI |
| | A4 section_coverage | 检查 6 个 canonical section 的覆盖率 | 字符串匹配 + LLM 回退 |
| Visual | B1 layout_rationality | 几何分（grid 对齐 / overlap / 白空 / 阅读流） + VLM 评分 | PPTX geom + Qwen-VL |
| | B2 readability | 字号 / 行距 / 对比度的可读性 proxy | PPTX 静态分析 |
| | B3 academic_compliance | 标题精度 / 引用合规等 5 项 | 需 expert_ratings.csv（人工） |
| User | C1 paperquiz | LLM 生题 → VLM 看海报作答 → 准确率 | LLM + VLM |
| | C2 sus_likert | SUS / Likert 量表 | 需 sus_likert.csv（人工） |
| | C3 time_saving | 用户读时长对比 | 需 timing.csv（人工） |
| Eng | D1 latency | 从 experiment_log.jsonl 算端到端耗时 | 纯静态 |
| | D2 cost | 用 pricing.py 给 LLM 调用计价 | 纯静态 |
| | D3 failure_rate | exit_code / soffice 失败 / 空 PPTX | 纯静态 |

- **输入**：`experiments/results/artifacts/*/` + `experiments/configs/metrics.yaml`（per-metric 阈值/模型/权重） + `papers_30.json`（meta 反查）
- **输出**：`experiments/results/metrics/<baseline>_<arxiv_id>.json`，每文件含一个 dict `{metric_id → {score, skipped, skip_reason, detail}}`
- **位置**：L4 → L5
- **耗时**：~30–60 分钟（取决于 LLM cache 命中率）。
- **避坑**：
  - **B3/C2/C3 是人工 metric**，缺对应 CSV 会被 `skipped=true` 标记，不影响主表的 A/B/D 维度。
  - **LLM judge 有缓存**：`experiments/.cache/` 用 `SHA256(model+prompt+temperature)` 做 key，二次跑近乎免费。第一次跑会非常慢且烧钱。
  - **跑单 cell 调试**：`--artifact experiments/results/artifacts/ours_svfp_RAG_0816F4 --metrics a1_information_retention`
  - **并发**：`--workers 4` 用线程池（LLM 调用是 I/O bound），别用进程池。

---

## 9. 聚合统计 + 配对显著性

```bash
python -m experiments.scripts.aggregate_stats --out experiments/results/aggregate/
```

- **作用**：把 metric/cell 矩阵聚合成 metric × baseline 的表，做 BCa bootstrap 95% CI + Wilcoxon signed-rank（单边，方向按 metric 是高好还是低好确定） + Cohen's d + rank-biserial r。p 值同时给 Bonferroni 校正（除以 3 个 baseline 对比）和 BH-FDR（q=0.10）survives 标记。
- **输入**：`experiments/results/metrics/*.json`（L5）
- **输出**：
  - `aggregate.tsv` 列：`metric / baseline / n / mean / ci_low / ci_high`
  - `pairwise.tsv` 列：`metric / ours / vs / n_pairs / p_value / p_bonferroni / bh_fdr_survives / cohens_d / rank_biserial_r`
- **位置**：L5 → L6
- **避坑**：
  - 默认 `--reference ours_svfp`，即把 SVFP 当作 "Ours" 与其它 baseline 配对。
  - 高优 / 低优 metric 的方向写死在 `_HIGHER_IS_BETTER` / `_LOWER_IS_BETTER` 集合里——如果新增 metric 必须同步更新，否则 Wilcoxon 的 `alternative` 会反向。

---

## 10. 出图 + 出表

```bash
python -m experiments.scripts.plot_figures --out experiments/results/figures/
python -m experiments.scripts.print_paper_table
```

- `plot_figures.py` 产出 5 张图（每张 PDF + PNG）：
  - `fig01_quality_bars.pdf` — A1/A2/A3/A4/B1/B2/C1 的分组条形图
  - `fig02_d1_latency_log.pdf` — D1 时延，log 轴
  - `fig03_d2_cost.pdf` — D2 单海报 USD 成本
  - `fig04_d1_d2_pareto.pdf` — 时延-成本 Pareto 散点
  - `fig05_per_paper_b2.pdf` — 每篇 paper 的 B2 可读性 strip plot
- `print_paper_table.py` 把 `aggregate.tsv + pairwise.tsv` 渲染成可直接贴进 `PAPER_DRAFT_v0.md` 的 Markdown 表（主表 + Cohen's d 摘要）。
- **位置**：L6 → L7/L8
- **避坑**：
  - 中文 PDF 标题在 fig05 里需要 CJK 字体——脚本里写死了 `Heiti TC / PingFang SC / Noto Sans CJK SC`，macOS 默认能 fallback；Linux 服务器跑要先装 Noto CJK。
  - 第一次跑出来发现某 metric 没数 → 99% 是 metric 跑挂了被 `skipped=true` 静默掉，回 §8 看日志。

---

## 11. Sanity check（贴论文前必做）

打开 `experiments/results/aggregate/aggregate.tsv`，验证：

1. **B1**: `ours_svfp` 均值 > `ours_no_svfp` > `gpt4o_zeroshot` ？
   - 是 → SVFP 视觉收益叙事在 n=30 站得住 → 论文核心主张安全。
   - 否 → 小样本 artifact，需要重新设计贡献叙事。
2. **B1/B2 的 CI**：`ours_svfp` vs `ours_no_svfp` 不相交？
   - 不相交 → 强主张。
   - 相交但 vs `gpt4o_zeroshot` 不相交 → 改口为 "SVFP 显著优于单次规划" 更安全。
3. **A1**：`gpt4o_zeroshot` 是不是最高？
   - 是 → `PAPER_DRAFT_v0.md` Part 1.3 的 precision–recall trade-off 叙事仍成立。
   - 否 → 重新打磨叙事。
4. **D1（latency）`ours_svfp` 中位数**：>120s 则 `PAPER_DRAFT_v0.md` Part 0.2 的 M5（D1 优化）任务变成 priority。

---

## 12. 一页 cheat sheet

```
[ ] §1   环境 + .env + Dify 连通                       (一次性)
[ ] §2   python -m app.main                            (保持长开)
[ ] §3   ls experiments/datasets/papers/*.pdf | wc -l   (确认数量)
[ ] §4   batch_dify_runs --limit N --skip-cached        (~2 min/篇)
[ ] §5   import_dify_runs --report-path …               (~2 min total)
[ ] §6   inline python 构建 papers_30.json              (~1 min)
[ ] §7   run_matrix --baselines ours_svfp,...           (~3 h)
[ ] §8   compute_metrics --all                          (~45 min)
[ ] §9   aggregate_stats                                (~10 s)
[ ] §10  plot_figures + print_paper_table               (~30 s)
[ ] §11  sanity check aggregate.tsv                     (~10 min 人工)
```

---

## 13. 常见症状速查

| 症状 | 多半是 | 立刻做 |
| --- | --- | --- |
| `DIFY_API_KEY missing` | `.env` 没加载 | `grep DIFY_API_KEY .env`，key 上不要加引号 |
| Dify 全篇 `[401]` | API key 过期 | Dify > Access API 重新复制 |
| Dify 全篇 `[400]` chat-messages | Start 节点变量名变了 | 打开 Chatflow 看 Start 节点变量列表 |
| Dify 全篇 `[404]` chat-messages | 应用类型变成 Workflow 不是 Chatflow | 改回 `/workflows/run` |
| `import_dify_runs` 写入 0 | `outputs/runs/` 是空的 | Chatflow 的 HTTP 节点根本没到 FastAPI |
| `run_matrix` 全 fail | FastAPI 没跑 | `python -m app.main` |
| compute_metrics A1/A3 跑得很慢 | LLM cache miss + rate limit | 重跑一次走 cache |
| fig05 中文乱码 | 缺 CJK 字体 | macOS 自带；Linux 装 Noto CJK |
| aggregate.tsv 缺某 metric | 该 metric 全 cell 都 skipped | 回 §8 看 metric JSON 的 `skipped` 字段 |

---

## 14. 数据流交叉引用

| 产物 | 由谁产生 | 谁消费 |
| --- | --- | --- |
| `outputs/runs/*/input.json` (L1) | FastAPI `/generate_ppt` （Dify Chatflow 触发） | `import_dify_runs.py` |
| `planner_cache/*.json` (L2) | `import_dify_runs.py` | `ours_svfp.py`, `ours_no_svfp.py`（通过 `_planner_shared.cached_plan`） |
| `papers_30.json` (L3) | §6 内联脚本 | `run_matrix.py`, `compute_metrics.py` |
| `artifacts/<cell>/poster.pptx` (L4) | `run_matrix.py` → baseline runner | `compute_metrics.py`（B1/B2 几何分析）+ 论文 demo |
| `artifacts/<cell>/panels.json` (L4) | baseline runner（PosterTask 快照） | A1/A3/A4 metric |
| `artifacts/<cell>/experiment_log.jsonl` (L4) | 生产链路 hook | D1/D2 metric |
| `metrics/<cell>.json` (L5) | `compute_metrics.py` | `aggregate_stats.py`, `plot_figures.py` |
| `aggregate.tsv` / `pairwise.tsv` (L6) | `aggregate_stats.py` | `plot_figures.py`, `print_paper_table.py` |
| `figures/*.pdf` (L7) | `plot_figures.py` | 直接贴 PAPER_DRAFT |
| `print_paper_table.py` stdout (L8) | 同上 | 直接贴 PAPER_DRAFT |

---

## 15. 与 Dify 流程的边界

整个实验链路里，Dify Chatflow 负责的部分仅在 **L0 → L1**（即把 PDF 走完整三 Agent 链路，输出 `input.json` 这份 `PosterTask` JSON）。Chatflow 本身的设计（三个 Agent 的 Prompt 与节点编排）是论文的"前期准备工程"——详见同仓库的 `dify/DIFY_WORKFLOW_AND_PAPER_DESIGN.md`，那里把 Chatflow 拆解到了 Agent / 节点 / Prompt 三个层面，便于你后续做论文方法章节或迭代 Prompt 时直接复用。

本文档仅关心：**Dify 跑完之后，怎么把 L1 产物搬运到 L2 → 跑实验 → 出论文表**。两份文档配合就是从 PDF 到论文图表的完整闭环。
