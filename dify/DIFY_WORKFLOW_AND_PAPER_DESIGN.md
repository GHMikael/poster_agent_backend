# Dify 工作流与论文前期设计文档（DIFY_WORKFLOW_AND_PAPER_DESIGN）

> 本文是整篇论文"前期准备工程"的工程沉淀文件。它的作用不是教你怎么跑——跑流程见 `INTERNAL_EXPERIMENT_GUIDE.md`——而是把"为什么是这个 Chatflow + 为什么是这三个 Agent + 三个 Prompt 设计的关键约束"完整地写下来，让你（以及未来的 AI、合作者、审稿前的自己）只要读完这份文档加 `dify/prompts/` 三份 Prompt 原文，就能完整复述这一模块的设计意图、为什么这样设计、它对论文整体叙事的支撑作用。
>
> 配套文件：
> - `dify/prompts/text-parseagent.txt`
> - `dify/prompts/visual-parseagent.txt`
> - `dify/prompts/planneragent.txt`
> - `INTERNAL_EXPERIMENT_GUIDE.md`（实验运行手册）
> - `app/main.py`、`app/models.py`、`app/pdf_assets.py`（后端 contract）

---

## 1. 模块定位：Dify 流程在整篇论文里的角色与创新点

### 1.1 整篇论文的工程视角

论文研究一个端到端的问题：**给定一篇学术论文 PDF，自动生成一张可编辑、信息密度高、版面美观的 A3 学术海报。** 整体系统拆成三段：

```
[PaperParse + Planner Agents]  ──→  [Renderer]  ──→  [SVFP Visual Feedback Loop]
      ↑                                  ↑                       ↑
   本文档主题                          静态渲染                  视觉反馈迭代
   （Dify Chatflow）                  （app/ppt_renderer.py）   （app/feedback_loop.py）
```

Dify Chatflow 承担的是**第一段：把一份非结构化的 PDF 转化为渲染器可以直接消费的结构化 JSON 资产（`PosterTask`）**。这一步是论文里的"内容规划 + 图文匹配"层，决定了海报的信息选择、章节切分、模板/调色板选择、图文配对策略，是渲染器和反馈循环都赖以工作的"语义基底"。

### 1.2 为什么这一段值得单独写成论文模块

学术海报生成的难点不在 PPTX 渲染——把布局画好是工程问题。真正的瓶颈在于：

1. **论文是非结构化文本**：6–30 页 PDF，包含数千个 token、十多张图，但海报只有有限空间（5–7 个 panel × 各 5–6 个 bullet）。
2. **图文必须语义对齐**：方法框架图必须放在 Method panel，结果对比图必须放在 Results panel——错配会让海报失去阅读价值。
3. **风格选择是判断题**：方法类论文适合 classic 模板；多 Agent 系统适合 storyflow；benchmark 适合 dashboard。这种判断需要看完全文才能做。
4. **必须可重现**：作为论文实验，这个规划过程必须每次都产出同样结构的 JSON，否则下游 baseline 对比无意义。

我们把这四件事**全部塞进一个 Dify Chatflow，并且通过三个 Agent 的分工把它们解耦**——这就是该模块的工程价值，也是论文方法章节的核心创新点。

### 1.3 三大创新点（论文叙事可直接引用）

1. **解耦的多 Agent 分工**：把"看文本""看图""做规划"分到三个独立 Agent，每个 Agent 只暴露最小输入面，单独可调试、单独可替换、单独可消融。
2. **离图 metadata-only 视觉规划**：Visual Parse Agent 永远不接触 base64 图片，只看后端给出的 caption + 元数据 + URL token；既减少 Token 消耗 70%+，也避免了图像在 Agent 间传输的安全/带宽问题。
3. **生产 → 实验的同源回放**：Chatflow 跑完的 `PosterTask` JSON 原封不动落盘到 `planner_cache/`，下游实验里所有 baseline 在"完全一致的规划"上对比，把 planner 非确定性从实验变量中剔除——这是 §4 详述的工程价值。

---

## 2. 架构说明：数据流入 → 节点调度 → JSON 结构化资产生成

### 2.1 完整 Chatflow 节点拓扑（与 Dify UI 截图一一对应）

```
开始 (Start)
   │ paper: File 类型（"Other file types"，Dify 内部 type=custom）
   ▼
EXTRACTPAPERASSETS (HTTP node, 失败重试 3 次)
   │ POST http://host.docker.internal:8000/extract_pdf_assets
   │ 入参：file（multipart）
   │ 出参：{ asset_token, text_preview, figures: {Fig1: {...}, ...} }
   ▼
数据分割 (Code Node)
   │ 把 EXTRACTPAPERASSETS 的响应拆成两路：
   │   path A: text_preview → TEXT PARSEAGENT
   │   path B: figures      → VISUAL PARSEAGENT
   ▼
   ├──→ TEXT PARSEAGENT (LLM node, Qwen/Qwen2.5-72B-... CHAT)
   │       │ 输入：text_preview
   │       │ 输出：text_assets JSON（dify/prompts/text-parseagent.txt 定义结构）
   │       ▼
   │    数据分割1 (Code Node)
   │       │ 解析 JSON、做合法性校验、剥离 markdown 围栏
   │       ▼
   │       └────────────┐
   │                    ▼
   └──→ VISUAL PARSEAGENT (LLM node, Qwen/Qwen2.5-72B-... CHAT)
           │ 输入：figures 元数据
           │ 输出：visual_assets JSON（dify/prompts/visual-parseagent.txt 定义结构）
           ▼
        数据分割2 (Code Node)
           │ 同上，解析 + 校验
           ▼
           ↓
        PLANNERAGENT (LLM node, Qwen/Qwen2.5-72B-... CHAT)
           │ 输入：asset_token + text_assets + visual_assets
           │ 输出：PosterTask JSON（dify/prompts/planneragent.txt 定义结构）
           ▼
        GENERATEPPT (HTTP node, 失败重试 2 次)
           │ POST http://host.docker.internal:8000/generate_ppt
           │ body: PosterTask JSON
           │ 出参：{ job_id, status: "pending", status_url }
           ▼
        提取JOB_ID (Code Node)
           │ 从 GENERATEPPT 响应抠出 job_id
           ▼
        循环 (Iteration Loop)
           ├── 异步请求 (HTTP): GET /jobs/{job_id}
           ├── 提取STATUS (Code): 抠 status 字段
           └── 变量赋值: 把 status 覆写到循环条件变量
           （直到 status ∈ {completed, failed} 跳出循环）
           ▼
        最终获取PPT结果 (HTTP node, 失败重试 3 次)
           │ GET /jobs/{job_id}（一次终态查询，拿 download_url + result.body）
           ▼
        FINAL结果 (Code Node)
           │ 组装最终回复 body/status_code/headers/files
           ▼
        预览 (End Node)
           回复 PlannerAgent.structured_output（即 PosterTask JSON）
```

### 2.2 数据流的三层抽象

整个 Chatflow 的状态可以抽象成三层：

| 层 | 形态 | 谁产生 | 谁消费 | 关键作用 |
| --- | --- | --- | --- | --- |
| **L0 原始资产** | PDF 二进制 | 用户上传 | EXTRACTPAPERASSETS | 系统的唯一输入 |
| **L1 语义元数据** | `text_preview`（≤12000 字）+ `figures`（轻量元数据）+ `asset_token` | EXTRACTPAPERASSETS（FastAPI） | TEXT/VISUAL PARSEAGENT | 把 PDF 解耦成"可塞进 LLM 上下文"的紧凑表示 |
| **L2 海报中间表示** | `text_assets`（结构化论文摘要）+ `visual_assets`（图片类型 + 章节匹配 + 重要性） | TEXT/VISUAL PARSEAGENT | PLANNERAGENT | 进一步把语义元数据折叠成"做规划决策所需的最少信息" |
| **L3 渲染指令** | `PosterTask` JSON（模板 + 调色板 + 5–7 panel + 图文匹配） | PLANNERAGENT | GENERATEPPT（FastAPI） | 直接喂给渲染器的最终产物 |

每一层都比上一层更紧凑、更结构化、更接近"海报渲染需要什么"。这种逐层折叠的设计让任何一层都可以单独被替换、消融、缓存。

### 2.3 异步任务模式（重点说明）

GENERATEPPT 并不是一次同步调用就结束——因为 SVFP 反馈循环单篇耗时 60–180s，远超 Dify HTTP 节点的默认超时（约 60s）。设计上：

1. GENERATEPPT 立刻拿到 `{job_id, status: "pending"}` 返回（HTTP 202）。
2. 进入 Dify "循环" 节点，每次循环 GET `/jobs/{job_id}`。后端用 long-polling：默认 `wait=20s`，要么 20s 内 job 终态返回，要么 20s 超时再返回当前状态。
3. 当状态变为 `completed` 或 `failed`，循环跳出，进入 "最终获取PPT结果" 节点拿最终结果。

这套设计同时满足：
- **客户端不重试** → 不会产生重复 run（Dify 默认 5xx 自动重试，所以同步 60s+ 调用必然炸）。
- **服务端不长持连接** → FastAPI 的请求不会被 Dify 一个超长 HTTP 卡住。
- **Dify 节点都在超时内** → 整条链路对网络抖动鲁棒。

### 2.4 失败重试策略

| 节点 | 重试次数 | 重试目的 |
| --- | --- | --- |
| EXTRACTPAPERASSETS | 3 | FastAPI 偶发抖动 / PDF 解析瞬时失败 |
| GENERATEPPT | 2 | 同上，但比上面少一次：异步 job 提交是幂等的 |
| 异步请求（轮询 GET /jobs/{job_id}）| 3 | 单次网络抖动不应让整个 chatflow 失败 |
| 最终获取PPT结果 | 3 | 同上 |

LLM 节点（三个 Agent）**没有显式重试**——LLM 失败通常是 Token 超限或合约不满足，重试也不会更好，应该靠 Code Node 的解析层兜底（数据分割/数据分割1/数据分割2）。

---

## 3. 核心 Prompt 解析：三个 Agent 的职责、设计逻辑、关键约束

### 3.1 TEXT PARSEAGENT — 论文文本压缩与结构化

**所在 Prompt**：`dify/prompts/text-parseagent.txt`
**模型**：Qwen/Qwen2.5-72B-Instruct（Chat 模式）
**输入变量**：`{{#1778997519582.text_preview#}}`（EXTRACTPAPERASSETS 节点的 `text_preview`，即 PDF 全文清洗后≤12000 字符的扁平字符串）
**输出变量**：`text_assets` JSON（被 数据分割1 节点解析后供 PlannerAgent 使用）

#### 3.1.1 职责

把一份开放结构的论文文本，**严格折叠成 10 个字段的固定 schema**：

```jsonc
{
  "title": "论文标题",
  "authors": "作者或机构信息",
  "paper_info": "论文来源、年份或一句话主题概括",
  "language": "zh 或 en",
  "research_background": [...],
  "research_problem":    [...],
  "method":              [...],
  "experiments":         [...],
  "results":             [...],
  "contributions":       [...],
  "limitations":         [...],
  "conclusion":          [...],
  "keywords":            [...]
}
```

#### 3.1.2 设计逻辑

1. **以 schema 为输入而非提示**：不是让 LLM "自由总结论文"，而是让它"按这 10 个槽位填空"。这种"用结构反向约束 LLM"的做法显著降低输出方差，让下游 Planner 可以按 key 直接索引，不需要做 fuzzy 提取。
2. **强制语言一致**：从 PDF 文本自动识别 `language` 字段，下游所有 panel 文本就跟这个字段走，绝不混用——避免中文海报里夹一段英文 bullet 的尴尬。
3. **空字段语义**：缺失信息用 `""` 或 `[]`，**禁止编造**。这条约束在 Prompt 里写得很死，是 A3 hallucination 指标守得住的前提。

#### 3.1.3 关键 Prompt 约束（提示词层面）

| 约束 | Prompt 原文摘要 | 为什么这样写 |
| --- | --- | --- |
| 不复制原文 | "不要简单复制论文原文" | 海报是压缩表示，不是摘要——逐字复制会让 panel 拥挤 |
| 不编造 | "不要编造论文中不存在的贡献、实验结果或方法细节" | A3 hallucination metric 的硬约束 |
| 缺失字段语义 | "如果某项信息在输入中缺失，请使用空字符串或空数组，不要猜测" | 配合下游 Planner 对空字段的 graceful degradation |
| 海报友好 | "输出内容应适合后续 PlannerAgent 生成 A3 学术海报" | 让 LLM 把压缩颗粒度调到"bullet 级"而非"段落级" |
| 只输 JSON | "必须只输出合法 JSON / 不要输出 Markdown / 不要输出代码块" | 配合 Code Node 解析层；任何额外字符都会让下游解析挂掉 |
| 双引号 / 无尾逗号 | 显式列出 | JSON 严格性，Dify 内置 JSON Parser 对宽松 JSON 不容错 |

#### 3.1.4 失败模式与防御

- **失败模式 1**：Token 超限（论文 >12000 字符且充满复杂公式）→ EXTRACTPAPERASSETS 节点已在 `app/pdf_assets.py:_clean_text(max_len=12000)` 处做截断，所以 LLM 永远不会爆。
- **失败模式 2**：LLM 输出 markdown 围栏（` ```json … ``` `）→ 数据分割1 节点的 Code Node 做正则剥离，再 `JSON.parse`。
- **失败模式 3**：LLM 跳过某个字段 → 下游 PlannerAgent 设计上能处理空数组（用前/中/后置 fallback），不会因此挂掉。

### 3.2 VISUAL PARSEAGENT — 图片元数据语义分析

**所在 Prompt**：`dify/prompts/visual-parseagent.txt`
**模型**：Qwen/Qwen2.5-72B-Instruct（Chat 模式）
**输入变量**：`{{#1778997519582.figures#}}`（EXTRACTPAPERASSETS 节点的 `figures`，每张图只含 `figure_id / caption / page / width / height / image_url / thumbnail_url`，**不含 base64**）
**输出变量**：`visual_assets` JSON

#### 3.2.1 职责

为每张图打四个标签：

```jsonc
{
  "visual_assets": {
    "Fig1": {
      "caption": "原始或压缩 caption",
      "type": "method_diagram | pipeline | architecture | result_chart | comparison_table | ablation_study | case_study | dataset_example | qualitative_example | other",
      "description": "保守描述（不能假装看到了图内细节）",
      "best_matched_section": "Motivation | Problem | Method | Experiments | Results | Key Findings | Conclusion | Other",
      "importance": "high | medium | low",
      "page": 1,
      "width": 1200,
      "height": 800,
      "image_url": "...",
      "thumbnail_url": "..."
    }
  }
}
```

#### 3.2.2 设计逻辑：为什么是"离图"Agent

这是整个 Chatflow 最关键的设计选择之一。**Agent 完全不接收 base64 图像**，只看 caption + 元数据。原因：

1. **Token 成本**：12 张图各传 base64 至少烧 50k+ token，光这一项就让单次跑成本翻 3 倍。
2. **传输/缓存安全**：Dify 节点间数据走 Redis；图片放在 Redis 里既慢又危险（淘汰策略、内存压力）。
3. **图早就在后端**：EXTRACTPAPERASSETS 已经把图持久化到 FastAPI 的 `static/assets/<asset_token>/Fig<N>.png`，并通过 `asset_token + figure_id` 寻址。Visual Agent 给出的 type / importance / matched section 已经够 Planner 做图文匹配。
4. **图本身的视觉信息有限**：学术论文里的 figure 类型高度规则化（pipeline 图 / 性能曲线 / 表格 / case 截图）。caption 加图尺寸已经能 80% 准确分类，不需要 VLM 看图。

**重要 trade-off**：这种设计放弃了"看图说话"的能力——比如 Visual Agent 看不到具体的对比柱状图里谁高谁低。但 PosterAgent 的角色是规划**用不用这张图、放哪个 panel**，而不是**描述这张图说了什么**（后者是 PaperQuiz / B1 VLM 评分阶段的事）。这种约束反而让 Visual Agent 的失败模式可控。

#### 3.2.3 关键 Prompt 约束

| 约束 | Prompt 原文摘要 | 为什么这样写 |
| --- | --- | --- |
| 离图判断 | "输入中的图片已经由后端保存 / 你不会收到 base64 图片 / 你不能输出 image_source" | 严格约束 Agent 不去虚构图片内容 |
| 保守 caption | "如果 caption 信息不足，请保守描述，不要假装看到了图中细节" | 防止 hallucination 进图描述 |
| 离散标签 | type / best_matched_section / importance 都给了离散 enum | 下游 Planner 用 enum 做规则匹配；自由文本对程序不友好 |
| 重要性规则 | "方法框架图、pipeline 图、模型结构图优先 high / 示例图、补充图优先 medium 或 low" | 让 Planner 优先选择信息密度高的图 |
| JSON 严格 | 同 TextParse | 同上 |

#### 3.2.4 与后端的契约

Visual Agent 的输出 schema 必须与 `app/models.py:FigureAsset` 字段对齐（除 `image_source` 外）：

```python
class FigureAsset(BaseModel):
    caption: str = ""
    type: str = "other"
    description: str = ""
    best_matched_section: str = ""
    importance: str = "medium"
    image_source: str = ""        # 由后端在 hydrate_task_image_sources 阶段填回
    image_url: str = ""
    thumbnail_url: str = ""
```

Planner 写出来的 `figures` 字段是 `Dict[str, FigureAsset]`，所以 Visual Agent 的输出 key 必须严格用 `"Fig1" / "Fig2" / ...`，与 EXTRACTPAPERASSETS 的命名空间一致。

### 3.3 PLANNERAGENT — 海报规划主脑

**所在 Prompt**：`dify/prompts/planneragent.txt`
**模型**：Qwen/Qwen2.5-72B-Instruct（Chat 模式）
**输入变量**：
- `{{#1778997519582.asset_token#}}`（EXTRACTPAPERASSETS 的 asset_token）
- `{{#1757473416398.result#}}`（数据分割1 输出，即 text_assets）
- `{{#1778566499276.result#}}`（数据分割2 输出，即 visual_assets）

**输出变量**：完整的 `PosterTask` JSON（直接喂给 GENERATEPPT 节点 POST 到 `/generate_ppt`）

#### 3.3.1 职责

输出一份完整的 `PosterTask`，包括：

1. **`template` 模板选择**（4 选 1：dashboard / classic / storyflow / minimal）
2. **`color_theme` 调色板选择**（4 选 1：academic_blue / engineering_green / warm_orange / minimal_gray）
3. **`poster_title / authors / paper_info`**（海报头部信息）
4. **`layout`**（A3 / dashboard_grid / top_to_bottom_left_to_right）
5. **`panels`**（5–7 个 panel，每个 panel 含 `section / content / figure_id / figure_caption / layout_hint`）
6. **`figures`**（被选中的图的轻量 metadata 池，去掉 image_source）
7. **`use_commenter / max_iterations / save_debug_images`**（控制下游 SVFP 反馈循环）

#### 3.3.2 设计逻辑：六大决策维度

PlannerAgent 是整个 Chatflow 里**决策密度最高**的节点。它要在一次 LLM 调用里做完六个决策：

1. **论文类型判断**（方法 / benchmark / empirical / 系统 / 综述 / 应用）—— 仅内部用，不输出，用来选 template。
2. **论文学科领域判断**（AI / CV / NLP / ML / HCI / 教育 / 医疗 / 工程 / 其它）—— 仅内部用，不输出，用来选 color_theme。
3. **template × color_theme 搭配**：Prompt 里写了"哪种 template 推荐配哪种 color_theme"的规则表，约束 LLM 不要产生视觉冲突组合。
4. **panel 结构化**：把 text_assets 的多个 list 字段（research_background / method / results / ...）折叠成 5–7 个 panel。Prompt 给了推荐模板 `Motivation / Problem / Method / Experiments / Results / Key Findings / Conclusion`，但允许根据论文结构调整。
5. **bullet 压缩**：每个 panel 5–6 个 bullet（**有图时降为 3 个**，防止挤压图位）；英文 ≤25 词，中文 ≤35 字。
6. **图文匹配**：从 visual_assets 中按 `best_matched_section` 和 `importance` 选图；同一张图不重复使用；选图后 panel 的 `layout_hint` 也跟着切换。

#### 3.3.3 关键 Prompt 约束（论文里需要引用这部分）

| 约束 | Prompt 原文摘要 | 设计动机 |
| --- | --- | --- |
| 不输出 image_source | "不要输出 image_source / 不要输出 base64" | 图片只通过 `asset_token + figure_id` 寻址，后端再水合（`hydrate_task_image_sources`） |
| 包含顶层 asset_token | "最终 JSON 必须包含顶层 asset_token" | 后端用它把 figure_id 解析回真实文件路径 |
| 必须含 template/color_theme | 显式列出 | 下游 renderer 是模板驱动的，缺这两个字段就崩 |
| panel 5–7 个 | "为一张 A3 学术海报规划 5 到 7 个 panel / panel 数量不要过多" | 排版美学约束（A3 上 8+ panel 会拥挤） |
| **核心：有图 panel 减 bullet** | "如果你为一个 panel 选择了图片，那么这一 panel 的 content 的 bullets point 就减少为 3 条，避免渲染时占用图片太多位置" | 这条规则是迭代多次得到的经验：渲染器算 bullet 高度 + 图片高度做包面会 overflow，因此 Prompt 层就限死 bullet 上限 |
| 严禁中英混杂 | 显式 | 配合 text_assets 的 language 字段 |
| 严格 JSON | 显式 | 输出直接 POST 到 `/generate_ppt`，任何额外文本会让 FastAPI 422 |
| layout_hint 离散 | 5 选 1（text_only / text_left_image_right / text_top_image_bottom / image_top_text_bottom / image_only）| renderer 按 layout_hint 分发到不同子布局 |

#### 3.3.4 模板与调色板的"决策矩阵"

Prompt 里写了一个明确的决策矩阵，便于复现：

| template | 触发条件 | 推荐 color_theme |
| --- | --- | --- |
| `template_dashboard` | 多模块系统 / benchmark / 多维结果对比 / 难以判断时默认 | `academic_blue` 或 `minimal_gray` |
| `template_classic` | 传统方法类，重点突出方法 + 实验 | `academic_blue` 或 `warm_orange` |
| `template_storyflow` | 流程 / pipeline / multi-stage / multi-agent | `engineering_green` 或 `academic_blue` |
| `template_minimal` | 内容精简、贡献集中、一句话核心结论 | `minimal_gray` 或 `academic_blue` |

| color_theme | 触发条件 |
| --- | --- |
| `academic_blue` | AI / ML / CV / NLP / Agent；难判时默认 |
| `engineering_green` | 系统 / 工程 / 机器人 / 嵌入式 / 自动驾驶 / 软件工程 |
| `warm_orange` | 教育 / HCI / 社会计算 / 可视化 / 设计 / 心理认知 / 医疗 |
| `minimal_gray` | 综述 / 理论 / benchmark / 纯实证 |

#### 3.3.5 输出 JSON 与后端 `app/models.PosterTask` 的契约

Planner 输出经 GENERATEPPT 节点 POST 给 FastAPI，由 `_parse_poster_task` 反序列化成 `PosterTask`，进入 `_run_poster_job`。所以 Planner JSON 的字段顺序、字段名必须严格匹配 `app/models.py`。当前对齐字段：

```python
class PosterTask(BaseModel):
    asset_token: str
    template: str
    layout_variant: str = "auto"      # Planner 不输出时用默认
    color_theme: str
    emphasis_level: str = "normal"
    poster_title: str
    authors: str
    paper_info: str
    layout: PosterLayout              # page_size / layout_type / reading_order
    panels: List[Panel]               # 必填
    figures: Dict[str, FigureAsset]   # 默认 {}
    use_commenter: bool = False       # Planner 默认 true → 走 SVFP
    max_iterations: int = 2           # Planner 默认 4
    save_debug_images: bool = True
    global_font_scale: float = 1.0
```

**注意**：当前 `planneragent.txt` 第 203 行写的是 `"use_commenter": ture`，存在一个 typo（应为 `true`），但 Pydantic 解析时如果上游 Code Node 没纠正会 422。这是一个**待修复 issue**（在生产中走通是因为 Dify Code Node 做了 fallback；纯 Prompt 输出严格按规范应是 `true`）。

---

## 4. 工程价值：为什么这样设计、对论文与系统的支撑作用

### 4.1 对系统扩展的支撑

| 扩展场景 | 受益于哪个设计 |
| --- | --- |
| 增加新模板（如 `template_journal`）| 只需改 PlannerAgent Prompt 的模板枚举 + renderer 加分支；TextParse / VisualParse 不动 |
| 增加新 color_theme | 只需改 PlannerAgent Prompt + renderer 配色表 |
| 把 Qwen 换成 GPT-4o / Claude | 三个 Agent 各自的 LLM 节点单独换模型；其它节点零改动 |
| 加新章节类型（如 Theorem panel）| TextParse schema 加一个 list 字段 + Planner panel 模板里加一项 |
| 把 VLM 看图引入 Visual Agent | Visual Agent 改成多模态 LLM，Prompt 加一段"可看图但仍按 schema 输出" |
| 增加新 baseline 做消融 | 直接读 `planner_cache/<stem>.json`，不需要重跑 Dify |

### 4.2 对论文打磨的支撑

#### 4.2.1 实验严谨性

`planner_cache/` 是论文实验结果可信度的基石。这是设计上最关键的工程价值：

- **生产 = 实验**：跑 Dify 一次拿到的 `input.json` 被 `import_dify_runs.py` 原封不动复制到 `planner_cache/<stem>.json`。下游 `ours_svfp` 和 `ours_no_svfp` 通过 `_planner_shared.cached_plan()` 直接读这个 JSON，跳过 LLM 调用。
- **消除 planner 非确定性**：LLM 即便 `temperature=0` 也会有微小波动（KV cache / batch / 服务端版本）。如果每次 `ours_svfp` vs `ours_no_svfp` 对比都让 Planner 重跑一次，两个 baseline 的差异里就会混入"两次 Planner 调用本身的方差"，让 SVFP 的真实贡献被掩盖。
- **A1/A3 metric 公平性**：A1 / A3 metric 评估的是 panel 文本与原论文的蕴含关系，如果 Planner 每次产物都不同，两个 baseline 在不同 panel 文本上被打分，根本无法做配对 Wilcoxon。
- **复现性**：未来审稿人想复现实验，不需要装 Dify，直接拿仓库里的 `planner_cache/*.json` 就能跑 `ours_svfp` / `ours_no_svfp`。这是论文 reproducibility 的硬指标。

#### 4.2.2 论文方法章节可直接复用的结构

论文方法章节按本文的结构组织即可：

1. **§3.1 三 Agent 解耦**：把"看文本 / 看图 / 做规划"分到三个 Agent。
2. **§3.2 离图 Visual Parse**：解释为什么不喂 base64 给 Visual Agent。
3. **§3.3 PlannerAgent 的六维决策**：把 3.3.2 的六大决策维度列成 method 章节的核心 contribution。
4. **§3.4 异步任务模式**：解释 2.3 节的异步任务 + long-polling 设计，强调"工程鲁棒性"也是论文贡献的一部分。
5. **§3.5 Production-to-experiment replay**：把 §4.2.1 的"生产 = 实验"作为可重现性章节的核心论点。

### 4.3 性能与成本量化（论文 D1/D2 metric 的支撑）

| 维度 | 离散指标 | 当前观测 |
| --- | --- | --- |
| Token 消耗 | TextParse + VisualParse + Planner 总 token | ~20k–25k input / ~3k–5k output per paper |
| 端到端耗时 | Chatflow start → end，含 FastAPI 异步 job | ~120–180s（含 SVFP 4 轮反馈） |
| 单次成本 | Qwen2.5-72B via SiliconFlow，按 token 计价 | 约 $0.02–0.03 / 海报 |
| 一次性失败率 | batch_dify_runs.py 跑完后 `failed/errored` 比例 | <5%（含网络抖动） |

这些数据原始来源于 `experiment_log.jsonl`（D1）和 `experiments/tools/pricing.py`（D2 计价表），所有数据可在论文的 §5 Engineering Metrics 章节被自动汇总（见 `INTERNAL_EXPERIMENT_GUIDE.md` §10）。

### 4.4 对未来 AI 接手该项目的支撑

任何 AI 或工程师只要读完：

1. 本文档（架构 + Prompt 约束）
2. `dify/prompts/` 三份 Prompt 原文
3. `app/main.py` 的 `/extract_pdf_assets` 和 `/generate_ppt` endpoint
4. `app/models.py` 的 `PosterTask` 定义
5. `experiments/datasets/planner_cache/*.json` 任一示例（如 `RAG_0816F4.json`）

就能：

- 完整还原 Chatflow 设计意图；
- 知道改 Prompt 时哪些约束不能动（强制 JSON / 不出 image_source / 模板枚举 / panel 数量）；
- 知道改 schema 时下游谁会受影响（`FigureAsset` ↔ Visual Agent；`PosterTask` ↔ Planner Agent）；
- 知道如何在不破坏实验复现性的前提下，迭代 Prompt（核心：迭代后必须重跑 `batch_dify_runs.py` 重建 `planner_cache/`，并把改动写进 commit message 备查）。

---

## 5. 已知设计取舍与待改进项

### 5.1 已知 trade-off

| 设计选择 | 牺牲了 | 换来了 |
| --- | --- | --- |
| Visual Agent 不接 base64 | 看不到图中细节（柱状图谁高谁低） | Token 成本 ↓70%；安全性 ↑ |
| Planner 一次出全部 JSON | 单次 LLM Token 较高（~10–15k 输出） | 链路简化；不需要在多个 LLM 节点间维护一致性 |
| 三个 Agent 都用同一个 Qwen2.5-72B | 缺少专门化 | 部署简单；只维护一份模型 endpoint |
| 模板/调色板硬枚举（4×4） | 灵活性差 | 渲染器实现简单；视觉风格可控 |
| 异步 job + Dify 循环节点 | Chatflow 拓扑变复杂 | 鲁棒性高；不会触发 Dify 自动重试导致重复 run |

### 5.2 待改进项（按优先级）

1. **`planneragent.txt` 第 203 行 typo**：`"use_commenter": ture` → 应为 `"use_commenter": true`（高优修）。
2. **模型可配置化**：当前三个 Agent 都硬编码 Qwen2.5-72B，应该在 `.env` 里抽出来变量，便于做"用更强模型规划"的消融。
3. **VisualParse 可选 VLM 升级**：保留当前的"离图模式"作为 baseline，新加一个 VLM 分支测看图是否能进一步提升 A2 figure_text_alignment。
4. **PlannerAgent 拆成两步**：第一步做模板/调色板决策（"看 text_assets + visual_assets 给我一个高层风格"），第二步做 panel 规划。理由：当前一次 LLM 调用决策太多，温度稍高就容易翻车；拆开后每步都更稳定。
5. **Chatflow 内的 graceful degradation**：当前如果 LLM 输出 JSON 不合法，仅靠 Code Node 兜底；应该加一个 LLM 重试节点（用更严格的 system message 重写）作为二次防御。

---

## 6. 同源回放工作流（重要工程契约）

把 Dify 输出回放到实验里走以下三步，是论文实验可复现性的关键：

```
Dify Chatflow 跑完
   │ POST /generate_ppt
   ▼
FastAPI 持久化 input.json → outputs/runs/<timestamp>_<slug>_<runid>/input.json
   │
   ▼
experiments/scripts/import_dify_runs.py
   │ 按 poster_title 首页文本匹配 PDF stem
   ▼
experiments/datasets/planner_cache/<pdf_stem>.json
   │
   ▼
experiments.baselines._planner_shared.cached_plan(paper_path)
   │ 检测 cache 存在 → 加载 PosterTask
   ▼
ours_svfp / ours_no_svfp baseline 复用同一份 plan
```

**这条链路必须保持原样**，任何改动都要确认：

- `outputs/runs/<dir>/input.json` 的 JSON 字段必须 100% 符合 `app/models.PosterTask`（否则 import 后下游会爆）。
- `cache_dir` 的路径不能变（`experiments/datasets/planner_cache/`），baseline 代码里写死。
- 文件名必须用 `<pdf_stem>.json`（与 PDF 同名，去掉 `.pdf`），baseline 通过 `paper_path.stem` 直接查找。

如果某天要让 PlannerAgent 输出新增字段，**先升级 `app/models.PosterTask`，再升级 Prompt，最后批量 rerun Dify 重建整个 planner_cache**，否则下游会用旧 schema 的 cache 跑新 baseline。

---

## 7. 阅读优先级（给未来回来的 AI 或人）

如果你只有 5 分钟：读 §1 + §3.3.2 + §4.2.1。
如果你只有 30 分钟：本文 + 三份 Prompt 原文。
如果你要复现完整工程：本文 + `INTERNAL_EXPERIMENT_GUIDE.md` + `app/main.py` + `app/models.py`。
如果你要改 Prompt：必读 §3 + §5.2 + §6，**改完 Prompt 必须重跑 batch_dify_runs 重建 cache**。
如果你要写论文方法章节：参考 §1.3 创新点 + §3.3.2 六维决策 + §4.2.2 章节结构。

---

## 8. 一句话总结

**Dify Chatflow 把"读论文+图文匹配+做海报规划"这件原本端到端模糊的事，解耦成三个最小职责 Agent，并通过严格的 JSON schema 与后端契约把 LLM 的不确定性约束在可控范围内；同时通过将每次跑产生的规划结果回放到实验 baseline，让 LLM-as-Planner 这个本质上非确定的环节，能够作为论文里可复现、可消融、可量化的研究对象。**
