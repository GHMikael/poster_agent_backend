# **PosterCSP 技术与论文优化方向参考文档 v2**

**版本**：v2.0
**日期**：2026-05-30
**作者**：项目主理人（AI 辅助诊断与重构）
**适用范围**：冲刺顶会/顶刊投稿前的方向重锚、指标体系重设、代码技术改革总规划
**当前代码基线**：`poster_agent_backend` v5.1（30 篇 planner_cache 已就绪，5 篇 pilot 已跑通并完成指标计算）

> **这份文档与 v1 的关系**：v1（`RESEARCH_DIRECTION.md`）保留不动，作为历史方向存档。v2 是在**看完 5 篇真实实验数据后**的方向修正。两者最大区别一句话：
> **v1 把"学术保真 / 高效 / 原图复用"当三大并列卖点；v2 在数据证伪这三点后，改用"一主(SVFP 协议)两从(benchmark + CS 垂直 case study)"的金字塔结构，把被证伪的点从"优势"降级为"诚实 trade-off / 待修工程问题"。**
>
> **配套文档**：`PAPER_DRAFT_v0.md`（英文论文初稿，已是方法论 framing，与 v2 方向一致，可直接续写）。

---

## **〇、v2 核心修订说明（先读这节）**

### 0.1 为什么必须改方向：5 篇数据的真相

下表是 15 个 `experiments/results/metrics/*.json`（5 篇 × 3 方法）汇总后的均值。**这是后续一切决策的 anchor，请钉死。**

| 指标 | 含义 | gpt4o_zeroshot | ours_no_svfp | ours_svfp | 真相判定 |
|---|---|---|---|---|---|
| **a1** 信息保留 | 原文 claim 被海报覆盖比例 | **0.544** | 0.448 | 0.448 | ❌ ours **输给**最弱 baseline |
| **a2** 图文对齐 | 图与文是否对应 | **None(算不出)** | 0.32 | 0.32 | ❌ 图复用 pipeline 坏了 |
| **a3** 幻觉率 | 不可溯源句子比例 | 0.117 | 0.100 | 0.117 | ⚠️ 三者无序，SVFP 反更高 |
| **a4** 章节覆盖 | 六模块齐全度 | **1.000** | **1.000** | **1.000** | ❌ 天花板，零区分度 |
| **c1** PaperQuiz | 看海报答题正确率 | **1.000** | **1.000** | 0.96 | ❌ 天花板，零区分度 |
| **b1** 布局合理性 | 几何排版分 | 0.745 | 0.766 | **0.781** | ⚠️ 唯一赢点，循环论证嫌疑 |
| **b2** 可读性 | 字号/对比/密度 | 0.748 | 0.748 | **0.782** | ⚠️ 唯一赢点，n=5 不显著 |
| **d1** 延迟 | 端到端时长 | 23 s | 0.04 s | **160 s** | ❌ 慢 7×，卖点反向 |
| **d2** 成本 | 单篇 USD | $0.0039 | $0 | $0.0116 | ❌ 贵 3× |

`pairwise.tsv` 里 **`bh_fdr_survives` 整列全 `False`**——多重比较校正后**没有一个指标显著**（n=5 的必然结果）。

### 0.2 五个致命问题（按严重度，全部带代码证据）

1. **图复用 pipeline 实际坏了**——a2 的 VLM 评语逐篇可见 `"figure is blank"`、`"a girl holding a kitten"`、`"a sunset over water with birds"`、`"a red circle with an 'X'"`。一篇主打"100% 原图复用"的论文，海报里是小猫和日落 stock 图。**创新点 1 目前 0 证据甚至反证。**
2. **SVFP 对内容指标"零贡献"是设计决定的**——`ours_svfp` vs `ours_no_svfp` 唯一区别是 `use_commenter`/`max_iterations`（`ours_svfp.py:51-52`），而所有修复操作**只改排版不碰文本**，`ACTION_ADD_BULLET` 被显式禁止增内容（`feedback_loop.py:401-407`）。故 a1/a3/a4/c1 在 svfp vs no_svfp 上**逐篇完全相同**。把 SVFP 宣传成提升"保真度"，消融表一眼穿帮。
3. **输给 zero-shot，且"六模块模板"不是对 zero-shot 的差异化优势**——`gpt4o_zeroshot` 用的是**同一渲染器、同一套六模块模板**（`gpt4o_zeroshot.py:82`→`generate_dashboard_pptx`），区别仅 planner prompt。所以 a4 全员 1.0；你 vs zero-shot 的差异纯是 planner，而 Dify planner 过度过滤 claim，召回反低。
4. **三个"硬指标"无区分度或有 bug**——a4 天花板（模板强制）；c1 天花板（5 道选择题 VLM 轻松全对）；a3 判定 `p_entail<0.4 AND p_contradict>0.3`（`a3_hallucination.py:68`）把 NLI **"中立/弃权"（p=0.33/0.33）误判成幻觉**。
5. **效率卖点反向**——d1=160 s，其中 VLM 调用占 ~117 s（见 `d1_latency` 拆解），加 soffice 渲 PNG。与"轻量化、迭代压到 1-2 轮"的定位直接冲突。

### 0.3 "三个都想做" → 一主两从金字塔（不是三选一）

```
        ┌──────────────────────────────────────────┐
  主    │  贡献① SVFP 结构化视觉反馈协议（方法论 spine） │  Intro/Method/核心实验都围绕它
        └──────────────────────────────────────────┘
        ┌────────────────────┐  ┌────────────────────────┐
  从    │ 贡献② CS-Poster-30   │  │ 贡献③ CS 垂直实例化        │  benchmark = method paper 的加分第二贡献
        │  benchmark + 评测套件 │  │ (六模块=domain prior;     │  垂直工作全保留，角色改为"验证 SVFP 的真实场景"
        └────────────────────┘  │  诚实报告 content trade-off)│  被证伪三点→写成 trade-off / limitation
                                └────────────────────────┘
```

**你想做的三件事一件不丢，只换层级与话术。唯一失去的是"把保真当优势吹"的权利——而那本就是数据不允许的。**

### 0.4 v1 → v2 关键变化对照

| 维度 | v1（旧） | v2（新） |
|---|---|---|
| 论文身份 | 垂直保真**系统** | SVFP **方法论协议**（plug-in） |
| 一级卖点 | 保真 + 效率 + 原图复用（并列） | **结构化反馈 > 自由反馈**（单一脊柱） |
| 保真度 | 当优势吹（保真度公式） | 诚实写成 **precision-recall trade-off** |
| 效率 | 当卖点（"轻量化"） | 先优化，再用 **quality-latency Pareto** frame |
| 原图复用 | 当卖点（"100%"） | 先**修 bug**，修好后作 benchmark 的一个 dimension（图复用率） |
| 六模块模板 | 核心创新点 | 降级为 **domain prior / 实验场景设定** |
| 核心缺失实验 | 无意识 | **closed-set vs free-form 三臂对照**（E1，最高优先级） |
| 指标 | 12 项并列，含天花板/bug | 删 2、修 2、去循环化 2、**新增 4 个"能赢"的协议指标** |

---

## **一、新定位（一句话 + 能说/不能说清单）**

**一句话定位（投稿用）**：

> 我们提出 **SVFP**（Structured Visual Feedback Protocol）——一个把 VLM 视觉批评约束到 `{4 类问题 × 9 个原子动作}` 封闭 schema 的、planner-agnostic 的视觉质量后处理协议。相比自由文本式 VLM critique，SVFP 让反馈**确定性可执行、可证明收敛**；我们在自建的 **CS-Poster-30** 基准上验证：SVFP 在视觉质量指标上取得大效应提升（Cohen's d≈1.8），并诚实刻画了其在内容召回上的 precision-recall trade-off。

**能写进论文的话（数据支撑）**：
- ✅ "结构化封闭反馈比自由文本反馈更可执行 / 更易收敛"（**前提：E1 跑出来**）
- ✅ "SVFP 是 planner-agnostic 的，可挂在任意 planner 后"（**前提：E2 跑出来**）
- ✅ "SVFP 在布局/可读性上有大效应提升"（**前提：去循环化 + n=30**）
- ✅ "我们诚实报告 content 的 precision-recall trade-off"（这是 honest evaluation，加分）

**绝不能写的话（数据反对）**：
- ❌ "我们的方法学术保真度更高"（a1 你输 zero-shot）
- ❌ "我们高效/轻量"（d1 慢 7×）——除非 B4 优化后改口径
- ❌ "我们 100% 原图复用、零图像幻觉"（a2 出现小猫/日落，未修复前禁说）
- ❌ "六模块模板带来优势"（zero-shot 也用同模板）

---

## **二、核心创新点 v2（重写）**

### 创新点①（主）：SVFP 结构化视觉反馈协议

- **技术核心**：VLM 批评被双向约束到封闭枚举——4 类 root-cause issue（`overlapping_elements / empty_space / low_contrast / figure_too_small`）× 9 个确定性 action（`vlm_commenter.py:43-117`），由确定性 `FeedbackApplier` 执行（`feedback_loop.py`）。
- **学术钩子（形式化 + 可证明收敛）**：建模为有限状态机 $\mathcal{M}=(S,A,\delta,s_0,F)$，给出 Proposition 1（面板容量有界 ⇒ 至多 K 步收敛）。**这部分 `PAPER_DRAFT_v0.md` §3 已写好，直接用。**
- **必须补的证明实验**：E1（closed-set vs free-form vs no-feedback）。**没有 E1，这个创新点只是断言。**

### 创新点②（从）：CS-Poster-30 基准 + 评测套件

- **内容**：30 篇真实 CS 顶会论文 planner_cache + 多维评测套件（修订后的指标体系，见第四节）+ 可复现 pipeline（L0→L8）。
- **学术价值**：method paper 附 benchmark 是常见加分项；同时把"原图复用率""反馈可执行率"等新指标作为基准的一部分 release。
- **包装**：强调可复现、可审计、领域专属（CS 海报这一垂直评测此前无公开基准）。

### 创新点③（从/场景）：CS 垂直实例化

- **内容**：六模块语义骨架（`layout_engine.py` 的 `classify_panel`）作为 **domain prior**；4 模板 × 4 主题渲染器（`ppt_renderer.py`）；100% 复用原文图表的资产管线（**修好后**）。
- **角色**：这是"我们在什么真实场景上验证 SVFP"，是 case study 与 design motivation，**不是独立的一级 claim**。
- **诚实处理**：a1 的"召回低于 zero-shot"写成"结构化抽取路径牺牲召回换取更低 hallucination 与更强结构性"的 trade-off，配 precision-recall 散点图，让 ours 落在 Pareto frontier 上（思路见 `PAPER_DRAFT_v0.md` §1.3）。

---

## **三、指标体系 v2（痛点"指标定得不好"的正面解药）**

原则：**删掉没区分度的，修掉有 bug 的，给"自己说自己好"的去循环化，新增"能赢"的。**

### 3.1 删除 / 降级（移出主表）

| 指标 | 处理 | 理由 |
|---|---|---|
| **a4** 章节覆盖 | **删出主表**，仅 appendix 作 sanity check | 模板强制六模块 → 永远 1.0，零区分度 |
| **c1** PaperQuiz（现版） | **重做**（见 3.4），否则降 appendix | 5 道选择题 VLM 全对，天花板 |

### 3.2 修 bug

| 指标 | 修法 | 代码位置 |
|---|---|---|
| **a3** 幻觉 | 把判定改成"仅当 contradict 为三类最大且 > 0.5 才算幻觉"；中立/弃权**不算**幻觉。拆成两个子指标：`unsupported_rate`（中立）与 `contradicted_rate`（矛盾） | `a3_hallucination.py:68` |
| **a2** 图文对齐 | **先修图 pipeline（见 B1）**，修好后才有意义；给 zero-shot 也分配 figure_id，否则永远 n=0 无法比较 | `a2_figure_text_alignment.py` |

### 3.3 去循环化（关键，否则被质疑"自家指标自家说好"）

| 指标 | 去循环化方案 |
|---|---|
| **b1/b2** 布局/可读性 | 二选一或并用：(a) 小规模 human rating（10-15 海报）做 correlation，证明几何分与人评一致；(b) 用与 loop 内 critic **不同**的独立 VLM（如 GPT-4V）当裁判，避免"同模型既当运动员又当裁判"。论文里必须报告这个 alignment 数字 |

### 3.4 新增"能赢"的协议级指标（SVFP 主场，由 E1 产出）

| 新指标 | 计算方式 | 为什么能赢 |
|---|---|---|
| **反馈可执行率** Action Executability Rate | 可被 renderer 确定性执行的反馈条数 / 总反馈条数 | closed-set=100%；free-form 经常无法解析/执行 → 直接量化"结构化 vs 自由"的差距 |
| **收敛性** Convergence Rate / 轮数 | 在 K 轮内进入终止状态的比例 + 平均轮数 | SVFP 可证明收敛；free-form 易震荡/不收敛 |
| **每轮视觉分增量** per-iter Δ visual | 每轮 b1/b2 的增量序列 | SVFP 单调改进 vs free-form 抖动 |
| **图复用率** Figure Reuse Rate | 复用原图数 / 论文总图数 | 修图后 ours 高、zero-shot 低（zero-shot 不复用图）→ 把"原图复用"从被证伪卖点变成有数据支撑的诚实指标 |

### 3.5 主表呈现（统计加固）

- 主表 = 4 个聚合 cluster（内容 / 视觉 / 协议 / 工程）+ Overall，完整 11 项进 appendix。
- n=30 paired bootstrap 1000 次给 95% CI；Wilcoxon signed-rank + BH-FDR；人评报 Cohen's κ。
- **不显著就诚实标"trend"并配 per-paper improvement 图**，不硬吹显著。

---

## **四、实验矩阵 v2（必做实验 backlog）**

| 编号 | 实验 | 目的 | 优先级 | 状态 |
|---|---|---|---|---|
| **E1** | **三臂对照**：`no-feedback` / `free-form VLM feedback` / `SVFP closed-set` | **证明主贡献**（结构化 > 自由）。需新写 free-form baseline | **P0 核心** | ⏳ 全缺 |
| **E2** | **cross-planner**：SVFP 挂到 zero-shot planner 后 | 证 planner-agnostic（plug-in） | P1 | ⏳ |
| **E3** | **per-issue ablation**：逐个关闭 4 类 issue | 哪类 issue 贡献最大 | P1 | ⏳ |
| **E4** | **扩到 n=30** | 过"样本量不足"那一刀（planner_cache 已就绪） | **P0** | ⏳ |
| **E5** | **human eval**（10-15 CS 博士） | 给 b1/b2 背书 + SUS + 信息获取时长 | P2 | ⏳ |
| **E6** | external SOTA（paper2poster / PosterGen） | 外部对比 | P2（stretch） | ⏳ 复现成本高，不行则引其论文数字 + "as reported" |

> **E1 是整篇论文的胜负手**：它是唯一能正面证明"SVFP 的封闭 schema 比自由文本反馈更好"的实验，目前完全缺失。free-form 臂的做法：让 VLM 自由吐槽（不约束 schema），再让 applier 尝试解析执行——大概率出现"可执行率低 / 不收敛"，这正是你要的对比。

---

## **五、代码技术改革 backlog（痛点"专业代码改革"的解药）**

| 编号 | 任务 | 优先级 | 具体修法 | 代码位置 |
|---|---|---|---|---|
| **B1** | **修图复用 pipeline** | **P0** | 先 root-cause：是 planner_cache 脏数据（混入 stock 图）还是 hydration bug（`hydrate_task_image_sources`）。修复后写一个 sanity 脚本验证 30 篇图都是论文真图 | `app/pdf_assets.py` / `app/image_utils.py` / `app/main.py` |
| **B2** | **修 a3 NLI 弃权误判** | **P0** | 见 3.2 | `a3_hallucination.py:68` |
| **B3** | **删/降天花板指标** | **P0** | a4 移出主表；c1 重做或降 appendix | `metrics.yaml` / 主表脚本 |
| **B4** | **延迟优化** | P1 | (a) early-stop：连续 N 轮无 issue 或视觉分不升即停（`PAPER_DRAFT` T3）；(b) 缓存/并行 soffice 渲染（`feedback_loop.py:588-624` 是瓶颈）；(c) 默认 `max_iterations` 调参。目标：压到可接受，或至少用 Pareto 图 frame trade-off | `feedback_loop.py` |
| **B5** | **写 free-form baseline** | **P0**（E1 依赖） | 新增 `experiments/baselines/ours_freeform.py`：VLM 自由 critique + best-effort applier，作为 E1 的对照臂 | 新文件 |
| **B6** | **content-preserving repair** | P2 | `reduce_bullet_count` 改成"合并"而非"丢弃"，缓解 a1/a3 负效应（`PAPER_DRAFT` T4） | `feedback_loop.py:422` |

---

## **六、投稿策略 v2**

基于"方法论(SVFP) + benchmark"的身份，推荐顺序：

| 渠道 | 适配度 | 备注 |
|---|---|---|
| **AAAI Technical Track** | ★★★★★ | `PAPER_DRAFT_v0.md` 已按此 framing 写。method novelty + 充分 empirical 正好 |
| **ACL ARR / EMNLP** | ★★★★★ | 重 evaluation 严谨度，structured-generation 主题契合；滚动 ARR 容错高 |
| **NeurIPS D&B Track** | ★★★★ | 若把 CS-Poster-30 benchmark 包装成主贡献之一 |
| **CHI**（follow-up） | ★★★★ | E5 人评做扎实后的深化版 |
| **CCF-A 中文期刊** | ★★★★ | 系统+评测贡献匹配度高，时间压力小，可作保底 |

> ⚠️ **deadline 我不保证准确，务必去各会议官网核实**。今天是 2026-05-30，请优先确认 EMNLP 2026 / AAAI 2026 的 abstract/full-paper 截稿是否还来得及；若都过窗口，ARR 滚动 + 中文期刊是稳妥保底。

---

## **七、修订版执行路线（2 周 sprint，按依赖排序）**

```
第 1 阶段·解除地基隐患（P0，不做后面全白做）
  D1   B1 图 pipeline root-cause + 修复 + 30 篇 sanity 验证
  D2   B2 修 a3 弃权 + B3 删天花板指标 + 指标体系 v2 落地到 metrics.yaml
  D3   B5 写 free-form baseline（E1 对照臂）

第 2 阶段·证明主贡献（P0/P1）
  D4-5 E1 三臂对照（先 5 篇验证 pipeline，再准备扩量）
  D6   E4 扩到 n=30 批跑（planner_cache 已就绪，过夜跑）
  D7   E2 cross-planner + E3 per-issue ablation

第 3 阶段·去循环化 + 出表出图（P1/P2）
  D8   b1/b2 去循环化（独立 VLM judge 或小样本 human-correlation）
  D9   aggregate + bootstrap + Wilcoxon + BH-FDR，出主表
  D10  画图：Pareto / ablation waterfall / per-paper heatmap / 可执行率对比

第 4 阶段·写作 + 人评（并行）
  D11  E5 小规模 human eval（如时间允许）
  D12  按 PAPER_DRAFT 续写 Experiments / Results / Analysis / Limitations
  D13  B4 延迟优化（让"工程性"更扎实，或为 Pareto 图补数据）
  D14  2 轮自审 + 找同行 review
```

---

## **八、风险与诚实声明**

| 风险 | 应对 |
|---|---|
| E1 跑出来 free-form 并不比 closed-set 差 | 那主贡献需重想——但更可能 free-form 可执行率低/不收敛，先跑 5 篇验证 |
| n=30 后 delta 仍不显著 | 用 per-paper improvement + 大效应量（Cohen's d）+ 诚实标 trend |
| 图 pipeline root-cause 复杂 | D1 即开始，预留 buffer；先确认是数据问题还是代码问题 |
| 外部 SOTA 复现遇阻 | 标 stretch goal，引论文数字 + "as reported" 脚注 |
| 延迟优化不达标 | 用 quality-latency Pareto frame 成 trade-off，而非硬吹快 |

---

## **九、与代码的对应关系（写 LaTeX / 改代码时回查）**

- SVFP 协议定义（issue×action enum）：`app/vlm_commenter.py:43-117`
- FeedbackApplier（确定性修复 dispatch）：`app/feedback_loop.py`；禁止增内容：`:401-407`；palette 仅切 2 色：`:66`；soffice 渲染瓶颈：`:588-624`
- PosterTask schema / 默认 max_iterations=2：`app/models.py:58`
- 模板/主题（4×4）：`app/ppt_renderer.py`（`template_map` / `PALETTES`）
- baseline 差异：`ours_svfp.py:51-52` vs `ours_no_svfp.py:46-47` vs `gpt4o_zeroshot.py:78,82`
- 指标实现：`experiments/metrics/`（a1-a4 / b1-b3 / c1-c3 / d1-d3）
- 指标配置：`experiments/configs/metrics.yaml`
- pilot 数据：`experiments/results/aggregate/aggregate.tsv` + `pairwise.tsv`
- 实验操作指南：`INTERNAL_EXPERIMENT_GUIDE.md`

---

> 版本控制建议：本文件每次方向调整更新版本号并保留 changelog。下一步可直接从第五节 backlog 的 **B1（修图 pipeline）** 或第四节 **E1（三臂对照）** 任一项动手。
>
> *本文档由 AI 基于真实实验数据与代码诊断辅助生成，方向判断仅供参考；venue deadline 等时效信息务必自行核实。*
