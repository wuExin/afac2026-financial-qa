# BM25 检索日志与可视化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 BM25Retriever 上加 per-qid 检索日志（含 chunk 文本）+ 静态 HTML 报告，帮助定位"检索是不是问题"。

**Architecture:** 新增 `RetrievalLogger` 类负责把每题检索上下文落盘到 `logs/<qid>.json`；BM25Retriever 在 `retrieve()` 末尾和早退分支两处调用 dump。新增 `scripts/generate_report.py` 读 logs 合成单文件 HTML（数据嵌入 `<script type="application/json">`，CSS/JS 全内联，双击打开）。

**Tech Stack:** Python 3.10+ / pyyaml（已有）/ pytest（新增） / 原生 HTML+CSS+JS（无框架）

**Spec:** `docs/superpowers/specs/2026-06-17-bm25-retrieval-logging-design.md`

---

## 文件结构

| 文件 | 操作 | 责任 |
|---|---|---|
| `requirements.txt` | 改 | 加 pytest 到 dev 段 |
| `pytest.ini` | 新增 | 配置 pythonpath 让 `from src.xxx` 在 tests 里可用 |
| `tests/__init__.py` | 新增 | 空，标记 tests 为 package |
| `tests/test_retrieval_logger.py` | 新增 | RetrievalLogger 单元测试 |
| `src/agent/retrieval_logger.py` | 新增 | RetrievalLogger 类，~80 行 |
| `src/agent/agent.py` | 改 | BM25Retriever.__init__ 实例化 + retrieve() 两处 dump |
| `config/config.yaml` | 改 | 末尾加 logging 段 |
| `scripts/generate_report.py` | 新增 | 读 logs 合成 HTML，~250 行 |

---

## Task 1: 建立测试基础设施

**Files:**
- Modify: `requirements.txt`
- Create: `pytest.ini`
- Create: `tests/__init__.py`
- Create: `tests/test_smoke.py`

- [ ] **Step 1: 加 pytest 到 requirements.txt**

打开 `requirements.txt`，在末尾追加（不修改现有内容）：

```
# 开发依赖（测试用，baseline 运行不需要）
pytest>=7.4.0
```

- [ ] **Step 2: 安装 pytest**

Run: `pip install pytest>=7.4.0`
Expected: 安装成功，`pytest --version` 输出版本号

- [ ] **Step 3: 创建 pytest.ini**

写入 `pytest.ini`：

```ini
[pytest]
pythonpath = .
testpaths = tests
python_files = test_*.py
```

- [ ] **Step 4: 创建 tests/ package**

Run: `mkdir -p tests && touch tests/__init__.py`
（或用 Write 工具创建空的 `tests/__init__.py`）

- [ ] **Step 5: 写 smoke 测试验证基础设施**

写入 `tests/test_smoke.py`：

```python
def test_pytest_runs():
    assert 1 + 1 == 2
```

- [ ] **Step 6: 跑 smoke 测试**

Run: `pytest tests/test_smoke.py -v`
Expected: `1 passed`

- [ ] **Step 7: Commit**

```bash
git add requirements.txt pytest.ini tests/__init__.py tests/test_smoke.py
git commit -m "test: bootstrap pytest infrastructure"
```

---

## Task 2: 实现 RetrievalLogger（TDD）

**Files:**
- Create: `tests/test_retrieval_logger.py`
- Create: `src/agent/retrieval_logger.py`

### Step 1-5: 写失败测试

- [ ] **Step 1: 写 test_retrieval_logger.py**

写入 `tests/test_retrieval_logger.py`：

```python
"""RetrievalLogger 单元测试。"""
import json
from pathlib import Path

import pytest

from src.agent.retrieval_logger import RetrievalLogger


@pytest.fixture
def basic_question():
    return {
        "qid": "reg_a_001",
        "domain": "regulatory",
        "split": "A",
        "question": "下列哪些属于应当识别的受益所有人？",
        "options": ["A. 公司高管", "B. 实际控制人", "C. 持股 25% 自然人", "D. 员工"],
        "answer_format": "multi",
        "type": "multi_choice",
        "doc_ids": ["strict_v3_008_xxx", "strict_v3_017_yyy"],
        # 故意带 answer，验证白名单过滤
        "answer": "BC",
    }


@pytest.fixture
def basic_chunks():
    return [
        {
            "doc_id": "strict_v3_008_xxx",
            "start": 0,
            "end": 2517,
            "score": 808.0685,
            "query_types": ["domain_terms", "option_A"],
            "content": "金融机构应当识别客户的受益所有人...",
        },
    ]


@pytest.fixture
def basic_stats():
    return {
        "retrieval_method": "bm25_window",
        "query_count": 7,
        "retrieved_windows": 1,
        "max_bm25_score": 808.07,
    }


def test_dump_writes_json_file(tmp_path, basic_question, basic_chunks, basic_stats):
    logger = RetrievalLogger(log_dir=str(tmp_path), enabled=True)
    logger.dump(
        qid="reg_a_001",
        question=basic_question,
        queries=["受益所有人", "客户尽职调查"],
        chunks=basic_chunks,
        stats=basic_stats,
    )
    out_file = tmp_path / "reg_a_001.json"
    assert out_file.exists()
    payload = json.loads(out_file.read_text(encoding="utf-8"))
    assert payload["qid"] == "reg_a_001"
    assert payload["question_text"] == "下列哪些属于应当识别的受益所有人？"
    assert payload["queries"] == ["受益所有人", "客户尽职调查"]
    assert payload["stats"]["retrieval_method"] == "bm25_window"
    assert len(payload["chunks"]) == 1
    assert payload["chunks"][0]["text"] == "金融机构应当识别客户的受益所有人..."
    assert payload["chunks"][0]["score"] == 808.0685  # 原始精度保留


def test_dump_respects_disabled(tmp_path, basic_question, basic_chunks, basic_stats):
    logger = RetrievalLogger(log_dir=str(tmp_path), enabled=False)
    logger.dump(
        qid="reg_a_001",
        question=basic_question,
        queries=["x"],
        chunks=basic_chunks,
        stats=basic_stats,
    )
    assert not (tmp_path / "reg_a_001.json").exists()


def test_dump_filters_answer_field(tmp_path, basic_question, basic_chunks, basic_stats):
    """question 字段白名单：answer 不能进 log。"""
    logger = RetrievalLogger(log_dir=str(tmp_path), enabled=True)
    logger.dump(
        qid="reg_a_001",
        question=basic_question,
        queries=[],
        chunks=[],
        stats=basic_stats,
    )
    payload = json.loads((tmp_path / "reg_a_001.json").read_text(encoding="utf-8"))
    assert "answer" not in payload["question_meta"]
    assert "answer" not in payload
    # 但 doc_ids 等白名单字段要保留
    assert payload["question_meta"]["doc_ids"] == ["strict_v3_008_xxx", "strict_v3_017_yyy"]


def test_dump_silenced_on_io_error(tmp_path, basic_question, basic_chunks, basic_stats, capsys):
    """IO 失败时打印 stderr 但不抛出。"""
    # 把 log_dir 指向一个已存在的文件（不是目录），触发 mkdir 失败
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    logger = RetrievalLogger(log_dir=str(blocker), enabled=True)
    # 不应抛出
    logger.dump(
        qid="reg_a_001",
        question=basic_question,
        queries=[],
        chunks=[],
        stats=basic_stats,
    )
    captured = capsys.readouterr()
    assert "RetrievalLogger" in captured.err or "RetrievalLogger" in captured.out


def test_dump_handles_empty_qid(tmp_path, basic_question, basic_stats):
    """qid 为空时跳过，不写文件。"""
    logger = RetrievalLogger(log_dir=str(tmp_path), enabled=True)
    logger.dump(
        qid="",
        question=basic_question,
        queries=[],
        chunks=[],
        stats=basic_stats,
    )
    assert list(tmp_path.glob("*.json")) == []


def test_dump_handles_empty_chunks(tmp_path, basic_question, basic_stats):
    """空 chunks 仍然落盘，便于排查为什么没召回。"""
    logger = RetrievalLogger(log_dir=str(tmp_path), enabled=True)
    logger.dump(
        qid="reg_a_002",
        question=basic_question,
        queries=["受益所有人"],
        chunks=[],
        stats=basic_stats,
    )
    payload = json.loads((tmp_path / "reg_a_002.json").read_text(encoding="utf-8"))
    assert payload["chunks"] == []
    assert payload["queries"] == ["受益所有人"]


def test_dump_overwrites_existing_file(tmp_path, basic_question, basic_chunks, basic_stats):
    """同名文件覆盖写。"""
    logger = RetrievalLogger(log_dir=str(tmp_path), enabled=True)
    logger.dump(
        qid="reg_a_001",
        question=basic_question,
        queries=["old"],
        chunks=basic_chunks,
        stats=basic_stats,
    )
    logger.dump(
        qid="reg_a_001",
        question=basic_question,
        queries=["new"],
        chunks=basic_chunks,
        stats=basic_stats,
    )
    payload = json.loads((tmp_path / "reg_a_001.json").read_text(encoding="utf-8"))
    assert payload["queries"] == ["new"]
```

- [ ] **Step 2: 跑测试确认全部失败**

Run: `pytest tests/test_retrieval_logger.py -v`
Expected: `ModuleNotFoundError: No module named 'src.agent.retrieval_logger'`

### Step 3-5: 实现 RetrievalLogger

- [ ] **Step 3: 写 src/agent/retrieval_logger.py**

写入 `src/agent/retrieval_logger.py`：

```python
"""BM25 检索结果落盘：把每题的检索上下文（含 chunk 文本）写到 logs/<qid>.json。

用于人工核对检索质量，定位"BM25 搜出来的对不对"。
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List


# question 字段白名单：只 dump 这些字段，过滤潜在的 answer（避免误导检索质量评估）
QUESTION_FIELDS_WHITELIST = (
    "qid",
    "domain",
    "split",
    "question",
    "options",
    "answer_format",
    "type",
    "doc_ids",
)


class RetrievalLogger:
    """把 BM25 检索结果（含 chunk 文本）落盘到 logs/<qid>.json。

    设计原则：
    - 单一职责：只负责把检索结果写到磁盘，不参与检索逻辑
    - 失败静默：任何 IO/序列化错误都不影响主流程
    - 覆盖写：每次运行覆盖旧文件，确保看到的是最近一次结果
    """

    def __init__(self, log_dir: str = "logs", enabled: bool = True):
        self.log_dir = Path(log_dir)
        self.enabled = enabled

    def dump(
        self,
        qid: str,
        question: Dict,
        queries: List[str],
        chunks: List[Dict],
        stats: Dict,
    ) -> None:
        """写 logs/<qid>.json。失败时打印 stderr 但不抛出。"""
        if not self.enabled:
            return
        if not qid:
            print("[RetrievalLogger] 跳过：qid 为空", file=sys.stderr)
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            payload = self._build_payload(qid, question, queries, chunks, stats)
            out_path = self.log_dir / f"{qid}.json"
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[RetrievalLogger] 写 {qid} 失败: {e}", file=sys.stderr)

    def _build_payload(
        self,
        qid: str,
        question: Dict,
        queries: List[str],
        chunks: List[Dict],
        stats: Dict,
    ) -> Dict:
        return {
            "qid": qid,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "domain": question.get("domain", ""),
            "question_meta": {
                k: question[k]
                for k in QUESTION_FIELDS_WHITELIST
                if k in question
            },
            "question_text": question.get("question", ""),
            "options": question.get("options", []),
            "answer_format": question.get("answer_format", ""),
            "doc_ids": question.get("doc_ids", []),
            "queries": list(queries),
            "stats": stats,
            "chunks": [self._serialize_chunk(c) for c in chunks],
        }

    @staticmethod
    def _serialize_chunk(chunk: Dict) -> Dict:
        return {
            "doc_id": chunk.get("doc_id", ""),
            "start": chunk.get("start", 0),
            "end": chunk.get("end", 0),
            "score": round(float(chunk.get("score", 0.0)), 4),
            "query_types": sorted(chunk.get("query_types", []) or []),
            "text": chunk.get("content", ""),
        }
```

- [ ] **Step 4: 跑测试确认全部通过**

Run: `pytest tests/test_retrieval_logger.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/agent/retrieval_logger.py tests/test_retrieval_logger.py
git commit -m "feat(agent): add RetrievalLogger for BM25 chunk logging"
```

---

## Task 3: BM25Retriever 接入 logger

**Files:**
- Modify: `src/agent/agent.py`（顶部 import；BM25Retriever.__init__ 289-304 行；retrieve() 306-389 行含早退 312-313）
- Modify: `config/config.yaml`（末尾追加 logging 段）

- [ ] **Step 1: 改 agent.py 顶部 import**

在 `src/agent/agent.py` 现有 import 段（约 14-20 行附近，`from src.utils.llm_client import LLMClient` 那块）追加一行：

```python
from src.agent.retrieval_logger import RetrievalLogger
```

- [ ] **Step 2: 改 BM25Retriever.__init__ 实例化 logger**

在 `src/agent/agent.py` 中找到 `BM25Retriever.__init__`（约 289 行），在末尾（`self.max_query_terms = ...` 那行之后）追加：

```python
        # 检索日志（默认开；失败静默不影响主流程）
        log_cfg = (cfg.get("logging") or {})
        self.log_retrieval = bool(log_cfg.get("log_retrieval", True))
        self.retrieval_logger = RetrievalLogger(
            log_dir=log_cfg.get("retrieval_log_dir", "logs"),
            enabled=self.log_retrieval,
        )
```

注意：`cfg` 是 `BM25Retriever.__init__` 里的局部变量 `cfg = config or {}`，但当前 `cfg` 只读 `retrieval` 段（参见现有 291-304 行的 `cfg.get("bm25_k1", ...)` 等）。logging 段在 config 顶层，所以这里要从 `cfg.get("logging")` 读，cfg 就是整个 config dict（不是 config["retrieval"]）。

**验证 cfg 来源**：打开 `src/agent/agent.py:289-290`，确认 `__init__(self, config)` 里第一行是 `cfg = config or {}`。如果是 `cfg = config.get("retrieval", {})`，需要改为读顶层 config：

```python
def __init__(self, config: Optional[Dict] = None):
    cfg = config or {}        # 整个 config dict
    retrieval_cfg = cfg.get("retrieval", {})   # 检索专用参数
    # 后续 bm25_k1 等都从 retrieval_cfg 读
```

**实施前先 grep 确认**：

Run: `grep -n "cfg = " src/agent/agent.py | head -5`

如果 `cfg = config or {}`（即 cfg 是顶层 config）→ 直接用 `cfg.get("logging")`
如果 `cfg = config.get("retrieval", {})` → 改成两段：`cfg = config or {}` + `retrieval_cfg = cfg.get("retrieval", {})`，然后把所有 bm25_xxx 读取从 cfg 改为 retrieval_cfg

**当前 agent.py:289-290 实际代码**（已在 brainstorming 阶段确认）：

```python
def __init__(self, config: Optional[Dict] = None):
    cfg = config or {}
```

→ cfg 就是顶层 config。直接用 `cfg.get("logging")` 即可。

- [ ] **Step 3: 改 retrieve() 早退路径加 dump**

在 `src/agent/agent.py` 中找到 `BM25Retriever.retrieve`（约 306 行），找到现有的早退逻辑（约 312-313 行）：

```python
        if not chunks or not queries:
            return evidence, self._empty_stats("bm25", len(chunks), len(queries))
```

替换为：

```python
        if not chunks or not queries:
            empty_stats = self._empty_stats("bm25", len(chunks), len(queries))
            if self.log_retrieval:
                self.retrieval_logger.dump(
                    qid=question.get("qid", ""),
                    question=question,
                    queries=queries,
                    chunks=[],
                    stats=empty_stats,
                )
            return evidence, empty_stats
```

- [ ] **Step 4: 改 retrieve() 正常路径加 dump**

在 `src/agent/agent.py` 找到 `retrieve` 方法末尾的 `return retrieved, stats`（约 389 行），在它之前插入：

```python
        if self.log_retrieval:
            self.retrieval_logger.dump(
                qid=question.get("qid", ""),
                question=question,
                queries=queries,
                chunks=limited,
                stats=stats,
            )

        return retrieved, stats
```

注意：`limited` 是 `_limit_total_chars` 的返回值（约 319 行赋值），是 list of dict，每个含 `content / doc_id / start / end / score / query_types`。RetrievalLogger._serialize_chunk 已经处理 content→text 字段映射。

- [ ] **Step 5: 改 config.yaml 加 logging 段**

打开 `config/config.yaml`，在末尾（约 102 行之后）追加：

```yaml

# 检索日志（每次跑覆盖；落盘失败不影响主流程）
logging:
  log_retrieval: true
  retrieval_log_dir: "logs"
```

- [ ] **Step 6: 跑全量单元测试确认没回归**

Run: `pytest tests/ -v`
Expected: 所有测试通过（retrieval_logger 7 个 + smoke 1 个 = 8 passed）

- [ ] **Step 7: 集成测试 — 跑 --limit 3 真实跑通**

确保 `.env` 已配置 API key。Run:

```bash
python -m src.agent.run --split A --limit 3
```

Expected:
- 程序正常退出，输出 `成功率: 3/3`
- `logs/` 目录下出现 3 个 JSON 文件（`fc_a_001.json`, `fc_a_002.json`, `fc_a_003.json`）
- `output/submission_a.csv` 正常生成

- [ ] **Step 8: 抽查一个 log 文件结构**

Run:

```bash
python -c "
import json
data = json.load(open('logs/fc_a_001.json', encoding='utf-8'))
print('qid:', data['qid'])
print('chunks count:', len(data['chunks']))
print('first chunk text preview:', data['chunks'][0]['text'][:80] if data['chunks'] else '(empty)')
print('stats retrieval_method:', data['stats']['retrieval_method'])
print('has answer field:', 'answer' in data or 'answer' in data.get('question_meta', {}))
"
```

Expected:
- qid 是 fc_a_001
- chunks count > 0（baseline 跑通的话应该有检索结果）
- first chunk text preview 非空
- stats retrieval_method 是 bm25_window
- has answer field: False（白名单过滤生效）

- [ ] **Step 9: 错误路径验证 — log_dir 不可写时静默**

临时把 `config/config.yaml` 里 `retrieval_log_dir` 改成 `"Z:/nonexistent_path"`（Windows 上不存在的盘符），再跑：

Run: `python -m src.agent.run --split A --limit 1`
Expected: 程序仍然退出码 0，`成功率: 1/1`，stderr 看到 `[RetrievalLogger] 写 fc_a_001 失败: ...`

验证后**改回** `retrieval_log_dir: "logs"`。

- [ ] **Step 10: Commit**

```bash
git add src/agent/agent.py config/config.yaml
git commit -m "feat(agent): wire BM25Retriever to dump retrieval logs"
```

---

## Task 4: 实现 generate_report.py

**Files:**
- Create: `scripts/generate_report.py`

- [ ] **Step 1: 写 scripts/generate_report.py**

写入 `scripts/generate_report.py`：

```python
"""读 logs/*.json，合成单文件静态 HTML 报告，浏览 BM25 检索结果。

用法：
    python scripts/generate_report.py
    python scripts/generate_report.py --log-dir logs --output output/retrieval_report.html
"""
import argparse
import json
import sys
from collections import defaultdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOG_DIR = ROOT / "logs"
DEFAULT_OUTPUT = ROOT / "output" / "retrieval_report.html"


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>BM25 检索报告</title>
<style>
:root {
  --green: #4caf50;
  --yellow: #ffa726;
  --red: #f44336;
  --blue: #3578e5;
  --blue-bg: #e3f2fd;
  --orange-bg: #fff3e0;
  --border: #e0e0e0;
  --bg: #f7f7f8;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto,
               "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 13px;
  color: #222;
}
#app { display: flex; height: 100vh; }
.sidebar {
  width: 260px;
  background: var(--bg);
  border-right: 1px solid var(--border);
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.sidebar-search {
  padding: 8px;
  border-bottom: 1px solid var(--border);
}
.sidebar-search input {
  width: 100%;
  padding: 6px 8px;
  font-size: 12px;
  border: 1px solid var(--border);
  border-radius: 4px;
}
.sidebar-list {
  flex: 1;
  overflow-y: auto;
}
.domain-group {
  padding: 4px 0;
}
.domain-label {
  padding: 6px 12px 2px;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #888;
}
.qid-row {
  padding: 5px 12px;
  cursor: pointer;
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  border-left: 3px solid transparent;
}
.qid-row:hover { background: #ececec; }
.qid-row.active {
  background: var(--blue);
  color: white;
}
.qid-row .score-chip {
  font-size: 10px;
  padding: 1px 5px;
  border-radius: 8px;
  background: rgba(0,0,0,0.08);
}
.qid-row.active .score-chip { background: rgba(255,255,255,0.2); }
.detail {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
.detail-header {
  padding: 10px 14px;
  border-bottom: 1px solid var(--border);
  background: #fafafa;
}
.detail-header h2 {
  margin: 0 0 4px;
  font-size: 16px;
}
.detail-header .meta-line {
  font-size: 11px;
  color: #666;
  margin-bottom: 6px;
}
.detail-header .question-text {
  font-size: 12px;
  margin: 6px 0;
  line-height: 1.5;
}
.query-chips, .option-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 4px;
}
.chip {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: 8px;
  background: #eee;
}
.chip.domain { background: var(--blue-bg); }
.chip.option { background: var(--orange-bg); }
.chip.failed { background: #fee; color: var(--red); }
.chunks-area {
  flex: 1;
  overflow-y: auto;
  padding: 10px 14px;
}
.chunks-label {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
  color: #888;
  margin-bottom: 8px;
}
.chunk-card {
  background: white;
  padding: 8px 10px;
  border-radius: 4px;
  margin-bottom: 8px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.05);
  border-left: 4px solid var(--border);
}
.chunk-card.score-high { border-left-color: var(--green); }
.chunk-card.score-mid { border-left-color: var(--yellow); }
.chunk-card.score-low { border-left-color: var(--red); }
.chunk-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  margin-bottom: 2px;
}
.chunk-head .score { font-weight: bold; }
.chunk-head .score.high { color: var(--green); }
.chunk-head .score.mid { color: var(--yellow); }
.chunk-head .score.low { color: var(--red); }
.chunk-meta {
  font-size: 10px;
  color: #888;
  margin-bottom: 4px;
}
.chunk-text {
  font-family: ui-monospace, "Cascadia Mono", Consolas, monospace;
  font-size: 11px;
  white-space: pre-wrap;
  word-break: break-word;
  background: #fafafa;
  padding: 6px;
  border-radius: 3px;
  margin: 4px 0 0;
  line-height: 1.5;
}
.empty {
  padding: 40px;
  text-align: center;
  color: #888;
}
.warn-bar {
  background: #fee;
  color: var(--red);
  padding: 6px 14px;
  font-size: 11px;
  border-bottom: 1px solid #fcc;
}
</style>
</head>
<body>
<div id="app"></div>
<script type="application/json" id="data">__DATA_PLACEHOLDER__</script>
<script>
const DATA = JSON.parse(document.getElementById('data').textContent);

function scoreClass(score, maxScore) {
  if (!maxScore) return 'low';
  const ratio = score / maxScore;
  if (ratio >= 0.7) return 'high';
  if (ratio >= 0.4) return 'mid';
  return 'low';
}
function scoreCardClass(score, maxScore) {
  const c = scoreClass(score, maxScore);
  return 'chunk-card score-' + c;
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[ch]);
}
function truncate(s, n) {
  if (!s) return '';
  return s.length > n ? s.slice(0, n) + '…' : s;
}

function renderSidebar(records, activeQid, filter) {
  const grouped = {};
  for (const r of records) {
    const d = r.domain || '(no domain)';
    if (!grouped[d]) grouped[d] = [];
    grouped[d].push(r);
  }
  const domains = Object.keys(grouped).sort();
  let html = '';
  for (const d of domains) {
    const items = grouped[d].filter(r =>
      !filter || (r.qid && r.qid.toLowerCase().includes(filter.toLowerCase()))
    );
    if (!items.length) continue;
    html += `<div class="domain-group">`;
    html += `<div class="domain-label">${escapeHtml(d)} (${items.length})</div>`;
    for (const r of items) {
      const maxScore = r.stats && r.stats.max_bm25_score || 0;
      const cls = scoreClass(maxScore, maxScore) || 'low';
      const colorMap = { high: 'var(--green)', mid: 'var(--yellow)', low: 'var(--red)' };
      const active = r.qid === activeQid ? ' active' : '';
      html += `<div class="qid-row${active}" onclick="selectQid('${escapeHtml(r.qid)}')">
        <span>${escapeHtml(r.qid)}</span>
        <span class="score-chip" style="color:${colorMap[cls]}">
          ■ ${maxScore.toFixed(1)} · ${(r.chunks || []).length}c
        </span>
      </div>`;
    }
    html += `</div>`;
  }
  return html;
}

function renderDetail(r) {
  if (!r) return '<div class="empty">选择左侧 qid 查看详情</div>';
  const stats = r.stats || {};
  const maxScore = stats.max_bm25_score || 0;
  const chunks = r.chunks || [];
  const queries = r.queries || [];
  const options = r.options || [];

  let html = `<div class="detail-header">
    <h2>${escapeHtml(r.qid)}</h2>
    <div class="meta-line">
      ${escapeHtml(r.domain || '')} · ${escapeHtml(r.answer_format || '')} ·
      queries: ${queries.length} · chunks: ${stats.chunk_count || 0} ·
      windows: ${stats.retrieved_windows || 0} ·
      <span style="color:var(--green)">max ${maxScore.toFixed(2)}</span> ·
      avg ${(stats.avg_bm25_score || 0).toFixed(2)}
    </div>
    <div class="question-text"><strong>题目：</strong>${escapeHtml(r.question_text || '')}</div>
    <div class="query-chips">
      ${queries.map(q => `<span class="chip">${escapeHtml(q)}</span>`).join('')}
    </div>
    ${options.length ? `<div class="option-chips">
      ${options.map(o => `<span class="chip option">${escapeHtml(o)}</span>`).join('')}
    </div>` : ''}
  </div>`;

  html += `<div class="chunks-area">
    <div class="chunks-label">chunks · 按 score 降序 (${chunks.length})</div>`;
  const sorted = [...chunks].sort((a, b) => (b.score || 0) - (a.score || 0));
  for (let i = 0; i < sorted.length; i++) {
    const c = sorted[i];
    const cls = scoreClass(c.score || 0, maxScore);
    html += `<div class="${scoreCardClass(c.score || 0, maxScore)}">
      <div class="chunk-head">
        <strong>chunk ${i + 1}</strong>
        <span class="score ${cls}">★ ${(c.score || 0).toFixed(2)}</span>
      </div>
      <div class="chunk-meta">
        ${escapeHtml(truncate(c.doc_id, 60))} · ${c.start || 0}-${c.end || 0}
        ${(c.query_types || []).map(q => `<span class="chip ${q.startsWith('option') ? 'option' : 'domain'}">${escapeHtml(q)}</span>`).join(' ')}
      </div>
      <pre class="chunk-text">${escapeHtml(c.text || '')}</pre>
    </div>`;
  }
  if (!sorted.length) {
    html += `<div class="empty">无 chunks（检索为空）</div>`;
  }
  html += `</div>`;
  return html;
}

function renderFailedBar(failed) {
  if (!failed || !failed.length) return '';
  const items = failed.map(f => `${escapeHtml(f.qid)}: ${escapeHtml(f.error)}`).join('; ');
  return `<div class="warn-bar">⚠ ${failed.length} 个 log 解析失败：${items}</div>`;
}

let activeQid = DATA.records[0] && DATA.records[0].qid;
let filter = '';

function render() {
  const app = document.getElementById('app');
  const active = DATA.records.find(r => r.qid === activeQid);
  const sidebarHtml = renderSidebar(DATA.records, activeQid, filter);
  const detailHtml = renderDetail(active);
  const failedBar = renderFailedBar(DATA.failed);
  app.innerHTML = `
    <div class="sidebar">
      <div class="sidebar-search">
        <input placeholder="搜 qid..." value="${escapeHtml(filter)}" oninput="setFilter(this.value)">
      </div>
      <div class="sidebar-list">${sidebarHtml}</div>
    </div>
    <div class="detail">
      ${failedBar}
      ${detailHtml}
    </div>
  `;
}

function selectQid(qid) {
  activeQid = qid;
  render();
}

function setFilter(value) {
  filter = value;
  render();
}

if (!DATA.records.length && !DATA.failed.length) {
  document.getElementById('app').innerHTML =
    '<div class="empty" style="flex:1;display:flex;align-items:center;justify-content:center">未找到任何 log</div>';
} else if (!DATA.records.length) {
  document.getElementById('app').innerHTML =
    `<div style="flex:1">${renderFailedBar(DATA.failed)}</div>`;
} else {
  render();
}
</script>
</body>
</html>
"""


def load_logs(log_dir: Path):
    """读 log_dir 下所有 *.json，返回 (records, failed)。"""
    records = []
    failed = []
    for path in sorted(log_dir.glob("*.json")):
        try:
            with path.open(encoding="utf-8") as f:
                records.append(json.load(f))
        except Exception as e:
            failed.append({"qid": path.stem, "error": str(e)})
    records.sort(key=lambda r: r.get("qid", ""))
    return records, failed


def render_html(records, failed):
    data = {"records": records, "failed": failed}
    data_json = json.dumps(data, ensure_ascii=False)
    return HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", escape(data_json))


def main():
    parser = argparse.ArgumentParser(description="生成 BM25 检索 HTML 报告")
    parser.add_argument("--log-dir", default=str(DEFAULT_LOG_DIR),
                        help=f"log 目录（默认 {DEFAULT_LOG_DIR}）")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT),
                        help=f"输出 HTML 路径（默认 {DEFAULT_OUTPUT}）")
    args = parser.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.exists():
        print(
            f"错误: log 目录 {log_dir} 不存在。"
            f"请先跑 python -m src.agent.run --split A",
            file=sys.stderr,
        )
        sys.exit(1)

    records, failed = load_logs(log_dir)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    html = render_html(records, failed)
    output.write_text(html, encoding="utf-8")

    print(f"报告已生成: {output}")
    print(f"  题数: {len(records)}，失败 log: {len(failed)}")
    if not records and not failed:
        print(f"  警告: {log_dir} 下没有 JSON 文件", file=sys.stderr)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 用 --help 验证脚本可执行**

Run: `python scripts/generate_report.py --help`
Expected: 输出 argparse 帮助文本（看到 `--log-dir` 和 `--output` 参数说明）

- [ ] **Step 3: 集成测试 — 用 Task 3 生成的 logs 生成报告**

前置：Task 3 已经跑过 `python -m src.agent.run --limit 3`，`logs/` 下应有 3 个 JSON。

Run: `python scripts/generate_report.py`
Expected:
- stdout: `报告已生成: F:\...\output\retrieval_report.html` + `题数: 3，失败 log: 0`
- `output/retrieval_report.html` 文件存在，大小 > 0

- [ ] **Step 4: 浏览器手动验证**

在文件管理器中双击 `output/retrieval_report.html`（或在终端 `start output/retrieval_report.html`）。预期看到：

- 左栏：3 个 qid，按 domain 分组（应该都是 `financial_contracts`，因为 fc_a_xxx 是前 3 题）
- 点选不同 qid，右栏切换
- 右栏顶部：qid + stats 行 + 题目 + queries chips
- chunks 区：按 score 降序的卡片，色条绿/黄/红

- [ ] **Step 5: 失败路径验证 — log_dir 不存在**

Run: `python scripts/generate_report.py --log-dir /tmp/nonexistent_xyz`
Expected: 退出码 1，stderr 输出 `错误: log 目录 ... 不存在`

- [ ] **Step 6: 失败路径验证 — 单个 log 损坏**

制造一个损坏的 log：

```bash
echo "{invalid json" > logs/_corrupted_test.json
python scripts/generate_report.py
```

Expected:
- 程序正常退出（退出码 0）
- stdout 显示 `失败 log: 1`
- HTML 页面顶部红色告警条列出 `_corrupted_test`

清理测试文件：

```bash
rm logs/_corrupted_test.json
```

- [ ] **Step 7: Commit**

```bash
git add scripts/generate_report.py
git commit -m "feat(scripts): add BM25 retrieval HTML report generator"
```

---

## Task 5: 全量验证与回归

**Files:** 无新增，仅验证

- [ ] **Step 1: 全量单元测试**

Run: `pytest tests/ -v`
Expected: 所有测试通过（retrieval_logger 7 + smoke 1 = 8 passed）

- [ ] **Step 2: 全量 A 组跑通**

确保 `.env` 已配置 API key，且 `logs/` 目录之前的内容可以覆盖。

Run: `python -m src.agent.run --split A`
Expected:
- `成功率: 100/100`
- `logs/` 下生成约 100 个 JSON 文件
- `output/submission_a.csv` 正常生成
- 总 token 消耗约 130 万（与历史一致）

- [ ] **Step 3: 全量报告生成**

Run: `python scripts/generate_report.py`
Expected:
- stdout: `题数: 100，失败 log: 0`
- `output/retrieval_report.html` 文件大小 < 10 MB（预计 3-5 MB）

- [ ] **Step 4: 浏览器抽查**

打开 `output/retrieval_report.html`，抽查：

- 左栏出现 5 个 domain 分组（financial_contracts / financial_reports / insurance / regulatory / research）
- 抽查 reg_a_001（已知 retrieved_windows=4，max_bm25_score≈808）：右栏应显示 4 张 chunk 卡片，绿色色条
- 搜索框输入 "reg"，左栏实时过滤只剩 regulatory 组的题
- 检查 chunks 卡片内文本完整（没有截断）

- [ ] **Step 5: 关闭 logging 验证（回归）**

临时把 `config/config.yaml` 里 `log_retrieval: true` 改成 `false`，删除 `logs/` 目录，跑：

Run: `rm -rf logs && python -m src.agent.run --split A --limit 2`
Expected:
- 程序正常退出
- `logs/` 目录**不**重新创建（说明 logging 真的关了）
- `output/submission_a.csv` 正常生成

验证后改回 `log_retrieval: true`。

- [ ] **Step 6: 最终 commit（如有改动）**

如果 Task 5 期间没有改动代码（仅验证），跳过此步。

否则：

```bash
git add <改动的文件>
git commit -m "test: full A-group regression for retrieval logging"
```

---

## 完成标准

- [ ] 所有 Task 1-5 的 step 都已勾选
- [ ] `pytest tests/ -v` 全绿
- [ ] `python -m src.agent.run --split A` 跑通 100/100，`logs/` 下有 100 个 JSON
- [ ] `output/retrieval_report.html` 在浏览器中正常显示双栏、可切换、chunks 着色正确
- [ ] 故意制造错误路径（log_dir 不可写、单个 JSON 损坏）时，主流程不挂
- [ ] 所有 commit 已推到本地（按需 push）

## Self-Review 检查清单

**1. Spec 覆盖：**

| Spec 章节 | 实现 Task |
|---|---|
| §3 RetrievalLogger 接口 / schema / 调用点 | Task 2 + Task 3 |
| §3 关键约定（覆盖写/失败静默/空 chunks 也写/白名单） | Task 2 测试覆盖全部 4 条 |
| §4 config.yaml logging 段 | Task 3 Step 5 |
| §5 HTML 报告（双栏/chunks 着色/搜索框/错误处理） | Task 4 |
| §6 文件影响清单 | 全部 Task |
| §7 测试场景（4 类） | Task 3 Step 7-9 + Task 4 Step 3-6 + Task 5 |

**2. Placeholder 扫描：** 已检查，无 TBD / "适当处理" / "类似 Task N" 等占位符。所有 step 都有具体代码或具体命令。

**3. 类型/命名一致性：**

- `RetrievalLogger.dump(qid, question, queries, chunks, stats)` — Task 2 定义，Task 3 调用，签名一致 ✓
- `log_retrieval` / `retrieval_log_dir` — config.yaml 字段名与 `__init__` 读取的字段名一致 ✓
- `limited` 变量 — Task 3 Step 4 引用，与现有 agent.py:319 一致 ✓
- `_serialize_chunk` 把 `content` 重命名为 `text` — Task 2 实现，Task 4 HTML 模板读 `c.text` 一致 ✓
- `stats.max_bm25_score` — 与现有 agent.py:375 输出一致 ✓
