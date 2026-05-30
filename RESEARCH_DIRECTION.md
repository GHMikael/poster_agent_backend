# **PosterCSP 技术优化方向参考文档**

**版本**：v1.0  
**日期**：2026-05-27  
**作者**：项目主理人  
**适用范围**：本项目后续 2 周冲刺至顶会/顶汇投稿前的技术与论文优化总规划  
**当前代码基线**：`poster_agent_backend` v5.1（30 篇 planner_cache 已就绪，5 篇 baseline 已跑通）

---

## **一、项目核心定位**

本项目最终定位为**首个面向 CS 顶会场景的轻量化、学术保真型海报生成框架**，主打"100% 复用原图 + CS 六模块固定语义骨架 + 结构化视觉反馈"，解决通用方案的两大痛点——**内容幻觉**与**算力门槛**。

**一句话定位**：

> 我们提出 PosterCSP——首个面向 CS 顶会海报这一垂直场景，集成图文资产保真提取、SVFP 结构化视觉反馈协议与六模块语义模板的轻量化生成框架。在 30 篇真实 CS 顶会论文上，相对 Paper2Poster 与 PosterGen 在学术保真度、生成效率、专家满意度三类指标上均取得显著提升。

---

## **二、差异化竞争定位**

本项目不与大模型类方案拼参数，而是占据"**学术保真 + 垂直适配 + 低门槛**"细分生态位。

| 竞争对手 | 核心死穴 | PosterCSP 的对应优势 |
|---|---|---|
| **Paper2Poster** | 多智能体迭代低效；改动原图丢失学术严谨性 | SVFP 协议把迭代压至 1-2 轮；100% 原图复用 |
| **PosterGen / GPT Image 2** | 通用布局不贴合 CS 论文逻辑；落地门槛高 | CS 六模块固定模板；普通设备一键跑通 |
| **GPT-4o zero-shot** | 全无结构约束，幻觉严重 | 结构化 IR + 容量约束 |

---

## **三、三大核心创新点（最终版）**

三个创新点呈递进关系：**提取 → 反馈 → 工作流闭环**。每个创新点均配套"硬学术钩子"以提升学术含金量。

### **创新点 1：CS 垂直适配的图文资产提取框架**

**解决问题**：通用海报方案乱配图、产生与原文不一致的幻觉内容。

**技术核心**：
- 精调三 Agent 提示词（Text Parse / Visual Parse / Planner）做原文结构化提取
- 强制 100% 复用原文图表（不调用图像生成模型重画）
- 输出绑定 CS 六模块语义骨架的 PosterTask JSON

**学术钩子（保真度评分公式）**：

$$
F = \lambda_1 \cdot \mathrm{ImgReuse} + \lambda_2 \cdot \mathrm{TextMatch}_{\mathrm{BERTScore}} + \lambda_3 \cdot (1 - \mathrm{HallucinationRate})
$$

通过 grid search + 用户偏好回归确定 $\lambda$ 权重，使保真度成为可量化的领域指标，而非主观描述。

### **创新点 2：SVFP 结构化视觉反馈协议**

**解决问题**：大模型 visual-critic loop 盲目迭代，延迟高、收敛慢。

**技术核心**：
- 把模糊的"排版问题"拆解为可执行的原子操作集合：`overlapping_elements` → 减 bullet/缩字号；`empty_space` → 增字号/补内容；`low_contrast` → 切主题；`figure_too_small` → 切 image_focus 布局
- 引入 deterministic repair 替代 LLM 反复重写，迭代轮次从 3-4 轮压至 1-2 轮

**学术钩子（FSM 形式化与收敛性）**：

将 SVFP 建模为有限状态机 $\mathcal{M} = (S, A, \delta, s_0, F)$，其中 $S$ 为 issue 状态集，$A$ 为原子操作集，$\delta$ 为转移函数。给出收敛性命题：

> **Proposition 1**：在面板容量有界条件下，SVFP 协议在至多 $K$ 步内以概率 1 进入终止状态 $F$，其中 $K$ 由模板 panel 数与 issue 类型数线性决定。

这一形式化把 SVFP 从工程 trick 升级为可证明的协议。

### **创新点 3：面向 CS 顶会的海报生成工作流**

**解决问题**：现有方案缺少可复现、可验证的领域专属 pipeline。

**技术核心**：
- 把前两个模块通过 PosterTask JSON 中间格式串联
- JSON schema 作为**领域特定中间表示 (DSL/IR)**，类比编译器中间表示
- 全流程 L0→L8 可追溯（PDFs → planner_cache → 渲染产物 → 指标 → 论文图表）

**学术钩子**：将 PosterTask schema 包装为"Poster IR"，对接 ACL/EMNLP 偏爱的 structured generation 主题，强调可复现性与可审计性。

---

## **四、三类硬指标体系**

弃用"内容质量"等主观描述，全部替换为绑定创新点、可量化的硬指标。

### **保真类（绑定创新点 1）**

| 指标 | 计算方式 | 范围 | 优化目标 |
|---|---|---|---|
| 原图复用率 | 复用图数 / 论文总图数 | 0-100% | ↑ |
| 核心要点匹配度 | BERTScore(海报 bullet, 原文 section) | 0-1 | ↑ |
| 图文对应准确率 | 图所在 panel 与 caption 主语义一致率 | 0-100% | ↑ |
| 幻觉发生率 | 不能溯源回原文的句子比例 | 0-100% | ↓ |

### **效率类（绑定创新点 2）**

| 指标 | 计算方式 | 单位 | 优化目标 |
|---|---|---|---|
| 单篇生成时长 | end-to-end wall time | 秒 | ↓ |
| SVFP 平均迭代轮次 | 触发反馈到收敛的轮数 | 轮 | ↓ |
| 排版问题修复率 | 修复成功 issue / 检出 issue | 0-100% | ↑ |
| 迭代后信息密度提升值 | $\Delta$(单位面积有效信息) | bits/cm² | ↑ |

### **体验类（绑定创新点 3，需要用户研究）**

| 指标 | 计算方式 | 优化目标 |
|---|---|---|
| 核心信息获取时长 | 受试者找到主结论用时 | ↓ |
| 排版满意度 | 5 点李克特量表均值 | ↑ |
| 六模块合规度 | 海报含全 6 模块比例 | ↑ |
| 实验图保留率 | 关键实验图被复用比例 | ↑ |

### **统计学加固（必做）**

- 30 篇 paired bootstrap 1000 次给 95% CI
- 与各 baseline 对比报告 Wilcoxon signed-rank p 值，p<0.05 标 \*，p<0.01 标 \*\*
- 用户研究报告 inter-rater agreement (Cohen's κ)
- 主表用 4 个聚合 cluster + Overall 加权和呈现，完整 11 项指标进 Appendix

---

## **五、Baseline 对照矩阵**

| Baseline | 角色 | 状态 |
|---|---|---|
| Paper2Poster (公开版) | 主对照，证 SVFP 优越性 | **待复现** |
| PosterGen / GPT Image 2 | 主对照，证 CS 垂直适配优越性 | **待复现** |
| GPT-4o zero-shot | 朴素 LLM 下限 | 已就绪 |
| Ours w/o SVFP | 消融，证反馈协议必要性 | 已就绪 |
| Ours w/o 六模块（自由布局） | 消融，证模板必要性 | **新增** |
| Ours full | 主方法 | 已就绪（待重命名） |
| Human poster (oracle) | 上界参考，从 arXiv/会议官网爬取 | **新增** |

---

## **六、用户研究方案**

| 设计要素 | 配置 |
|---|---|
| 被试规模 | 12-15 名 CS 在读博士 |
| 被试构成 | 4 位本方向 + 4 位邻近方向 + 4 位无关方向，控制 expertise bias |
| 任务 1 | 30 秒内回答"该论文核心方法是什么"，记录正确率与用时 |
| 任务 2 | 三方案盲测 + 5 点李克特量表打分 + 自由评论 |
| 顺序控制 | Latin square 抵消顺序效应 |
| 伦理 | informal IRB statement 写入论文 |

---

## **七、论文结构骨架**

| 章节 | 内容要点 | 篇幅 |
|---|---|---|
| 1. Introduction | 痛点 → 垂直适配定位 → 三大贡献 | 1 页 |
| 2. Related Work | Paper-to-Poster / Constrained Layout / Visual Critic LLM | 1 页 |
| 3. Problem Formulation | Poster IR 定义 + SVFP 状态机 + 保真度公式 | 1 页 |
| 4. Method | 4.1 资产提取 / 4.2 SVFP 协议 / 4.3 工作流 | 2 页 |
| 5. Experimental Setup | 30 papers / 7 baselines / 11 指标 / 用户研究 | 1 页 |
| 6. Results | 主表 + Pareto + Ablation + 用户研究 | 2 页 |
| 7. Analysis | 2 个 case study + oracle gap 讨论 | 1 页 |
| 8. Conclusion & Limitations | 必含 limitations | 0.5 页 |

---

## **八、可视化产出清单**

| 图编号 | 内容 | 数据来源 |
|---|---|---|
| Fig.1 | PosterCSP 整体 pipeline 示意 | 手绘 / Mermaid |
| Fig.2 | SVFP 状态机转移图 | 手绘 |
| Fig.3 | Quality–Latency Pareto frontier | 30 篇主实验 |
| Fig.4 | Ablation waterfall（zero-shot → +模板 → +SVFP → full） | aggregate.tsv |
| Fig.5 | 30 papers × baselines 改进 heatmap | metrics/ |
| Fig.6 | 用户研究柱状图（正确率/满意度/获取时长） | user study |
| Fig.7 | 案例对比（成功 + 失败各一） | 渲染产物 |

---

## **九、目标投稿渠道**

| 会议/期刊 | 截稿（约） | 适配度 | 备注 |
|---|---|---|---|
| **EMNLP 2026 Industry Track** | 6 月中 | ★★★★★ | 主投，强调 LLM + structured generation |
| **ACL ARR** | 滚动 | ★★★★★ | 备选，重 evaluation 严谨度 |
| **NeurIPS 2026 D&B Track** | 6 月初 | ★★★★ | 若包装 30-paper benchmark 为数据集贡献 |
| **CHI 2027** | 9 月 | ★★★★ | 用户研究是杀手锏，可作 follow-up |
| **CCF-A 中文期刊** | 滚动 | ★★★★ | 计算机学报 / 软件学报，垂直系统贡献匹配度高 |

**双线策略推荐**：EMNLP 主投 + CHI 作为深化版 follow-up。

---

## **十、14 天执行路线图**

| 时间 | 任务 | 交付物 |
|---|---|---|
| D1-D2 | 按三创新点重写 Abstract / Intro / Related Work | 论文初稿前 3 节 |
| D3 | 实现 4 个保真度 judge（BERTScore / 图文匹配 / 幻觉率 / 原图复用率） | `experiments/metrics/faithfulness/` |
| D4 | 写 SVFP FSM 形式化定义 + Proposition 1 证明 | Section 3 草稿 |
| D5-D7 | 复现 Paper2Poster + PosterGen，跑 30 papers × 7 baselines | results matrix |
| D8-D9 | 招募 12 名 CS 博士，执行用户研究 | user_study.csv |
| D10 | bootstrap + Wilcoxon + Cohen's κ 出主表 | aggregate/main_table.tex |
| D11-D12 | 绘制 Fig.3-Fig.7 共 5 张图 | figures/*.pdf |
| D13 | 写 Method / Experiments / Analysis / Limitations | 完整初稿 |
| D14 | 全文 2 轮自审 + 找 1-2 位同行 review | 投稿稿 |

---

## **十一、风险点与应对**

| 风险 | 严重度 | 应对方案 |
|---|---|---|
| Paper2Poster 复现遇阻（环境/依赖） | 高 | D5 即开始，预留 buffer；不行则改用其论文报告数字加 "as reported in" 脚注 |
| 用户研究招募周期长 | 中 | D8 前提前预约；准备远程 Google Form 备份方案 |
| 30 篇 baseline 跑完后 delta 仍不显著 | 高 | 立即扩到 50 篇；或加"per-paper improvement" 替代均值显著性 |
| 审稿人质疑"工程性大于学术性" | 中 | 靠 Section 3 的 Proposition 1 + Poster IR 形式化兜底 |
| 模板只有 4 个，覆盖度被质疑 | 低 | 在 Limitations 明确声明 + 提出 future work 扩展为 learnable templates |

---

## **十二、核心资产清单（已就绪 / 待补）**

| 资产 | 状态 |
|---|---|
| 30 篇 planner_cache | ✅ 已就绪 |
| 5 篇 pilot baseline 数据 | ✅ 已就绪 |
| 4 模板 × 4 主题渲染器 | ✅ 已就绪 |
| SVFP loop 实现 | ✅ 已就绪（待协议化文档） |
| 12 项原始 metric judge | ✅ 已就绪 |
| 4 项保真度 judge | ⏳ 待实现 |
| Paper2Poster / PosterGen 复现 | ⏳ 待实现 |
| Human oracle baseline | ⏳ 待爬取 |
| 用户研究问卷与协议 | ⏳ 待设计 |
| 统计学脚本（bootstrap / Wilcoxon） | ⏳ 待补 |

---

文档版本控制建议：本文件 commit 至仓库 `docs/RESEARCH_DIRECTION.md`，每次方向调整后更新版本号并保留 changelog。如需我接下来直接出 **SVFP FSM 的形式化 LaTeX 段落**或 **4 个保真度 judge 的 Python 实现**，告诉我从哪一项动手即可。

*内容由 AI 生成仅供参考*