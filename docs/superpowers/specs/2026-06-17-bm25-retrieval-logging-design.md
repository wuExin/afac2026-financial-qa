# BM25 检索结果日志与可视化设计

> 日期：2026-06-17
> 状态：待审阅
> 范围：在现有 BM25Retriever 上加 per-qid 检索日志 + 静态 HTML 报告

## 1. 目标

帮助定位"BM25 检索是不是问题"。两类产出：

1. **per-qid JSON 日志**：每题一份完整检索上下文（题目 / queries / stats / chunks 含文本），落到 `logs/<qid>.json`
2. **静态 HTML 报告**：双栏布局浏览所有题目，chunks 按分数着色，单文件、无依赖、双击打开

非目标：

- 不替代 `output/diagnostics_a.csv`（后者仍按现有格式生成，用于提交 CSV 配套诊断）
- 不记录 LLM 最终 prompt（如需要后续再加，当前只覆盖检索阶段）
- 不实时观察（agent 跑完再生成报告）

## 2. 架构

```
src/agent/
  retrieval_logger.py            [新增] ~50 行。RetrievalLogger 类，写 logs/<qid>.json
  agent.py                       [改] BM25Retriever.__init__ 实例化 logger；retrieve() 末尾调 dump
scripts/
  generate_report.py             [新增] 读 logs/*.json，合成 output/retrieval_report.html
config/config.yaml               [改] 加 logging 段
logs/                            [运行时生成] <qid>.json，每次跑覆盖
output/
  retrieval_report.html          [运行时生成] 单文件，CSS/JS 全内联
```

## 3. RetrievalLogger

### 接口

```python
class RetrievalLogger:
    def __init__(self, log_dir: str = "logs", enabled: bool = True):
        self.log_dir = Path(log_dir)
        self.enabled = enabled

    def dump(self, qid: str, question: dict, queries: list[str],
             chunks: list[dict], stats: dict) -> None:
        """写 logs/<qid>.json，失败静默"""
```

### 调用点

`agent.py` 中 `BM25Retriever.retrieve()`（约 306 行起），在 stats 组装完成后、`return retrieved, stats` 之前：

```python
if self.log_retrieval:
    self.retrieval_logger.dump(
        qid=question.get("qid", ""),
        question=question,
        queries=queries,
        chunks=limited,   # 已含 content 字段，零拷贝
        stats=stats,
    )
return retrieved, stats
```

`_empty_stats` 早退路径（chunks 或 queries 为空时）**也要 dump**，便于排查"为什么 BM25 没命中"。早退分支传入 `chunks=[]`、`queries=实际 queries`、`stats=_empty_stats(...)`。

### `logs/<qid>.json` schema

```json
{
  "qid": "reg_a_001",
  "timestamp": "2026-06-17T23:20:00+08:00",
  "domain": "regulatory",
  "question": "...题目正文...",
  "options": ["A. ...", "B. ...", "C. ...", "D. ..."],
  "answer_format": "multi",
  "doc_ids": ["strict_v3_008_...", "strict_v3_009_..."],
  "queries": ["受益所有人", "客户尽职调查", "..."],
  "stats": {
    "retrieval_method": "bm25_window",
    "query_count": 7,
    "chunk_count": 25,
    "candidate_count": 25,
    "retrieved_windows": 4,
    "retrieved_chars": 10394,
    "doc_coverage": 2,
    "max_bm25_score": 808.07,
    "avg_bm25_score": 548.37,
    "selected_sources": [...],
    "retrieval_doc_stats": {...}
  },
  "chunks": [
    {
      "doc_id": "strict_v3_008_...",
      "start": 0,
      "end": 2517,
      "score": 808.07,
      "query_types": ["domain_terms", "option_A", "option_B"],
      "text": "...chunk 实际内容（即原 limited[i]['content']）..."
    }
  ]
}
```

### 关键约定

- **覆盖写**：每次运行覆盖同名文件。理由：调试场景下用户只关心最近一次的检索结果，旧 log 进版本控制意义不大。
- **失败静默**：所有 IO/序列化异常 try/except 吞掉，stderr 打印一行告警，绝不影响主流程。
- **空 chunks 也写**：检索空也落盘，便于排查"为什么没召回"。
- **question 字段白名单**：dump 时只保留 `qid / domain / split / question / options / answer_format / type / doc_ids`。A 组题目本就不含 `answer` 字段，但用白名单可以防止未来 B 组或 schema 变化时把答案混进 log，误导检索质量评估。
- **retrieved Evidence 不进 log**：retrieved 是 chunks 拼接后的产物，stats + chunks 已是上游原始信息，重复落盘没价值。

## 4. 配置

`config/config.yaml` 末尾追加：

```yaml
logging:
  log_retrieval: true              # 是否启用 BM25 检索日志
  retrieval_log_dir: "logs"        # 日志目录
```

`BM25Retriever.__init__` 读 `config["logging"]` 段：

```python
log_cfg = (config or {}).get("logging") or {}
self.log_retrieval = bool(log_cfg.get("log_retrieval", True))
self.retrieval_logger = RetrievalLogger(
    log_dir=log_cfg.get("retrieval_log_dir", "logs"),
    enabled=self.log_retrieval,
)
```

注意：`BM25Retriever.__init__` 现有签名是 `__init__(self, config: Optional[Dict] = None)`，从 `cfg = config or {}` 读 bm25 参数。新增 logging 段从同一 config 顶层读取。

## 5. 静态 HTML 报告

### 命令

```bash
python scripts/generate_report.py
# 或带参数
python scripts/generate_report.py --log-dir logs --output output/retrieval_report.html
```

注：`scripts/` 目录当前不是 Python package（无 `__init__.py`），用路径式调用即可；后续如需 `python -m scripts.xxx` 调用再加 `__init__.py`。

### 流程

1. 扫 `--log-dir` 下所有 `*.json`，按 qid 字典序排序
2. 每个文件单独解析；解析失败的记入 `failed` 列表
3. 提取每题的列表元数据：`qid`, `domain`, `max_bm25_score`, `retrieved_windows`, `chunks_count`, `answer_format`
4. 将完整 JSON 数组嵌入 HTML 模板的 `<script type="application/json" id="data">` 标签
5. 渲染双栏 HTML，写 `--output`

### 网页布局（双栏）

整体高度撑满视口，左栏固定宽，右栏滚动。

**左栏（240px）**：

- 顶部搜索框：实时按 qid 关键字过滤
- qid 列表按 `domain` 分组（financial_contracts / financial_reports / insurance / regulatory / research）
- 每行：qid + 分数小色块 + chunks 数（如 `reg_a_002 ■ 4`）
- 当前选中行高亮蓝底白字

**右栏（flex:1）**：

- **顶部固定区**（紧凑，不滚动）：
  - 第一行：qid（大号）+ `· domain · answer_format` + 右侧关键 stats（queries / chunks / windows / max score）
  - 第二行：题目正文（可折叠展开全文）
  - 第三行：queries chips（按 query_type 着色：domain_terms 蓝、option_X 橙、其余灰）
  - 第四行：options（A/B/C/D 紧凑显示）
- **chunks 卡片列表**（按 score 降序）：
  - 每张卡左侧 4px 色条：
    - 绿色 `#4caf50`：score ≥ 70% × max_score
    - 黄色 `#ffa726`：40% ≤ score < 70%
    - 红色 `#f44336`：score < 40%
  - 卡内顶部行：`chunk N` + `★ score`
  - 卡内元数据：`doc_id`（截断 + 全文 tooltip）+ `start-end` + query_types chips
  - 卡内文本：`<pre>` 格式，完整不截断，等宽字体便于扫读

### 静态资源策略

- 单 HTML 文件，CSS 全部内联到 `<style>`，JS 全部内联到 `<script>`
- JSON 数据嵌入 `<script type="application/json" id="data">`
- 字体使用系统默认（`-apple-system, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif`），保证中文显示
- 无任何 CDN/外链，断网可看

### 错误处理

- `--log-dir` 不存在 → 报错退出，提示"先跑 `python -m src.agent.run --split A`"
- 目录存在但为空 → 生成报告，页面居中显示"未找到任何 log"
- 单个 JSON 解析失败 → 跳过该题，HTML 顶部红色告警条列出失败的 qid

## 6. 文件影响清单

| 文件 | 改动类型 | 行数估计 |
|---|---|---|
| `src/agent/retrieval_logger.py` | 新增 | ~50 |
| `src/agent/agent.py` | 改 `BM25Retriever.__init__`（+5 行）和 `retrieve()`（+8 行，含早退路径） | ~13 |
| `scripts/generate_report.py` | 新增 | ~150（含内联 HTML 模板） |
| `config/config.yaml` | 加 logging 段 | +4 |

`run.py` 不需要改——报告生成是独立命令，不嵌入 run 流程。

## 7. 测试

### 单元 / 集成（手动）

1. `python -m src.agent.run --split A --limit 3`
   - `logs/` 下出现 3 个 JSON，文件名 = qid
   - 抽查 `reg_a_001`（已知 retrieved_windows=4）：`chunks` 数组长度 = 4，每个 `text` 非空，`stats` 字段齐全，`queries` 是字符串列表
   - 抽查 retrieval 空的题（如有）：`chunks=[]`、`stats.retrieved_windows=0`，文件仍然落盘

2. `python -m scripts.generate_report`
   - `output/retrieval_report.html` 生成，大小 > 0
   - 浏览器打开：左栏显示 3 个 qid 按 domain 分组，色块颜色合理
   - 点选切换右栏：顶部 stats 区更新，chunks 卡片重新渲染
   - 搜索框输入 qid 片段：左栏实时过滤

3. 故意把 `retrieval_log_dir` 改成不可写路径（如 Windows 上 `Z:/nonexistent`）
   - agent 主流程跑完不报错（stderr 看到 RetrievalLogger 失败告警）
   - `output/submission_a.csv` 正常生成

4. 故意删一个 log JSON 再生成报告
   - 报告顶部红色告警条列出失败 qid
   - 其余 qid 正常显示

### 回归

- 跑 `--limit 3` 不开 logging（`log_retrieval: false`）：`logs/` 不写新文件，其他一切正常
- 跑全量 A 组再生成报告：100 题 HTML 单文件大小可接受（预计 < 5 MB，含全部 chunk 文本）

## 8. 已决定 / 默认值一览

| 决策点 | 决定 | 理由 |
|---|---|---|
| log 内容粒度 | chunk + 完整检索上下文（question + queries + stats + chunks） | 一次看全检索链路 |
| log 文件组织 | 每题一个 JSON | 便于按 qid 直接定位 |
| log 目录 | `logs/`（项目根） | 与 output/ 平行，一眼可见 |
| 写入策略 | 每次覆盖 | 调试只关心最近一次 |
| `log_retrieval` 默认值 | true | 不影响主流程，开着备用 |
| 题目标准答案 | **不进 log** | log 聚焦检索，不混入"答对没" |
| 报告技术栈 | 纯静态 HTML（嵌入 JSON） | 无依赖、可分享、双击打开 |
| 网页布局 | 双栏（左列表 + 右详情） | 切换最快 |
| chunks 排版 | 卡片列表按 score 降序 | 直接服务"检索质量好不好" |
| 色条阈值 | 同题 max 的 70% / 40% | 同题内可比、跨题不可比 |
| 报告生成时机 | 独立命令 `scripts.generate_report` | 不耦合 run.py，跑完按需生成 |
