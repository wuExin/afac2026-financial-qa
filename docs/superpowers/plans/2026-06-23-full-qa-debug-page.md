# Full QA Debug Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the existing local BM25 debug page into a question-driven full QA debug workbench that can run intent selection, BM25 retrieval, and final answer generation independently.

**Architecture:** Keep the current no-build static HTML page and Python `http.server` backend. Add focused service functions in `src/agent/bm25_debug_service.py`, expose them through two new HTTP endpoints, then update the page to load questions and render step results.

**Tech Stack:** Python standard library HTTP server, pytest, plain HTML/CSS/JavaScript, existing `FinancialQAAgent`, `IntentTermSelector`, and `BM25Retriever`.

## Global Constraints

- Do not add third-party frontend dependencies or a build step.
- Do not change the formal batch runner `src.agent.run`.
- Only display `mcq` and `multi` questions in the page.
- The three flow steps are `intent`, `bm25`, and `answer`; all default to enabled in the UI.
- Page-level retrieval parameter overrides must not write to `config/config.yaml`.
- Debug retrieval logging must stay disabled for page-triggered BM25 runs.
- Final answer generation may call the real LLM and must return a clear error if API configuration is missing.

---

## File Structure

- Modify `src/agent/bm25_debug_service.py`
  Owns validation, question loading, retrieval config overlays, intent selection, BM25 serialization, and full-flow orchestration.
- Modify `scripts/bm25_debug_server.py`
  Owns HTTP routing only: static page, `/api/search`, `/api/questions`, and `/api/run-question`.
- Modify `scripts/bm25_debug_ui.html`
  Owns all browser UI state and rendering.
- Modify `tests/test_bm25_debug_service.py`
  Covers new service behavior with monkeypatched agent and LLM calls.
- Modify `tests/test_bm25_debug_server.py`
  Covers new request parsing and route helper behavior.

---

### Task 1: Service Layer for Questions and Flow Execution

**Files:**
- Modify: `src/agent/bm25_debug_service.py`
- Test: `tests/test_bm25_debug_service.py`

**Interfaces:**
- Consumes: existing `validate_question(question)`, `build_retrieval_config(base_config, params)`, `serialize_debug_response(question, retrieved, stats)`, `FinancialQAAgent`, `BM25Retriever`, `IntentTermSelector`.
- Produces:
  - `load_debug_questions(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]`
  - `normalize_steps(steps: Optional[Dict[str, Any]]) -> Dict[str, bool]`
  - `build_debug_config(base_config: Dict[str, Any], params: Optional[Dict[str, Any]], steps: Optional[Dict[str, bool]]) -> Dict[str, Any]`
  - `run_intent_selection(agent: FinancialQAAgent, question: Dict[str, Any]) -> Dict[str, Any]`
  - `run_question_flow(question: Dict[str, Any], steps: Optional[Dict[str, Any]] = None, params: Optional[Dict[str, Any]] = None, config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]`

- [ ] **Step 1: Write failing tests for question loading and step defaults**

Append these tests to `tests/test_bm25_debug_service.py`:

```python
import json
from pathlib import Path

from src.agent.bm25_debug_service import load_debug_questions, normalize_steps


def test_normalize_steps_defaults_all_enabled():
    assert normalize_steps(None) == {"intent": True, "bm25": True, "answer": True}


def test_normalize_steps_accepts_false_values():
    assert normalize_steps({"intent": False, "bm25": True, "answer": False}) == {
        "intent": False,
        "bm25": True,
        "answer": False,
    }


def test_load_debug_questions_filters_to_mcq_and_multi(tmp_path):
    questions_dir = tmp_path / "questions"
    questions_dir.mkdir()
    (questions_dir / "sample_questions.json").write_text(
        json.dumps(
            [
                {
                    "qid": "q2",
                    "domain": "research",
                    "question": "多选题",
                    "options": {"A": "a"},
                    "answer_format": "multi",
                    "doc_ids": ["d2"],
                },
                {
                    "qid": "q1",
                    "domain": "insurance",
                    "question": "单选题",
                    "options": {"A": "a"},
                    "answer_format": "mcq",
                    "doc_ids": ["d1"],
                },
                {
                    "qid": "q3",
                    "domain": "regulatory",
                    "question": "判断题",
                    "options": {"A": "对", "B": "错"},
                    "answer_format": "tf",
                    "doc_ids": ["d3"],
                },
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    config = {"data": {"questions_dir": str(questions_dir)}}

    result = load_debug_questions(config)

    assert result["ok"] is True
    assert [item["qid"] for item in result["questions"]] == ["q1", "q2"]
    assert result["stats"] == {"total": 2, "mcq": 1, "multi": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_bm25_debug_service.py::test_normalize_steps_defaults_all_enabled tests/test_bm25_debug_service.py::test_normalize_steps_accepts_false_values tests/test_bm25_debug_service.py::test_load_debug_questions_filters_to_mcq_and_multi -v
```

Expected: FAIL because `normalize_steps` and `load_debug_questions` do not exist.

- [ ] **Step 3: Implement question loading and step normalization**

Add imports in `src/agent/bm25_debug_service.py`:

```python
import json
from pathlib import Path
```

Add functions:

```python
STEP_KEYS = ("intent", "bm25", "answer")


def normalize_steps(steps: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    steps = steps or {}
    return {key: bool(steps.get(key, True)) for key in STEP_KEYS}


def _public_question(question: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "qid": question.get("qid", ""),
        "domain": question.get("domain", ""),
        "answer_format": question.get("answer_format", ""),
        "question": question.get("question", ""),
        "options": question.get("options", {}),
        "doc_ids": question.get("doc_ids", []),
        "split": question.get("split", ""),
    }


def load_debug_questions(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base_config = config or load_config()
    questions_dir = Path(base_config["data"]["questions_dir"])
    questions: List[Dict[str, Any]] = []
    counts = {"mcq": 0, "multi": 0}

    for path in sorted(questions_dir.glob("*_questions.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            answer_format = item.get("answer_format")
            if answer_format not in counts:
                continue
            questions.append(_public_question(item))
            counts[answer_format] += 1

    questions.sort(key=lambda item: str(item.get("qid", "")))
    return {
        "ok": True,
        "questions": questions,
        "stats": {"total": len(questions), **counts},
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/test_bm25_debug_service.py::test_normalize_steps_defaults_all_enabled tests/test_bm25_debug_service.py::test_normalize_steps_accepts_false_values tests/test_bm25_debug_service.py::test_load_debug_questions_filters_to_mcq_and_multi -v
```

Expected: PASS.

- [ ] **Step 5: Write failing tests for flow execution without real LLM**

Append:

```python
from src.agent.bm25_debug_service import run_question_flow


def test_run_question_flow_bm25_only_skips_answer(monkeypatch):
    from src.agent.agent import Evidence, FinancialQAAgent

    def fake_load_evidence(self, question):
        return [
            Evidence(
                doc_id="doc1",
                source="financial_reports/doc1",
                content="营业收入 100 亿元，净利润 8 亿元。",
            )
        ]

    def fail_answer(self, question):
        raise AssertionError("answer_question should not be called")

    monkeypatch.setattr(FinancialQAAgent, "_load_evidence", fake_load_evidence)
    monkeypatch.setattr(FinancialQAAgent, "answer_question", fail_answer)

    question = {
        "qid": "q1",
        "domain": "financial_reports",
        "question": "营业收入是多少？",
        "options": {"A": "100 亿元", "B": "50 亿元"},
        "answer_format": "mcq",
        "doc_ids": ["doc1"],
    }
    config = {
        "retrieval": {
            "bm25_k1": 1.5,
            "bm25_b": 0.75,
            "chunk_size_chars": 30,
            "chunk_overlap_chars": 0,
            "min_chunk_chars": 1,
            "expand_before_chars": 0,
            "expand_after_chars": 0,
            "merge_gap_chars": 0,
            "per_doc_min": 1,
            "per_doc_max": 2,
            "global_top_k": 2,
            "max_total_chars": 500,
            "min_score": 0.1,
            "max_query_terms": 120,
            "intent_terms": {"enabled": False, "max_terms": 8},
        },
        "data": {},
        "model": {},
    }

    result = run_question_flow(
        question,
        steps={"intent": False, "bm25": True, "answer": False},
        params={"global_top_k": 1},
        config=config,
    )

    assert result["ok"] is True
    assert result["intent"] == {"enabled": False, "terms": [], "token_usage": {}}
    assert result["bm25"]["enabled"] is True
    assert result["bm25"]["stats"]["retrieved_windows"] >= 1
    assert result["answer"] == {"enabled": False}


def test_run_question_flow_answer_uses_agent_and_returns_usage(monkeypatch):
    from src.agent.agent import Evidence, FinancialQAAgent

    def fake_answer(self, question):
        assert self.config["retrieval"]["intent_terms"]["enabled"] is False
        return (
            "A",
            [Evidence(doc_id="doc1", source="financial_reports/doc1", content="证据")],
            {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "first_answer": "A",
                "reflected": False,
                "reflection_decision": "",
                "reflection_trigger_reason": "",
            },
        )

    monkeypatch.setattr(FinancialQAAgent, "answer_question", fake_answer)

    question = {
        "qid": "q1",
        "domain": "financial_reports",
        "question": "营业收入是多少？",
        "options": {"A": "100 亿元"},
        "answer_format": "mcq",
        "doc_ids": ["doc1"],
    }
    config = {
        "retrieval": {
            "enabled": True,
            "method": "bm25",
            "intent_terms": {"enabled": True, "max_terms": 8},
        },
        "model": {},
        "reflection": {},
        "data": {},
    }

    result = run_question_flow(
        question,
        steps={"intent": False, "bm25": False, "answer": True},
        config=config,
    )

    assert result["ok"] is True
    assert result["bm25"] == {"enabled": False}
    assert result["answer"]["enabled"] is True
    assert result["answer"]["answer"] == "A"
    assert result["answer"]["total_tokens"] == 12
```

- [ ] **Step 6: Run flow tests to verify they fail**

Run:

```bash
pytest tests/test_bm25_debug_service.py::test_run_question_flow_bm25_only_skips_answer tests/test_bm25_debug_service.py::test_run_question_flow_answer_uses_agent_and_returns_usage -v
```

Expected: FAIL because `run_question_flow` does not exist.

- [ ] **Step 7: Implement debug config and flow execution**

Add this implementation to `src/agent/bm25_debug_service.py`:

```python
def build_debug_config(
    base_config: Dict[str, Any],
    params: Optional[Dict[str, Any]],
    steps: Optional[Dict[str, bool]],
) -> Dict[str, Any]:
    debug_config = deepcopy(base_config)
    retrieval_cfg = build_retrieval_config(debug_config, params)
    step_flags = normalize_steps(steps)
    intent_cfg = dict(retrieval_cfg.get("intent_terms", {}) or {})
    intent_cfg["enabled"] = bool(intent_cfg.get("enabled", False)) and step_flags["intent"]
    retrieval_cfg["intent_terms"] = intent_cfg
    debug_config["retrieval"] = retrieval_cfg
    return debug_config


def _empty_token_usage() -> Dict[str, int]:
    return {}


def _serialize_usage(usage: Any) -> Dict[str, int]:
    if not usage:
        return {}
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0)),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0)),
        "total_tokens": int(getattr(usage, "total_tokens", 0)),
    }


def run_intent_selection(agent: FinancialQAAgent, question: Dict[str, Any]) -> Dict[str, Any]:
    active_question = dict(question)
    selector = getattr(agent, "intent_selector", IntentTermSelector(enabled=False))
    terms = selector.select(
        active_question,
        getattr(agent, "intent_llm", agent.llm),
        BM25Retriever.INTENT_TERMS,
    )
    if terms:
        active_question["_intent_terms"] = terms
    return {
        "enabled": True,
        "terms": terms,
        "token_usage": _serialize_usage(getattr(selector, "last_usage", None)),
        "question": active_question,
    }


def _serialize_answer_result(answer: str, token_usage: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "enabled": True,
        "answer": answer,
    }
    result.update(token_usage or {})
    return result


def run_question_flow(
    question: Dict[str, Any],
    steps: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    question = validate_question(question)
    step_flags = normalize_steps(steps)
    base_config = deepcopy(config or load_config())
    debug_config = build_debug_config(base_config, params, step_flags)
    response: Dict[str, Any] = {
        "ok": True,
        "question": _public_question(question),
        "steps": step_flags,
        "intent": {"enabled": False, "terms": [], "token_usage": {}},
        "bm25": {"enabled": False},
        "answer": {"enabled": False},
    }

    active_question = dict(question)

    if step_flags["intent"]:
        agent = FinancialQAAgent(debug_config)
        intent_result = run_intent_selection(agent, active_question)
        active_question = intent_result.pop("question")
        response["intent"] = intent_result

    if step_flags["bm25"]:
        bm25_payload = run_debug_search(active_question, params, debug_config)
        response["bm25"] = {
            "enabled": True,
            "stats": bm25_payload.get("stats", {}),
            "chunks": bm25_payload.get("chunks", []),
            "retrieved": bm25_payload.get("retrieved", []),
        }

    if step_flags["answer"]:
        agent = FinancialQAAgent(debug_config)
        answer, _evidence, token_usage = agent.answer_question(question)
        response["answer"] = _serialize_answer_result(answer, token_usage)

    return response
```

- [ ] **Step 8: Run service tests**

Run:

```bash
pytest tests/test_bm25_debug_service.py -v
```

Expected: PASS.

- [ ] **Step 9: Commit Task 1**

Run:

```bash
git add src/agent/bm25_debug_service.py tests/test_bm25_debug_service.py
git commit -m "feat(debug): add full QA flow service"
```

---

### Task 2: HTTP Endpoints for Questions and Full Flow

**Files:**
- Modify: `scripts/bm25_debug_server.py`
- Test: `tests/test_bm25_debug_server.py`

**Interfaces:**
- Consumes: `load_debug_questions(config=None) -> Dict[str, Any]`, `run_question_flow(question, steps, params, config=None) -> Dict[str, Any]`.
- Produces:
  - `handle_api_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]`
  - `GET /api/questions`
  - `POST /api/run-question`

- [ ] **Step 1: Write failing tests for route helper**

Append to `tests/test_bm25_debug_server.py`:

```python
from scripts import bm25_debug_server
from scripts.bm25_debug_server import handle_api_post


def test_handle_api_post_run_question_delegates(monkeypatch):
    captured = {}

    def fake_run_question_flow(question, steps, params):
        captured["question"] = question
        captured["steps"] = steps
        captured["params"] = params
        return {"ok": True, "answer": {"enabled": False}}

    monkeypatch.setattr(bm25_debug_server, "run_question_flow", fake_run_question_flow)

    result = handle_api_post(
        "/api/run-question",
        {
            "question": {"qid": "q1"},
            "steps": {"intent": False},
            "params": {"global_top_k": 1},
        },
    )

    assert result == {"ok": True, "answer": {"enabled": False}}
    assert captured == {
        "question": {"qid": "q1"},
        "steps": {"intent": False},
        "params": {"global_top_k": 1},
    }


def test_handle_api_post_unknown_route_returns_error():
    assert handle_api_post("/api/missing", {}) == {"ok": False, "error": "Not found"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/test_bm25_debug_server.py::test_handle_api_post_run_question_delegates tests/test_bm25_debug_server.py::test_handle_api_post_unknown_route_returns_error -v
```

Expected: FAIL because `handle_api_post` does not exist.

- [ ] **Step 3: Implement endpoint helper and imports**

Update imports in `scripts/bm25_debug_server.py`:

```python
from src.agent.bm25_debug_service import (
    load_debug_questions,
    run_debug_search,
    run_question_flow,
)
```

Add:

```python
def handle_api_post(path: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    if path == "/api/search":
        return run_debug_search(payload.get("question"), payload.get("params", {}))
    if path == "/api/run-question":
        return run_question_flow(
            payload.get("question"),
            payload.get("steps", {}),
            payload.get("params", {}),
        )
    return make_error("Not found")
```

Update `do_GET`:

```python
if self.path == "/api/questions":
    self._send_json(load_debug_questions())
    return
```

Update `do_POST` to use `handle_api_post` and preserve `404` for unknown POST paths:

```python
payload = parse_json_body(self.rfile.read(length))
result = handle_api_post(self.path, payload)
status = 404 if not result.get("ok") and result.get("error") == "Not found" else 200
self._send_json(result, status=status)
```

- [ ] **Step 4: Run server tests**

Run:

```bash
pytest tests/test_bm25_debug_server.py -v
```

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

Run:

```bash
git add scripts/bm25_debug_server.py tests/test_bm25_debug_server.py
git commit -m "feat(debug): expose QA debug endpoints"
```

---

### Task 3: Browser UI for Question List and Step Results

**Files:**
- Modify: `scripts/bm25_debug_ui.html`

**Interfaces:**
- Consumes: `GET /api/questions`, `POST /api/run-question`.
- Produces browser functions:
  - `loadQuestions()`
  - `renderQuestionList()`
  - `renderQuestionDetail()`
  - `collectSteps()`
  - `runSelectedQuestion()`
  - `renderFlowResults(payload)`

- [ ] **Step 1: Preserve the current page as a fallback reference**

Read `scripts/bm25_debug_ui.html` before editing and keep the existing parameter form names. The parameter input IDs must stay in the form `param_${key}` so `collectParams()` continues to work.

- [ ] **Step 2: Replace the static HTML body with workbench panels**

Use this body structure:

```html
<body>
  <header>
    <h1>QA 全流程调试</h1>
    <div class="toolbar">
      <button id="reloadQuestionsBtn" type="button">刷新题库</button>
      <button id="resetParamsBtn" type="button">重置参数</button>
      <button id="runBtn" class="primary" type="button">启动</button>
    </div>
  </header>
  <main>
    <section>
      <h2>问题列表</h2>
      <div id="errorBox" class="error"></div>
      <input id="filterInput" type="search" placeholder="qid / domain / 题型 / 题干">
      <div id="questionStats" class="summary"></div>
      <div id="questionList" class="question-list"></div>
    </section>
    <section>
      <h2>当前问题</h2>
      <div id="questionDetail" class="empty">请选择问题</div>
      <h2>流程</h2>
      <div class="step-list">
        <label class="check-row"><input id="stepIntent" type="checkbox" checked> 意图识别</label>
        <label class="check-row"><input id="stepBm25" type="checkbox" checked> BM25 检索</label>
        <label class="check-row"><input id="stepAnswer" type="checkbox" checked> 最终答案生成</label>
      </div>
      <h2>BM25 参数</h2>
      <div id="paramsForm" class="params"></div>
    </section>
    <section>
      <h2>结果</h2>
      <div id="results" class="empty">等待启动</div>
    </section>
  </main>
</body>
```

- [ ] **Step 3: Update CSS for dense workbench UI**

Keep the existing palette and add:

```css
.summary {
  color: var(--muted);
  font-size: 12px;
  margin: 8px 0;
}
.question-text {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-top: 4px;
  line-height: 1.4;
}
.check-row {
  display: flex;
  gap: 8px;
  align-items: center;
  margin: 8px 0;
  color: var(--text);
}
.check-row input {
  width: auto;
}
.detail-block,
.result-block {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px;
  margin-bottom: 10px;
  background: #fbfcfe;
}
.option-row {
  margin: 6px 0;
}
.answer-value {
  font-size: 30px;
  font-weight: 700;
  color: var(--accent);
}
.term-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.term {
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 8px;
  background: white;
  font-size: 12px;
}
```

- [ ] **Step 4: Replace JavaScript state and loading functions**

Use this state:

```javascript
let questions = [];
let selectedIndex = -1;
let isRunning = false;
```

Add:

```javascript
async function loadQuestions() {
  showError("");
  el("questionList").innerHTML = '<div class="empty">加载题库中</div>';
  const response = await fetch("/api/questions");
  const payload = await response.json();
  if (!payload.ok) {
    showError(payload.error || "题库加载失败");
    return;
  }
  questions = payload.questions || [];
  selectedIndex = questions.length ? 0 : -1;
  renderQuestionStats(payload.stats || {});
  renderQuestionList();
  renderQuestionDetail();
}

function renderQuestionStats(stats) {
  el("questionStats").textContent = `共 ${stats.total || 0} 题，单选 ${stats.mcq || 0}，多选 ${stats.multi || 0}`;
}
```

- [ ] **Step 5: Implement question list and detail rendering**

Use:

```javascript
function renderQuestionList() {
  const filter = el("filterInput").value.trim().toLowerCase();
  const list = el("questionList");
  list.innerHTML = "";
  questions.forEach((q, index) => {
    const haystack = `${q.qid || ""} ${q.domain || ""} ${q.answer_format || ""} ${q.question || ""}`.toLowerCase();
    if (filter && !haystack.includes(filter)) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `question-item${index === selectedIndex ? " active" : ""}`;
    btn.innerHTML = `
      <strong>${escapeHtml(q.qid || `#${index + 1}`)}</strong>
      <span>${escapeHtml(q.domain || "")} / ${escapeHtml(q.answer_format || "")}</span>
      <span class="question-text">${escapeHtml((q.question || "").slice(0, 90))}</span>
    `;
    btn.onclick = () => {
      selectedIndex = index;
      renderQuestionList();
      renderQuestionDetail();
    };
    list.appendChild(btn);
  });
  if (!list.children.length) {
    list.innerHTML = '<div class="empty">没有匹配问题</div>';
  }
}

function renderQuestionDetail() {
  const q = questions[selectedIndex];
  if (!q) {
    el("questionDetail").className = "empty";
    el("questionDetail").textContent = "请选择问题";
    return;
  }
  const options = q.options || {};
  el("questionDetail").className = "detail-block";
  el("questionDetail").innerHTML = `
    <div class="meta">
      <span>qid=${escapeHtml(q.qid)}</span>
      <span>domain=${escapeHtml(q.domain)}</span>
      <span>type=${escapeHtml(q.answer_format)}</span>
    </div>
    <pre>${escapeHtml(q.question || "")}</pre>
    <div>${Object.entries(options).map(([key, value]) => `
      <div class="option-row"><strong>${escapeHtml(key)}.</strong> ${escapeHtml(value)}</div>
    `).join("")}</div>
    <label>doc_ids</label>
    <pre>${escapeHtml((q.doc_ids || []).join(", "))}</pre>
  `;
}
```

- [ ] **Step 6: Implement flow execution and result rendering**

Use:

```javascript
function collectSteps() {
  return {
    intent: el("stepIntent").checked,
    bm25: el("stepBm25").checked,
    answer: el("stepAnswer").checked
  };
}

async function runSelectedQuestion() {
  const q = questions[selectedIndex];
  if (!q) {
    showError("请先选择一道题");
    return;
  }
  isRunning = true;
  el("runBtn").disabled = true;
  el("results").className = "empty";
  el("results").textContent = "运行中";
  try {
    const response = await fetch("/api/run-question", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({question: q, steps: collectSteps(), params: collectParams()})
    });
    const payload = await response.json();
    if (!payload.ok) {
      showError(payload.error || "运行失败");
      el("results").textContent = "运行失败";
      return;
    }
    showError("");
    renderFlowResults(payload);
  } finally {
    isRunning = false;
    el("runBtn").disabled = false;
  }
}

function renderFlowResults(payload) {
  el("results").className = "";
  el("results").innerHTML = `
    ${renderIntentResult(payload.intent || {})}
    ${renderBm25Result(payload.bm25 || {})}
    ${renderAnswerResult(payload.answer || {})}
  `;
}
```

Add render helpers:

```javascript
function renderIntentResult(intent) {
  if (!intent.enabled) {
    return '<div class="result-block"><h2>意图识别结果</h2><div class="empty">未执行</div></div>';
  }
  const terms = intent.terms || [];
  return `
    <div class="result-block">
      <h2>意图识别结果</h2>
      <div class="term-list">${terms.map(term => `<span class="term">${escapeHtml(term)}</span>`).join("") || '<span class="empty">无意图词</span>'}</div>
      <pre>${escapeHtml(JSON.stringify(intent.token_usage || {}, null, 2))}</pre>
    </div>
  `;
}

function renderBm25Result(bm25) {
  if (!bm25.enabled) {
    return '<div class="result-block"><h2>BM25 结果</h2><div class="empty">未执行</div></div>';
  }
  const stats = bm25.stats || {};
  const chunks = bm25.chunks || bm25.retrieved || [];
  return `
    <div class="result-block">
      <h2>BM25 结果</h2>
      ${renderStatsHtml(stats)}
      ${chunks.map(renderChunkHtml).join("") || '<div class="empty">没有命中片段</div>'}
    </div>
  `;
}

function renderStatsHtml(stats) {
  const keys = ["query_count", "chunk_count", "candidate_count", "retrieved_windows", "doc_coverage", "max_bm25_score", "avg_bm25_score"];
  return `<div class="stat-grid">${keys.map(key => `
    <div class="stat"><strong>${escapeHtml(formatNumber(stats[key]))}</strong><span>${key}</span></div>
  `).join("")}</div>`;
}

function renderChunkHtml(chunk) {
  return `
    <article class="chunk">
      <div class="meta">
        <span>doc_id=${escapeHtml(chunk.doc_id)}</span>
        <span>source=${escapeHtml(chunk.source)}</span>
        <span>pos=${escapeHtml(chunk.start ?? "")}-${escapeHtml(chunk.end ?? "")}</span>
        <span>score=${escapeHtml(formatNumber(chunk.score ?? chunk.relevance_score))}</span>
        <span>query=${escapeHtml((chunk.query_types || []).join(","))}</span>
      </div>
      <pre>${escapeHtml(chunk.text || chunk.content || "")}</pre>
    </article>
  `;
}

function renderAnswerResult(answer) {
  if (!answer.enabled) {
    return '<div class="result-block"><h2>最终答案</h2><div class="empty">未执行</div></div>';
  }
  return `
    <div class="result-block">
      <h2>最终答案</h2>
      <div class="answer-value">${escapeHtml(answer.answer || "")}</div>
      <pre>${escapeHtml(JSON.stringify(answer, null, 2))}</pre>
    </div>
  `;
}
```

- [ ] **Step 7: Wire events**

Use:

```javascript
el("filterInput").oninput = renderQuestionList;
el("runBtn").onclick = () => runSelectedQuestion().catch(err => showError(err.message));
el("resetParamsBtn").onclick = renderParams;
el("reloadQuestionsBtn").onclick = () => loadQuestions().catch(err => showError(err.message));
renderParams();
loadQuestions().catch(err => showError(err.message));
```

Remove file upload and JSON textarea logic from the previous UI.

- [ ] **Step 8: Manually inspect page source for broken strings**

Run:

```bash
python -m py_compile scripts/bm25_debug_server.py
```

Expected: no output and exit code 0.

- [ ] **Step 9: Commit Task 3**

Run:

```bash
git add scripts/bm25_debug_ui.html
git commit -m "feat(debug): add full QA debug UI"
```

---

### Task 4: Verification and Local Run

**Files:**
- No required source edits unless tests fail.

**Interfaces:**
- Consumes: completed Tasks 1-3.
- Produces: verified local URL and passing targeted tests.

- [ ] **Step 1: Run targeted tests**

Run:

```bash
pytest tests/test_bm25_debug_service.py tests/test_bm25_debug_server.py -v
```

Expected: PASS.

- [ ] **Step 2: Run the existing smoke tests if time allows**

Run:

```bash
pytest tests/test_smoke.py tests/test_intent_terms.py -v
```

Expected: PASS. If `test_intent_terms.py` is untracked or already failing from pre-existing work, report that separately and do not modify unrelated tests.

- [ ] **Step 3: Start the local server**

Run:

```bash
python scripts/bm25_debug_server.py --host 127.0.0.1 --port 8765
```

Expected terminal line:

```text
BM25 debug UI: http://127.0.0.1:8765
```

- [ ] **Step 4: Verify API endpoints manually**

In a separate command while the server runs:

```bash
python -c "import json, urllib.request; print(json.load(urllib.request.urlopen('http://127.0.0.1:8765/api/questions'))['stats'])"
```

Expected: prints a dict with nonzero `total`, `mcq`, and `multi`.

- [ ] **Step 5: Stop the server**

Stop the server with Ctrl+C. Do not leave background server processes running.

- [ ] **Step 6: Final status check**

Run:

```bash
git status --short
```

Expected: only unrelated pre-existing changes remain, or no changes if all task commits were created.
