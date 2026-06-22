# BM25 低置信度反思机制设计

**日期**：2026-06-22
**作者**：Claude (brainstorming with user)
**状态**：草案，待用户审阅

## 背景

当前 `FinancialQAAgent` 流程为：加载证据 → BM25 检索 → 单轮 LLM 调用 → 解析答案。
配置 `agent.max_rounds: 1`，没有任何基于置信度的重试或反思。

BM25 检索已经在 stats 里输出 `max_bm25_score`、`avg_bm25_score`、`selected_sources`，
但 LLM 调用环节并未消费这些信号——即使检索置信度很低（最强片段都很弱，或多个片段
打分接近没有明显优胜者），系统仍无条件信任首轮答案。

**赛题得分公式**：`FinalScore = 100 × Accuracy × (0.7 + 0.3 × TokenScore)`，
`TokenScore = max(0, min(1, (5,000,000 - TotalTokens) / 5,000,000))`。

- 准确率占 70%（主权重）
- Token 效率占 30%（次权重）
- 当前 best 约 21.81 分，token 远低于 5M 预算，仍有反思空间

## 目标

- **主要目标**：提升准确率（直接乘到 70% 主权重）
- **次要约束**：控制 token 增长，避免挤压 TokenScore
- **不做的事**：
  - 不重新检索（复用首轮 evidence，避免重复消耗 token）
  - 不引入新模型（沿用同一个 LLMClient）
  - 不做多轮反思循环（最多首轮 + 反思一次）

## 整体流程

在 `FinancialQAAgent.answer_question` 末尾插入**条件性反思环节**：

```
[现有流程] 证据加载 → BM25 检索 → 构建 prompt → LLM → 解析首轮答案
   ↓
[新增] 检查 BM25 置信信号
   ↓ (满足触发条件)
反思环节：把「问题 + 选项 + 检索证据 + 首轮答案」喂给同一个 LLM，
          让它做"对每个选项逐一核验"的二次推理，输出 KEEP / CHANGE 决策
   ↓
返回（首轮 OR 反思答案，取决于是否触发）
```

关键点：
- 只在低置信度时触发，高置信度题目不消耗额外 token
- 复用现有 LLMClient 和首轮 evidence，不引入新依赖
- 触发逻辑可配置（阈值、开关），方便 A/B 调参

## 触发条件细节

**两个触发信号，OR 关系（满足任一即触发）**：

### 1. 最高分阈值

`max_bm25_score < low_score_threshold`

- 当前数据观察：高置信题目 `max_bm25_score` 通常在 200+
- 触发阈值默认 **80**（可在 config 调）
- 含义：最强命中片段都偏弱，说明文档里没有强匹配证据

### 2. Top1/Top2 分差

`(top1_score - top2_score) / top1_score < top_gap_ratio`

- top1/top2 取自排序后的 `selected_sources[0]` 和 `selected_sources[1]`
- 需要从 `selected_sources` 拿到 top2 score（当前 stats 里只有 max 和 avg，需扩展）
- 默认阈值 **0.15**（即 top2 与 top1 分差 < 15%）
- 含义：多个片段打分接近，没有明显优胜候选，模型可能选错聚焦点
- **边界**：如果 `len(selected_sources) < 2`，跳过此条件（没有竞争对手不算"分差小"）

### 例外短路

- `retrieved_windows == 0`（BM25 完全没命中）→ 不反思，直接返回首轮答案
  - 理由：反思也救不回来，纯浪费 token
- 题目 `answer_format == "multi"` → 仍走反思，但 prompt 设计要让模型逐选项验证
  而非整体推翻

### 新增 config 字段

`retrieval` 同级新增 `reflection`：

```yaml
reflection:
  enabled: true
  low_score_threshold: 80.0
  top_gap_ratio: 0.15
  log_decisions: true   # 记录每题是否触发、为什么触发
```

## 反思 prompt 设计与实现

### 反思 prompt 结构

沿用 PromptBuilder 风格，新增 `ReflectionPromptBuilder`：

```
你是一位金融文档分析专家。请对下面的初答进行复核。

【文档】{evidence}              ← 复用首轮检索结果，不重新检索
【问题】{question}
【选项】
A. ...
B. ...
C. ...
D. ...

初答：{first_answer}

请按以下步骤分析：
1. 找出文档中支持初答的具体证据（引用原文片段）
2. 对每个选项逐一判断：是否有明确证据支持/反驳
3. 如果初答正确，输出 "KEEP {letter}"
4. 如果发现错误，输出 "CHANGE {letter}"

最终输出格式：KEEP/CHANGE {答案字母}
```

### 实现切分

1. **新增 `ReflectionPromptBuilder`**（与 `PromptBuilder` 并列，同文件 `src/agent/agent.py`）
   - 接收首轮 `answer` 和原始 `evidence`
   - 复用 `ContextManager.truncate` 控制长度

2. **在 `BM25Retriever` 的 stats 中扩展 `top1_score` / `top2_score`**
   - 当前已有 `selected_sources` 列表，只需暴露前两个的 score
   - 不破坏现有 CSV 字段（仅追加新列）

3. **`FinancialQAAgent.answer_question` 末尾追加反思分支**
   - 计算 `should_reflect(stats)` → bool
   - 若 true，调用 `_reflect(question, evidence, first_answer)` 拿到反思后的答案
   - 合并 token usage（首轮 + 反思两次都要计入）
   - 在结果 dict 里加诊断字段（便于后续调参与人工核对）：
     - `reflected: bool` — 是否触发了反思
     - `first_answer: str` — 首轮答案
     - `reflection_decision: "KEEP" | "CHANGE" | "PARSE_FAIL"` — 反思决策
     - `reflection_trigger_reason: "low_score" | "small_gap" | ""` — 触发原因
   - **采纳策略**：完全信任反思结果（用户已确认），即 `final_answer = reflection_answer`
   - **例外**：`PARSE_FAIL` 时保留首轮答案（fail-safe）

4. **答案解析逻辑**
   - 用正则提取 `KEEP ([A-D]+)` 或 `CHANGE ([A-D]+)`
   - 解析失败 → 保留首轮答案（fail-safe，不引入新错误源）
   - 答案校验仍走现有 `AnswerValidator.validate`

### Token 成本估计

- 反思 prompt 与首轮 prompt 体量相当（同样带 evidence）
- 假设 30% 题目触发反思，整体 token 增长约 30% × 1 ≈ **30%**
- 当前 best 21.81 分对应 token 远低于 5M 预算，有充足空间
- 若实际涨幅挤压 TokenScore，可调高 `low_score_threshold` 让触发更严格

## 测试与验证计划

### 单元测试

沿用现有 pytest 风格，新增到 `tests/`：

1. `test_should_reflect_trigger_conditions`
   - `max_bm25_score < 阈值` → 触发
   - `top1/top2 gap < 阈值` → 触发
   - 两者都不满足 → 不触发
   - `retrieved_windows == 0` → 不触发（短路）

2. `test_reflection_prompt_builder`
   - 包含初答、所有选项、证据
   - 截断行为符合 max_chars 约束

3. `test_reflection_answer_parsing`
   - `KEEP A` → 保留 A
   - `CHANGE B` → 改为 B
   - 输出格式异常 → 保留首轮（fail-safe）
   - 多选题 `KEEP ABC` / `CHANGE ABD` 正确解析

4. `test_agent_with_reflection_disabled`
   - config 关闭反思 → 行为完全等价当前 baseline

### 回归验证（手动）

1. 用同一份 A 组题（100 道），分别在 `reflection.enabled: false / true` 下各跑一次
2. 对比 diagnostics CSV：
   - **反思命中率**：多少题触发了反思
   - **答案变更率**：其中多少题反思后改了答案
   - **token 增长率**：总 token 涨了多少
3. 抽样 10 道触发反思的题，人工核对反思前后答案正确性

### 风险与回退

- 如果反思后整体准确率下降 → 直接关 `reflection.enabled` 即可回退
- 如果 token 涨太多挤压 TokenScore → 调高 `low_score_threshold` 让触发更严格

## 不做的事（YAGNI）

- 不做多次反思循环（`max_rounds` 仍为 1 + 反思 1 次）
- 不为反思单独引入新模型
- 不持久化反思日志到独立文件（复用现有 diagnostics CSV 的扩展字段即可）
- 不重新检索（复用首轮 evidence）
