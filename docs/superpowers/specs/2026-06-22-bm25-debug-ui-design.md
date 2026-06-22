# BM25 调试页面设计

## 目标

提供一个本地 Web 页面，用于人工测试当前项目的 BM25 检索效果。用户可以上传或粘贴题目 JSON，选择题目，调整 BM25 参数，点击搜索后查看命中的文档片段、分数、位置和诊断统计。

该工具只用于检索调试，不调用 LLM，不生成答案，不影响主运行入口。

## 范围

新增：

- `scripts/bm25_debug_server.py`：本地 HTTP 服务，复用现有文档读取和 BM25 检索逻辑。
- `scripts/bm25_debug_ui.html`：静态调试页面。
- 后端相关测试：覆盖 JSON 解析、参数覆盖、检索响应和错误响应。

不新增第三方依赖。前端使用原生 HTML/CSS/JavaScript；后端使用 Python 标准库 `http.server`。

## 用户流程

1. 运行本地服务：

   ```bash
   python scripts/bm25_debug_server.py
   ```

2. 浏览器打开服务地址，例如 `http://127.0.0.1:8765`。
3. 上传或粘贴题目 JSON。
4. 如果 JSON 是题目数组，页面显示题目列表，并支持按 `qid`、`domain`、`answer_format` 过滤。
5. 选择一道题，调整 BM25 参数。
6. 点击搜索。
7. 页面展示检索统计和命中片段。

## 输入格式

支持两种题目输入：

### 单题对象

```json
{
  "qid": "demo_001",
  "domain": "financial_reports",
  "question": "公司2023年的营业收入和净利润是多少？",
  "options": {
    "A": "营业收入100亿元，净利润8亿元",
    "B": "营业收入50亿元，净利润2亿元",
    "C": "营业收入100亿元，净利润亏损",
    "D": "未披露"
  },
  "answer_format": "mcq",
  "doc_ids": ["doc1"]
}
```

### 题目数组

上传赛题原始 `*_questions.json` 形式的数组。页面从数组中选择单题发起检索。

## 可调参数

页面参数面板支持覆盖当前 BM25 配置：

- `bm25_k1`
- `bm25_b`
- `chunk_size_chars`
- `chunk_overlap_chars`
- `min_chunk_chars`
- `expand_before_chars`
- `expand_after_chars`
- `merge_gap_chars`
- `per_doc_min`
- `per_doc_max`
- `global_top_k`
- `max_total_chars`
- `min_score`
- `max_query_terms`

为了降低 UI 复杂度，调试页中的 `chunk_size_chars`、`per_doc_max`、`global_top_k` 和 `max_total_chars` 使用单个数值覆盖，不做按领域或按题型的嵌套配置编辑。

## 后端设计

### 服务职责

`scripts/bm25_debug_server.py` 提供两个接口：

- `GET /`：返回 `scripts/bm25_debug_ui.html`。
- `POST /api/search`：执行 BM25 检索。

### 请求体

```json
{
  "question": {
    "qid": "demo_001",
    "domain": "financial_reports",
    "question": "...",
    "options": {},
    "answer_format": "mcq",
    "doc_ids": ["doc1"]
  },
  "params": {
    "bm25_k1": 1.5,
    "bm25_b": 0.75,
    "chunk_size_chars": 1400,
    "global_top_k": 6
  }
}
```

### 处理流程

1. 读取项目 `config/config.yaml`。
2. 从 `config["retrieval"]` 复制基础检索配置。
3. 将页面传入的参数覆盖到检索配置。
4. 强制关闭 retrieval logger，避免页面调试反复写 `logs/<qid>.json`。
5. 使用 `FinancialQAAgent(config)._load_evidence(question)` 读取题目对应全文。
6. 使用 `BM25Retriever(retrieval_cfg).retrieve(question, evidence)` 执行检索。
7. 将 `retrieved`、`stats`、`selected_sources` 和命中片段正文返回给页面。

### 响应体

成功响应：

```json
{
  "ok": true,
  "question": {
    "qid": "demo_001",
    "domain": "financial_reports"
  },
  "stats": {
    "retrieval_method": "bm25_window",
    "query_count": 2,
    "chunk_count": 18,
    "candidate_count": 7,
    "retrieved_windows": 3,
    "max_bm25_score": 12.34,
    "avg_bm25_score": 8.9
  },
  "chunks": [
    {
      "doc_id": "doc1",
      "source": "financial_reports/doc1",
      "start": 1200,
      "end": 3100,
      "score": 12.34,
      "query_types": ["question_options", "numbers"],
      "text": "..."
    }
  ],
  "retrieved": [
    {
      "doc_id": "doc1",
      "source": "financial_reports/doc1",
      "relevance_score": 12.34,
      "content": "..."
    }
  ]
}
```

错误响应：

```json
{
  "ok": false,
  "error": "question.doc_ids 不能为空"
}
```

## 前端设计

页面采用工作型调试界面，不做营销式首页。

布局：

- 顶部工具栏：上传 JSON、搜索、重置参数。
- 左栏：题目输入区、题目列表和过滤框。
- 中栏：BM25 参数表单。
- 右栏：检索结果。

结果展示：

- 统计条：`query_count`、`chunk_count`、`candidate_count`、`retrieved_windows`、`doc_coverage`、`max_bm25_score`、`avg_bm25_score`。
- chunk 列表：每个命中片段显示 `doc_id`、位置、分数、query 类型和正文。
- 文档级统计：显示 `retrieval_doc_stats`。
- 错误状态：JSON 解析失败、缺少必要字段、文档未找到或后端异常时，在页面固定错误区显示。

## 测试设计

新增后端测试，优先测试纯函数，避免启动真实浏览器：

- 单题 JSON 可以被 `/api/search` 请求体接受并返回成功结构。
- 题目数组中的选题逻辑由前端处理，后端只接受单题；前端单题归一化逻辑放在独立 JS 函数中，后续可人工验证。
- 参数覆盖会传入 `BM25Retriever` 并影响 `stats` 中的 chunk 数或窗口数量。
- 缺少 `doc_ids`、缺少 `domain`、JSON 非对象时返回 `ok=false` 和清晰错误信息。
- 文档不存在时不崩溃，返回带 fallback 文本或错误提示，保持页面可诊断。

## 运行方式

默认端口为 `8765`，支持参数覆盖：

```bash
python scripts/bm25_debug_server.py --host 127.0.0.1 --port 8765
```

服务启动后在终端打印访问地址。

## 风险与约束

- 每次搜索仍按当前实现现场读取全文、切 chunk、计算 IDF，长文档或多文档题目会有延迟。
- 页面不调用 LLM，只能评估 BM25 召回质量，不能直接判断最终答案是否正确。
- 调试页使用单值参数覆盖嵌套配置，和正式运行的按领域/按题型配置存在差异；页面会显示当前覆盖值，避免误解。
- 不新增依赖，因此前端状态管理保持简单，复杂交互不纳入首版。

## 验收标准

- 可以运行本地服务并打开页面。
- 可以上传赛题数组 JSON，选择某一道题。
- 可以粘贴单题 JSON 并直接搜索。
- 修改参数后再次搜索，结果统计随参数变化。
- 页面展示命中 chunk 的正文、分数、位置、query 类型和总体统计。
- 后端测试通过，现有测试不回归。
