# 全流程 QA 调试页面设计

## 目标

在现有 BM25 调试工具基础上扩展一个本地页面，用于逐题查看金融 QA 的全流程执行结果。页面左侧展示所有单选题和多选题，点击题目后可启动可勾选的流程，默认执行全部流程。结果区需要清晰展示三类中间与最终产物：意图识别结果、BM25 检索结果、最终答案。

该页面定位为本地调试工具，不替代正式批量运行入口，不新增第三方前端框架。

## 范围

改造现有文件：

- `scripts/bm25_debug_ui.html`：从“单题 BM25 检索页”扩展为“题库 + 可选流程 + 结果面板”的工作台。
- `scripts/bm25_debug_server.py`：新增题库读取和全流程运行接口。
- `src/agent/bm25_debug_service.py`：新增可复用服务函数，支撑题库过滤、意图识别、BM25 检索、答案生成。
- `tests/test_bm25_debug_server.py` 和 `tests/test_bm25_debug_service.py`：覆盖新增接口和服务行为。

不做：

- 不引入 React/Vue 等构建链。
- 不修改正式批量入口 `src.agent.run` 的行为。
- 不实现批量并发跑全量题库。
- 不支持判断题展示，首版仅展示 `answer_format` 为 `mcq` 和 `multi` 的题目。

## 用户流程

1. 用户启动本地服务：

   ```bash
   python scripts/bm25_debug_server.py
   ```

2. 浏览器打开 `http://127.0.0.1:8765`。
3. 页面自动请求题库，左侧展示所有 `mcq` 和 `multi` 题目。
4. 用户可按 `qid`、领域、题型或题干关键词过滤。
5. 用户点击某道题，页面显示题干、选项、领域、题型和 `doc_ids`。
6. 流程勾选项默认全选：
   - 意图识别
   - BM25 检索
   - 最终答案生成
7. 用户点击“启动”。
8. 页面展示本次执行的三个结果区：
   - 意图识别结果
   - BM25 结果
   - 最终答案

## 后端接口

### `GET /api/questions`

读取 `config.data.questions_dir` 下的 `*_questions.json`，合并后只返回 `answer_format in {"mcq", "multi"}` 的题目。

响应：

```json
{
  "ok": true,
  "questions": [
    {
      "qid": "fin_a_001",
      "domain": "financial_reports",
      "answer_format": "multi",
      "question": "...",
      "options": {"A": "...", "B": "..."},
      "doc_ids": ["annual_byd_2024_report"]
    }
  ],
  "stats": {
    "total": 80,
    "mcq": 40,
    "multi": 40
  }
}
```

### `POST /api/run-question`

请求：

```json
{
  "question": {
    "qid": "fin_a_001",
    "domain": "financial_reports",
    "answer_format": "multi",
    "question": "...",
    "options": {"A": "..."},
    "doc_ids": ["annual_byd_2024_report"]
  },
  "steps": {
    "intent": true,
    "bm25": true,
    "answer": true
  },
  "params": {
    "global_top_k": 10
  }
}
```

处理规则：

- `intent=true` 时，使用 `IntentTermSelector` 调用轻量 LLM，从白名单中选择 `_intent_terms`。
- `intent=false` 时，不追加 `_intent_terms`。
- `bm25=true` 时，使用现有 `BM25Retriever` 执行检索并返回 `stats`、`chunks`、`retrieved`。
- `bm25=false` 且 `answer=true` 时，答案生成仍可走 `FinancialQAAgent.answer_question()` 的正式路径，但必须用本次请求的步骤配置构造临时 agent：若 `intent=false`，临时关闭意图词；检索参数使用页面传入值。响应中标明 BM25 面板未单独执行。
- `answer=true` 时，生成最终答案，并返回 token、反思相关字段和错误信息。
- `answer=false` 时，不调用主答题 LLM。

响应：

```json
{
  "ok": true,
  "question": {"qid": "fin_a_001"},
  "steps": {"intent": true, "bm25": true, "answer": true},
  "intent": {
    "enabled": true,
    "terms": ["营业收入", "净利润"],
    "token_usage": {
      "prompt_tokens": 120,
      "completion_tokens": 20,
      "total_tokens": 140
    }
  },
  "bm25": {
    "enabled": true,
    "stats": {"retrieved_windows": 6, "max_bm25_score": 45.2},
    "chunks": []
  },
  "answer": {
    "enabled": true,
    "answer": "AC",
    "first_answer": "AC",
    "prompt_tokens": 1000,
    "completion_tokens": 20,
    "total_tokens": 1020,
    "reflected": false,
    "reflection_decision": "",
    "reflection_trigger_reason": ""
  }
}
```

错误响应沿用现有格式：

```json
{
  "ok": false,
  "error": "question.doc_ids 不能为空"
}
```

## 服务设计

`bm25_debug_service.py` 新增以下职责：

- `load_debug_questions(config=None)`：读取题库并过滤 `mcq/multi`。
- `run_question_flow(question, steps, params=None, config=None)`：统一执行可选流程。
- `run_intent_selection(agent, question)`：封装意图词选择，返回 active question 和 token。
- 复用现有 `run_debug_search()` 作为 BM25 检索实现，避免重复序列化逻辑。

全流程运行需要注意：

- 页面参数只覆盖检索配置，不修改全局配置文件。
- 检索日志在页面调试中默认关闭，避免反复写 `logs/<qid>.json`。
- 答案生成会真实调用 LLM；若缺少 API key，后端返回可读错误，页面保留已完成步骤结果。
- 若用户只勾选最终答案，服务可以直接调用 `FinancialQAAgent.answer_question()`，但需要先把本次请求的检索参数和意图开关写入临时配置，结果中的 BM25 面板显示“未单独执行”。
- 若用户同时勾选 BM25 和最终答案，页面展示的 BM25 结果与最终答案内部使用的检索配置必须一致；允许内部重新检索一次，但不能使用不同的参数或不同的意图开关。

## 前端设计

页面保持工作台风格，沿用原生 HTML/CSS/JavaScript。

布局：

- 顶部工具栏：刷新题库、启动、重置参数。
- 左侧：题目列表和搜索框。每个列表项展示 `qid`、领域、题型和题干摘要。
- 中栏：当前题详情、流程勾选、BM25 参数。
- 右侧：结果面板，按“意图识别结果 / BM25 结果 / 最终答案”分区。

交互状态：

- 初始加载题库时显示 loading。
- 点击题目后高亮选中项。
- 启动期间禁用启动按钮，结果区显示当前状态。
- 单个步骤失败时展示错误，同时保留其他已完成步骤。
- 未勾选步骤显示“未执行”，避免用户误以为缺少结果。

结果展示：

- 意图识别：展示 selected terms、是否启用、token。
- BM25：展示 `query_count`、`chunk_count`、`candidate_count`、`retrieved_windows`、`doc_coverage`、`max_bm25_score`、`avg_bm25_score`，并列出命中片段。
- 最终答案：展示 `answer`、`first_answer`、token、重试次数、反思触发原因和决策。

## 测试设计

后端测试优先覆盖纯函数，不启动真实浏览器。

- `load_debug_questions` 能读取多个题目文件，只返回 `mcq/multi`。
- `run_question_flow` 在只勾选 BM25 时不调用 LLM，并返回 BM25 结构。
- `run_question_flow` 在不勾选 intent 时不会写入 `_intent_terms`。
- `run_question_flow` 在勾选答案时返回 `answer` 结构，并可通过 monkeypatch 避免真实 LLM 调用。
- server 对 `/api/questions` 和 `/api/run-question` 路由分发正确。
- 缺少 `question.doc_ids`、非法 JSON、未知路由返回 `ok=false`。

## 验收标准

- 本地服务可以启动并打开页面。
- 页面自动展示所有单选和多选题。
- 题目列表可以点击选择，并展示题干和选项。
- 三个流程默认全选，且可单独取消。
- 点击启动后，页面能展示意图词、BM25 命中结果和最终答案。
- 不勾选某流程时，该流程不执行且页面明确标识。
- 后端测试通过，现有 BM25 调试测试不回归。
