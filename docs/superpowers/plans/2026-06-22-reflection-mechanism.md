# BM25 低置信度反思机制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `FinancialQAAgent` 上增加条件性反思环节：当 BM25 检索置信度低（最高分低于阈值或 top1/top2 分差小）时，复用首轮 evidence 让同一个 LLM 对初答做二次核验，输出 KEEP/CHANGE 决策以提升准确率。

**Architecture:** 在 `answer_question` 末尾追加反思分支。新增 `ReflectionPromptBuilder`、`should_reflect` 辅助函数、反思答案解析器。复用现有 LLMClient 和首轮 evidence，不重新检索。所有触发阈值通过 config 控制，默认开启但可随时关闭回退。

**Tech Stack:** Python 3.10+，pytest，OpenAI/Anthropic SDK（已有），YAML config。

---

## File Structure

| 文件 | 操作 | 职责 |
|------|------|------|
| `config/config.yaml` | Modify | 新增 `reflection` 配置块 |
| `src/agent/agent.py` | Modify | 新增 `ReflectionPromptBuilder`、`should_reflect`、`_reflect_answer`、`_parse_reflection_decision`；扩展 `BM25Retriever.retrieve` 的 stats；扩展 `answer_question` 流程 |
| `src/agent/run.py` | Modify | diagnostics CSV 新增反思字段列 |
| `tests/test_reflection.py` | Create | 反思机制全部单元测试 |

文件改动是单文件的（除了 config 和 run.py 加列），`agent.py` 已经是 1040 行，本次新增估计 150 行左右，仍在可接受范围。不做拆分。

---

## Task 1: 新增 reflection config 块

**Files:**
- Modify: `config/config.yaml`

- [ ] **Step 1: 在 config.yaml 末尾追加 reflection 配置块**

在 `logging:` 块之前插入：

```yaml
# 反思机制：BM25 低置信度时让 LLM 自评修正
reflection:
  enabled: true
  low_score_threshold: 80.0      # max_bm25_score < 此值时触发
  top_gap_ratio: 0.15            # (top1-top2)/top1 < 此值时触发
  log_decisions: true            # 记录触发原因到结果 dict
```

- [ ] **Step 2: 验证 YAML 语法正确**

Run: `python -c "import yaml; yaml.safe_load(open('config/config.yaml', encoding='utf-8'))"`
Expected: 无输出（解析成功）

- [ ] **Step 3: Commit**

```bash
git add config/config.yaml
git commit -m "feat(config): add reflection mechanism config block"
```

---

## Task 2: 扩展 BM25Retriever stats 暴露 top1/top2 score

**Files:**
- Modify: `src/agent/agent.py` 中 `BM25Retriever.retrieve` 方法的 stats 字典（约 line 385-406）
- Test: `tests/test_reflection.py`

- [ ] **Step 1: 创建测试文件并写失败测试**

Create `tests/test_reflection.py`:

```python
"""反思机制单元测试。"""
from unittest.mock import MagicMock, patch

import pytest

from src.agent.agent import (
    BM25Retriever,
    ContextManager,
    Evidence,
    FinancialQAAgent,
    ReflectionPromptBuilder,
    _parse_reflection_decision,
    should_reflect,
)


@pytest.fixture
def fake_evidence():
    return [
        Evidence(
            doc_id="text01",
            content="金融机构应当识别客户的受益所有人，持股 25% 以上的自然人属于受益所有人。",
            source="regulatory/text01",
        ),
    ]


@pytest.fixture
def fake_question():
    return {
        "qid": "reg_a_001",
        "domain": "regulatory",
        "split": "A",
        "question": "下列哪些属于应当识别的受益所有人？",
        "options": {
            "A": "公司高管",
            "B": "实际控制人",
            "C": "持股 25% 自然人",
            "D": "员工",
        },
        "answer_format": "multi",
        "doc_ids": ["text01"],
    }


def test_bm25_stats_exposes_top1_top2_scores(fake_question, fake_evidence):
    """BM25Retriever.retrieve 返回的 stats 应包含 top1_score 和 top2_score。"""
    retriever = BM25Retriever({"min_score": 0.1, "log_retrieval": False})
    _, stats = retriever.retrieve(fake_question, fake_evidence)
    assert "top1_score" in stats
    assert "top2_score" in stats
    assert isinstance(stats["top1_score"], float)
    # 只有一个文档 + 一个 chunk 时 top2 可能为 0.0，但字段必须存在
    assert stats["top1_score"] >= stats["top2_score"]


def test_bm25_stats_top_scores_reflect_ranking(fake_question):
    """多 chunk 场景下 top1 应大于 top2。"""
    long_evidence = [
        Evidence(
            doc_id="text01",
            content="受益所有人 持股 25% 自然人 受益所有人 持股 25% 自然人\n"
            + "无关内容\n" * 50
            + "另一段受益所有人内容",
            source="regulatory/text01",
        ),
    ]
    retriever = BM25Retriever({"min_score": 0.1, "log_retrieval": False})
    _, stats = retriever.retrieve(fake_question, long_evidence)
    assert stats["top1_score"] >= 0.0
    assert stats["top2_score"] <= stats["top1_score"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflection.py::test_bm25_stats_exposes_top1_top2_scores -v`
Expected: FAIL — `KeyError: 'top1_score'`

- [ ] **Step 3: 在 `BM25Retriever.retrieve` 的 stats 字典里增加 top1/top2 字段**

Locate the `stats = {...}` block in `BM25Retriever.retrieve` (around line 385). The current code computes `max_bm25_score` from `limited`. Add top1/top2 derivation right after `limited` is finalized (before the stats dict is constructed).

Insert this snippet immediately after `selected_sources = [...]` (around line 383) and before `stats = {`:

```python
        sorted_scores = sorted(
            (item["score"] for item in limited), reverse=True
        )
        top1_score = sorted_scores[0] if sorted_scores else 0.0
        top2_score = sorted_scores[1] if len(sorted_scores) >= 2 else 0.0
```

Then in the `stats = {...}` dict, add two new keys right after `"avg_bm25_score"`:

```python
            "top1_score": top1_score,
            "top2_score": top2_score,
```

Also update `_empty_stats` (around line 418) to include these keys:

```python
    def _empty_stats(self, method: str, chunk_count: int, query_count: int) -> Dict:
        return {
            "retrieval_method": method,
            "query_count": query_count,
            "chunk_count": chunk_count,
            "candidate_count": 0,
            "retrieved_windows": 0,
            "retrieved_chars": 0,
            "doc_coverage": 0,
            "max_bm25_score": 0.0,
            "avg_bm25_score": 0.0,
            "top1_score": 0.0,
            "top2_score": 0.0,
            "selected_sources": [],
            "retrieval_doc_stats": {},
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reflection.py::test_bm25_stats_exposes_top1_top2_scores tests/test_reflection.py::test_bm25_stats_top_scores_reflect_ranking -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_reflection.py
git commit -m "feat(agent): expose top1/top2 bm25 scores in retriever stats"
```

---

## Task 3: 实现 `should_reflect` 触发逻辑

**Files:**
- Modify: `src/agent/agent.py`（新增模块级函数）
- Test: `tests/test_reflection.py`

- [ ] **Step 1: 追加失败测试**

Append to `tests/test_reflection.py`:

```python
def test_should_reflect_triggers_on_low_max_score():
    """max_bm25_score 低于阈值时触发。"""
    stats = {"max_bm25_score": 50.0, "top1_score": 50.0, "top2_score": 0.0, "retrieved_windows": 3}
    config = {"enabled": True, "low_score_threshold": 80.0, "top_gap_ratio": 0.15}
    triggered, reason = should_reflect(stats, config)
    assert triggered is True
    assert reason == "low_score"


def test_should_reflect_triggers_on_small_gap():
    """top1/top2 分差比例小于阈值时触发。"""
    stats = {
        "max_bm25_score": 200.0,
        "top1_score": 200.0,
        "top2_score": 180.0,  # gap = 0.1 < 0.15
        "retrieved_windows": 3,
    }
    config = {"enabled": True, "low_score_threshold": 80.0, "top_gap_ratio": 0.15}
    triggered, reason = should_reflect(stats, config)
    assert triggered is True
    assert reason == "small_gap"


def test_should_reflect_no_trigger_when_high_score_clear_gap():
    """max_bm25_score 高且分差大时不触发。"""
    stats = {
        "max_bm25_score": 200.0,
        "top1_score": 200.0,
        "top2_score": 100.0,  # gap = 0.5 > 0.15
        "retrieved_windows": 3,
    }
    config = {"enabled": True, "low_score_threshold": 80.0, "top_gap_ratio": 0.15}
    triggered, reason = should_reflect(stats, config)
    assert triggered is False
    assert reason == ""


def test_should_reflect_short_circuit_on_zero_windows():
    """retrieved_windows == 0 时短路返回不触发（救不回来，省 token）。"""
    stats = {
        "max_bm25_score": 0.0,
        "top1_score": 0.0,
        "top2_score": 0.0,
        "retrieved_windows": 0,
    }
    config = {"enabled": True, "low_score_threshold": 80.0, "top_gap_ratio": 0.15}
    triggered, reason = should_reflect(stats, config)
    assert triggered is False
    assert reason == ""


def test_should_reflect_skips_gap_when_only_one_window():
    """只有 1 个 window（top2_score == 0）时跳过 gap 条件。"""
    stats = {
        "max_bm25_score": 100.0,
        "top1_score": 100.0,
        "top2_score": 0.0,
        "retrieved_windows": 1,
    }
    config = {"enabled": True, "low_score_threshold": 80.0, "top_gap_ratio": 0.15}
    triggered, _ = should_reflect(stats, config)
    assert triggered is False


def test_should_reflect_disabled_via_config():
    """config.enabled = False 时永不触发。"""
    stats = {
        "max_bm25_score": 10.0,
        "top1_score": 10.0,
        "top2_score": 9.0,
        "retrieved_windows": 3,
    }
    config = {"enabled": False, "low_score_threshold": 80.0, "top_gap_ratio": 0.15}
    triggered, reason = should_reflect(stats, config)
    assert triggered is False
    assert reason == ""
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflection.py -k should_reflect -v`
Expected: FAIL — `ImportError: cannot import name 'should_reflect'`

- [ ] **Step 3: 在 agent.py 实现 `should_reflect` 函数**

Insert this module-level function right before the `FinancialQAAgent` class definition (before line 784):

```python
def should_reflect(retrieval_stats: Dict, reflection_config: Dict) -> Tuple[bool, str]:
    """根据 BM25 检索 stats 和 reflection 配置判断是否需要反思。

    返回 (是否触发, 触发原因)。
    原因可能是 ""（不触发）、"low_score"、"small_gap"。
    """
    if not reflection_config.get("enabled", False):
        return False, ""

    retrieved_windows = retrieval_stats.get("retrieved_windows", 0)
    if retrieved_windows == 0:
        return False, ""

    low_threshold = float(reflection_config.get("low_score_threshold", 80.0))
    top_gap_ratio = float(reflection_config.get("top_gap_ratio", 0.15))

    top1 = float(retrieval_stats.get("top1_score", 0.0))
    top2 = float(retrieval_stats.get("top2_score", 0.0))

    if top1 < low_threshold:
        return True, "low_score"

    # 仅当存在第二个候选时才比较 gap
    if top2 > 0 and top1 > 0:
        gap = (top1 - top2) / top1
        if gap < top_gap_ratio:
            return True, "small_gap"

    return False, ""
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reflection.py -k should_reflect -v`
Expected: PASS（所有 6 个 should_reflect 测试）

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_reflection.py
git commit -m "feat(agent): add should_reflect trigger logic"
```

---

## Task 4: 实现 `ReflectionPromptBuilder`

**Files:**
- Modify: `src/agent/agent.py`（新增类）
- Test: `tests/test_reflection.py`

- [ ] **Step 1: 追加失败测试**

Append to `tests/test_reflection.py`:

```python
def test_reflection_prompt_includes_question_options_evidence_and_first_answer(
    fake_question, fake_evidence,
):
    """反思 prompt 必须包含问题、所有选项、证据、首轮答案、KEEP/CHANGE 指令。"""
    builder = ReflectionPromptBuilder()
    prompt = builder.build_prompt(fake_question, fake_evidence, first_answer="BC")

    assert "下列哪些属于应当识别的受益所有人" in prompt
    assert "A. 公司高管" in prompt
    assert "B. 实际控制人" in prompt
    assert "C. 持股 25% 自然人" in prompt
    assert "D. 员工" in prompt
    assert "BC" in prompt  # first_answer 出现
    assert "KEEP" in prompt or "CHANGE" in prompt
    assert "金融机构应当识别客户的受益所有人" in prompt  # 证据内容


def test_reflection_prompt_respects_max_chars_truncation(fake_question):
    """超长 evidence 应被 ContextManager 截断，不直接撑爆 prompt。"""
    big_evidence = [
        Evidence(
            doc_id="text01",
            content="受益所有人" * 5000,
            source="regulatory/text01",
        ),
    ]
    builder = ReflectionPromptBuilder()
    from src.agent.agent import ContextManager
    cm = ContextManager(max_chars=5000, max_doc_chars=2000)
    prompt = builder.build_prompt(
        fake_question, big_evidence, "A", context_manager=cm,
    )
    assert len(prompt) <= 6000  # 截断后留一点余量
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflection.py -k ReflectionPromptBuilder -v`
Expected: FAIL — `ImportError: cannot import name 'ReflectionPromptBuilder'`

- [ ] **Step 3: 在 agent.py 实现 `ReflectionPromptBuilder`**

Insert this class right after the existing `PromptBuilder` class definition (after line 753):

```python
class ReflectionPromptBuilder:
    """反思 prompt 构建器：让 LLM 对初答做二次核验。"""

    def build_prompt(
        self,
        question: Dict,
        evidence: List[Evidence],
        first_answer: str,
        context_manager: Optional[ContextManager] = None,
    ) -> str:
        """构建反思提示词。

        结构：文档 + 问题 + 选项 + 初答 + KEEP/CHANGE 指令。
        """
        context_parts = []
        for ev in evidence:
            content = ev.content
            if context_manager:
                content = context_manager.truncate_doc(content)
            context_parts.append(f"【文档 {ev.doc_id}】\n{content}")
        context = "\n\n".join(context_parts)

        options_text = "\n".join(
            [f"{k}. {v}" for k, v in question.get("options", {}).items()]
        )

        prompt = (
            "你是一位金融文档分析专家。请对下面的初答进行复核。\n\n"
            "要求：\n"
            "1. 仔细阅读文档，找出支持初答的具体证据（引用原文片段）\n"
            "2. 对每个选项逐一判断：是否有明确证据支持或反驳\n"
            "3. 如果初答正确，输出 \"KEEP {答案字母}\"\n"
            "4. 如果发现错误，输出 \"CHANGE {答案字母}\"\n"
            "5. 最终输出格式必须为 KEEP 或 CHANGE 开头，紧跟答案字母（多选按字母顺序排列）\n\n"
            f"{context}\n\n"
            f"问题：{question['question']}\n\n"
            f"选项：\n{options_text}\n\n"
            f"初答：{first_answer}\n\n"
            f"反思结论："
        )
        return prompt
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reflection.py -k "ReflectionPromptBuilder or reflection_prompt" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_reflection.py
git commit -m "feat(agent): add ReflectionPromptBuilder for second-pass review"
```

---

## Task 5: 实现反思答案解析 `_parse_reflection_decision`

**Files:**
- Modify: `src/agent/agent.py`（新增模块级函数）
- Test: `tests/test_reflection.py`

- [ ] **Step 1: 追加失败测试**

Append to `tests/test_reflection.py`:

```python
def test_parse_reflection_keep_decision():
    """KEEP 输出应保留原字母。"""
    decision, answer = _parse_reflection_decision("KEEP A", "A", "mcq")
    assert decision == "KEEP"
    assert answer == "A"


def test_parse_reflection_change_decision():
    """CHANGE 输出应替换为新字母。"""
    decision, answer = _parse_reflection_decision("CHANGE B", "A", "mcq")
    assert decision == "CHANGE"
    assert answer == "B"


def test_parse_reflection_multi_keep():
    """多选题 KEEP 保留多字母组合。"""
    decision, answer = _parse_reflection_decision("KEEP ABC", "ABC", "multi")
    assert decision == "KEEP"
    assert answer == "ABC"


def test_parse_reflection_multi_change():
    """多选题 CHANGE 替换为新的多字母组合。"""
    decision, answer = _parse_reflection_decision("CHANGE ABD", "ABC", "multi")
    assert decision == "CHANGE"
    assert answer == "ABD"


def test_parse_reflection_handles_leading_text():
    """输出前有自然语言描述也应能提取决策。"""
    decision, answer = _parse_reflection_decision(
        "经过分析，初答正确。\nKEEP A", "A", "mcq",
    )
    assert decision == "KEEP"
    assert answer == "A"


def test_parse_reflection_lowercase_input():
    """大小写不敏感。"""
    decision, answer = _parse_reflection_decision("keep a", "A", "mcq")
    assert decision == "KEEP"
    assert answer == "A"


def test_parse_reflection_parse_fail_returns_first_answer():
    """无法解析时返回 PARSE_FAIL 并保留首轮答案。"""
    decision, answer = _parse_reflection_decision(
        "我认为选项 A 是对的", "A", "mcq",
    )
    assert decision == "PARSE_FAIL"
    assert answer == "A"  # fail-safe 保留首轮


def test_parse_reflection_invalid_letter_returns_first_answer():
    """CHANGE 后跟无效字母（如 E）时视为解析失败。"""
    decision, answer = _parse_reflection_decision("CHANGE E", "A", "mcq")
    assert decision == "PARSE_FAIL"
    assert answer == "A"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflection.py -k parse_reflection -v`
Expected: FAIL — `ImportError: cannot import name '_parse_reflection_decision'`

- [ ] **Step 3: 在 agent.py 实现 `_parse_reflection_decision`**

Insert this module-level function right after `should_reflect` (added in Task 3):

```python
def _parse_reflection_decision(
    raw: str, first_answer: str, answer_format: str,
) -> Tuple[str, str]:
    """从反思输出中提取决策和答案。

    支持格式：
    - "KEEP A" / "keep A"  → ("KEEP", "A")
    - "CHANGE B"           → ("CHANGE", "B")
    - "KEEP ABC" (multi)   → ("KEEP", "ABC")
    - 无法解析              → ("PARSE_FAIL", first_answer)  # fail-safe
    """
    import re

    text = raw.strip().upper()
    # 匹配 KEEP 或 CHANGE 开头，后跟可选分隔符，再跟 1-4 个 A-D 字母
    match = re.search(r"\b(KEEP|CHANGE)\s+([A-D]{1,4})\b", text)
    if not match:
        return "PARSE_FAIL", first_answer

    decision = match.group(1)
    raw_letters = match.group(2)

    # 走 normalize_answer + validate 以确保格式合法
    normalized = normalize_answer(raw_letters, answer_format)
    try:
        AnswerValidator.validate(normalized, answer_format)
    except ValueError:
        return "PARSE_FAIL", first_answer

    return decision, normalized
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_reflection.py -k parse_reflection -v`
Expected: PASS（所有 8 个 parse_reflection 测试）

- [ ] **Step 5: Commit**

```bash
git add src/agent/agent.py tests/test_reflection.py
git commit -m "feat(agent): add reflection decision parser with fail-safe"
```

---

## Task 6: 把反思环节接入 `FinancialQAAgent.answer_question`

**Files:**
- Modify: `src/agent/agent.py` 中 `FinancialQAAgent.__init__`（初始化 reflection config）和 `answer_question`（追加反思分支）

- [ ] **Step 1: 追加集成测试（mock LLM）**

Append to `tests/test_reflection.py`:

```python
def test_answer_question_triggers_reflection_on_low_score(
    fake_question, fake_evidence, monkeypatch,
):
    """max_bm25_score 低时应触发反思，最终答案取反思结果。"""
    # 构造一个 stats 看起来低置信的场景：用 mock 替换 retriever
    agent = FinancialQAAgent.__new__(FinancialQAAgent)
    agent.config = {
        "model": {"max_context_tokens": 80000},
        "retrieval": {"enabled": True, "max_doc_chars": 16000, "method": "bm25"},
        "reflection": {
            "enabled": True,
            "low_score_threshold": 80.0,
            "top_gap_ratio": 0.15,
            "log_decisions": True,
        },
        "data": {"markdown_dir": "data/merged_md"},
    }
    agent.context_manager = ContextManager(max_chars=99999, max_doc_chars=16000)
    agent.retrieval_enabled = True
    agent.retriever = MagicMock()
    # 模拟 BM25 检索置信度低（max=50 < 80）
    agent.retriever.retrieve.return_value = (
        fake_evidence,
        {
            "retrieval_method": "bm25_window",
            "retrieved_windows": 2,
            "max_bm25_score": 50.0,
            "top1_score": 50.0,
            "top2_score": 40.0,
        },
    )
    agent.prompt_builder = MagicMock()
    agent.prompt_builder.build_prompt.return_value = "FIRST_PROMPT"
    agent.reflection_prompt_builder = ReflectionPromptBuilder()
    agent.memory = MagicMock()

    # 模拟首轮 LLM 答案 A，反思 LLM 改为 B
    first_resp = MagicMock(content="A", finish_reason="stop")
    first_resp.usage = MagicMock(
        prompt_tokens=100, completion_tokens=10, total_tokens=110,
    )
    reflect_resp = MagicMock(content="CHANGE B", finish_reason="stop")
    reflect_resp.usage = MagicMock(
        prompt_tokens=120, completion_tokens=20, total_tokens=140,
    )
    agent.llm = MagicMock()
    agent.llm.chat.side_effect = [first_resp, reflect_resp]

    # 跳过真实文档加载
    agent._load_evidence = MagicMock(return_value=fake_evidence)

    answer, evidence, token_usage = agent.answer_question(fake_question)

    assert answer == "B"  # 反思后改为 B
    assert token_usage["reflected"] is True
    assert token_usage["first_answer"] == "A"
    assert token_usage["reflection_decision"] == "CHANGE"
    assert token_usage["reflection_trigger_reason"] == "low_score"
    # 两次 LLM 调用 token 应合并
    assert token_usage["total_tokens"] == 110 + 140


def test_answer_question_skips_reflection_when_confidence_high(
    fake_question, fake_evidence,
):
    """高置信度时不应触发反思，只调用一次 LLM。"""
    agent = FinancialQAAgent.__new__(FinancialQAAgent)
    agent.config = {
        "model": {"max_context_tokens": 80000},
        "retrieval": {"enabled": True, "max_doc_chars": 16000, "method": "bm25"},
        "reflection": {
            "enabled": True,
            "low_score_threshold": 80.0,
            "top_gap_ratio": 0.15,
            "log_decisions": True,
        },
        "data": {"markdown_dir": "data/merged_md"},
    }
    agent.context_manager = ContextManager(max_chars=99999, max_doc_chars=16000)
    agent.retrieval_enabled = True
    agent.retriever = MagicMock()
    # 高置信度：max=200，gap 大
    agent.retriever.retrieve.return_value = (
        fake_evidence,
        {
            "retrieval_method": "bm25_window",
            "retrieved_windows": 3,
            "max_bm25_score": 200.0,
            "top1_score": 200.0,
            "top2_score": 100.0,  # gap=0.5 > 0.15
        },
    )
    agent.prompt_builder = MagicMock()
    agent.prompt_builder.build_prompt.return_value = "FIRST_PROMPT"
    agent.reflection_prompt_builder = ReflectionPromptBuilder()
    agent.memory = MagicMock()

    first_resp = MagicMock(content="A", finish_reason="stop")
    first_resp.usage = MagicMock(
        prompt_tokens=100, completion_tokens=10, total_tokens=110,
    )
    agent.llm = MagicMock()
    agent.llm.chat.return_value = first_resp

    agent._load_evidence = MagicMock(return_value=fake_evidence)

    answer, evidence, token_usage = agent.answer_question(fake_question)

    assert answer == "A"
    assert token_usage["reflected"] is False
    assert agent.llm.chat.call_count == 1  # 没调反思


def test_answer_question_reflection_parse_fail_keeps_first_answer(
    fake_question, fake_evidence,
):
    """反思输出无法解析时应保留首轮答案（fail-safe）。"""
    agent = FinancialQAAgent.__new__(FinancialQAAgent)
    agent.config = {
        "model": {"max_context_tokens": 80000},
        "retrieval": {"enabled": True, "max_doc_chars": 16000, "method": "bm25"},
        "reflection": {
            "enabled": True,
            "low_score_threshold": 80.0,
            "top_gap_ratio": 0.15,
            "log_decisions": True,
        },
        "data": {"markdown_dir": "data/merged_md"},
    }
    agent.context_manager = ContextManager(max_chars=99999, max_doc_chars=16000)
    agent.retrieval_enabled = True
    agent.retriever = MagicMock()
    agent.retriever.retrieve.return_value = (
        fake_evidence,
        {
            "retrieval_method": "bm25_window",
            "retrieved_windows": 2,
            "max_bm25_score": 50.0,  # 触发 low_score
            "top1_score": 50.0,
            "top2_score": 40.0,
        },
    )
    agent.prompt_builder = MagicMock()
    agent.prompt_builder.build_prompt.return_value = "FIRST_PROMPT"
    agent.reflection_prompt_builder = ReflectionPromptBuilder()
    agent.memory = MagicMock()

    first_resp = MagicMock(content="A", finish_reason="stop")
    first_resp.usage = MagicMock(
        prompt_tokens=100, completion_tokens=10, total_tokens=110,
    )
    reflect_resp = MagicMock(
        content="我觉得选项 A 是对的", finish_reason="stop",  # 无法解析
    )
    reflect_resp.usage = MagicMock(
        prompt_tokens=120, completion_tokens=30, total_tokens=150,
    )
    agent.llm = MagicMock()
    agent.llm.chat.side_effect = [first_resp, reflect_resp]

    agent._load_evidence = MagicMock(return_value=fake_evidence)

    answer, _, token_usage = agent.answer_question(fake_question)

    assert answer == "A"  # fail-safe 保留首轮
    assert token_usage["reflection_decision"] == "PARSE_FAIL"
    assert token_usage["reflected"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_reflection.py -k answer_question -v`
Expected: FAIL — `AttributeError: 'FinancialQAAgent' object has no attribute 'reflection_prompt_builder'`

- [ ] **Step 3: 在 `FinancialQAAgent.__init__` 中初始化 reflection 相关字段**

Locate the `__init__` method of `FinancialQAAgent` (around line 787). After `self.prompt_builder = PromptBuilder()` (line 800), insert:

```python
        self.reflection_prompt_builder = ReflectionPromptBuilder()
        reflection_cfg = self.config.get("reflection", {}) or {}
        self.reflection_enabled = bool(reflection_cfg.get("enabled", False))
        self.reflection_config = {
            "enabled": self.reflection_enabled,
            "low_score_threshold": float(reflection_cfg.get("low_score_threshold", 80.0)),
            "top_gap_ratio": float(reflection_cfg.get("top_gap_ratio", 0.15)),
            "log_decisions": bool(reflection_cfg.get("log_decisions", True)),
        }
```

- [ ] **Step 4: 在 `answer_question` 末尾追加反思分支**

Locate the end of `answer_question` (around line 980, after `token_usage = {...}` and before `return answer, evidence, token_usage`). Replace the final block with the following — note the new variables `first_answer`, `reflected`, `reflection_decision`, `reflection_trigger_reason`:

Original:
```python
        # 4. 从内容中提取答案
        answer = self._parse_answer(response, question.get("answer_format", "mcq"))

        token_usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "retries": retry_count,
            **retrieval_stats,
        }

        return answer, evidence, token_usage
```

Replacement:
```python
        # 4. 从内容中提取答案
        first_answer = self._parse_answer(response, question.get("answer_format", "mcq"))
        answer = first_answer

        reflected = False
        reflection_decision = ""
        reflection_trigger_reason = ""
        total_prompt = response.usage.prompt_tokens
        total_completion = response.usage.completion_tokens
        total_tokens = response.usage.total_tokens

        # 5. 反思环节：BM25 低置信度时让 LLM 自评修正
        if self.reflection_enabled:
            triggered, reason = should_reflect(retrieval_stats, self.reflection_config)
            reflection_trigger_reason = reason
            if triggered:
                reflect_prompt = self.reflection_prompt_builder.build_prompt(
                    question, evidence, first_answer, local_context,
                )
                reflect_prompt = local_context.truncate(reflect_prompt)
                reflect_response = self.llm.chat(
                    [{"role": "user", "content": reflect_prompt}], max_tokens=4096,
                )
                decision, parsed_answer = _parse_reflection_decision(
                    reflect_response.content, first_answer,
                    question.get("answer_format", "mcq"),
                )
                reflected = True
                reflection_decision = decision
                if decision != "PARSE_FAIL":
                    answer = parsed_answer
                # PARSE_FAIL 时保留首轮 answer（fail-safe）

                total_prompt += reflect_response.usage.prompt_tokens
                total_completion += reflect_response.usage.completion_tokens
                total_tokens += reflect_response.usage.total_tokens

        token_usage = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "retries": retry_count,
            "reflected": reflected,
            "first_answer": first_answer,
            "reflection_decision": reflection_decision,
            "reflection_trigger_reason": reflection_trigger_reason,
            **retrieval_stats,
        }

        return answer, evidence, token_usage
```

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/test_reflection.py -k answer_question -v`
Expected: PASS（3 个集成测试全部通过）

- [ ] **Step 6: 运行全部反思测试，确保没有回归**

Run: `pytest tests/test_reflection.py -v`
Expected: PASS（所有反思相关测试）

- [ ] **Step 7: 运行全部测试套件**

Run: `pytest tests/ -v`
Expected: PASS（包括已有的 test_retrieval_logger、test_smoke 等）

- [ ] **Step 8: Commit**

```bash
git add src/agent/agent.py tests/test_reflection.py
git commit -m "feat(agent): wire reflection step into answer_question pipeline"
```

---

## Task 7: 在 diagnostics CSV 中暴露反思字段

**Files:**
- Modify: `src/agent/run.py` 中 `generate_diagnostics_csv` 函数

- [ ] **Step 1: 查看当前 diagnostics CSV 字段**

Run: `head -1 output/diagnostics_a_glm51_full.csv`
Expected: 显示当前 CSV 列名（包含 qid、domain、answer_format、...、retries、random_fill、error）

- [ ] **Step 2: 修改 `generate_diagnostics_csv` 加列**

Locate `generate_diagnostics_csv` in `src/agent/run.py` (around line 189). Modify the `fields` list — insert the four new fields right after `"retries"`:

Original (around line 191-214):
```python
    fields = [
        "qid",
        "domain",
        "answer_format",
        "doc_ids",
        "answer",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "retrieval_method",
        "query_count",
        "chunk_count",
        "candidate_count",
        "retrieved_windows",
        "retrieved_chars",
        "doc_coverage",
        "max_bm25_score",
        "avg_bm25_score",
        "selected_sources",
        "retrieval_doc_stats",
        "retries",
        "random_fill",
        "error",
    ]
```

Replacement:
```python
    fields = [
        "qid",
        "domain",
        "answer_format",
        "doc_ids",
        "answer",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "retrieval_method",
        "query_count",
        "chunk_count",
        "candidate_count",
        "retrieved_windows",
        "retrieved_chars",
        "doc_coverage",
        "max_bm25_score",
        "avg_bm25_score",
        "top1_score",
        "top2_score",
        "selected_sources",
        "retrieval_doc_stats",
        "reflected",
        "first_answer",
        "reflection_decision",
        "reflection_trigger_reason",
        "retries",
        "random_fill",
        "error",
    ]
```

- [ ] **Step 3: 在 `writerow` 调用中加入新字段**

Locate the `writer.writerow({...})` call inside the loop (around line 220-244). Add the new fields after `"retrieval_doc_stats":`:

Original tail:
```python
                "retrieval_doc_stats": json.dumps(r.get("retrieval_doc_stats", {}), ensure_ascii=False),
                "retries": r.get("retries", ""),
                "random_fill": r.get("random_fill", ""),
                "error": r.get("error", ""),
            })
```

Replacement:
```python
                "retrieval_doc_stats": json.dumps(r.get("retrieval_doc_stats", {}), ensure_ascii=False),
                "top1_score": r.get("top1_score", ""),
                "top2_score": r.get("top2_score", ""),
                "reflected": r.get("reflected", ""),
                "first_answer": r.get("first_answer", ""),
                "reflection_decision": r.get("reflection_decision", ""),
                "reflection_trigger_reason": r.get("reflection_trigger_reason", ""),
                "retries": r.get("retries", ""),
                "random_fill": r.get("random_fill", ""),
                "error": r.get("error", ""),
            })
```

- [ ] **Step 4: 运行 run.py 端到端验证 CSV 输出格式**

Run a tiny end-to-end smoke test (only 2 questions, won't burn much token):

```bash
cd F:/private/afac2026-financial-qa
PYTHONPATH=. python -m src.agent.run --split A --limit 2 --tag reflection_smoke --workers 2
```

Expected: 无异常，`output/diagnostics_a_reflection_smoke.csv` 的 header 包含 `top1_score,top2_score,reflected,first_answer,reflection_decision,reflection_trigger_reason`。

- [ ] **Step 5: 检查 CSV header**

Run: `head -1 output/diagnostics_a_reflection_smoke.csv`
Expected: header 行包含所有新增字段

- [ ] **Step 6: Commit**

```bash
git add src/agent/run.py
git commit -m "feat(run): expose reflection fields in diagnostics csv"
```

---

## Task 8: 全量回归与人工抽检

**Files:** 无（运行 + 人工核对）

- [ ] **Step 1: 跑全量 A 组（反思开启）**

Run:
```bash
cd F:/private/afac2026-financial-qa
PYTHONPATH=. python -m src.agent.run --split A --tag reflection_on --workers 8
```

Expected: 完成 100 题，输出 `output/results_a_reflection_on.json` 和 `output/diagnostics_a_reflection_on.csv`。

- [ ] **Step 2: 跑全量 A 组（反思关闭，对照组）**

临时改 `config/config.yaml`：
```yaml
reflection:
  enabled: false
  ...
```

Run:
```bash
PYTHONPATH=. python -m src.agent.run --split A --tag reflection_off --workers 8
```

Expected: 完成后改回 `enabled: true`。

- [ ] **Step 3: 对比两组 token 与答案差异**

Run:
```bash
python -c "
import csv
def load(tag):
    with open(f'output/diagnostics_a_{tag}.csv', encoding='utf-8') as f:
        return {r['qid']: r for r in csv.DictReader(f)}
on = load('reflection_on')
off = load('reflection_off')
total_on = sum(int(r['total_tokens']) for r in on.values())
total_off = sum(int(r['total_tokens']) for r in off.values())
diff = [q for q in on if on[q]['answer'] != off[q]['answer']]
reflected = [q for q in on if on[q]['reflected'] == 'True']
print(f'Total tokens on={total_on}, off={total_off}, growth={total_on-total_off}')
print(f'Reflected: {len(reflected)}/100')
print(f'Answer changed: {len(diff)}')
for q in diff[:20]:
    print(f'  {q}: {off[q][\"answer\"]} -> {on[q][\"answer\"]} (reason={on[q][\"reflection_trigger_reason\"]})')
"
```

Expected: 输出 token 增长、反思命中数、答案变更数及前 20 个变更案例。

- [ ] **Step 4: 抽样人工核对 10 道触发反思的题**

从 `output/diagnostics_a_reflection_on.csv` 筛选 `reflected == True` 的题目，挑 10 道（覆盖不同 domain 和 answer_format），对照 `data/raw_dataset/questions/group_a/*_questions.json` 中的 `answer` 字段，统计：
- 反思后正确的数量
- 反思前正确但反思后错误的数量（**关注这个，是反思机制的风险点**）

- [ ] **Step 5: 决策——保留、调整阈值、或关闭**

根据 Step 3-4 的结果：
- 若反思后整体准确率提升 → 保留 `enabled: true`，进入下一阶段
- 若 token 涨幅 > 50% 但准确率提升不明显 → 调高 `low_score_threshold`（如改为 100 或 120），让触发更严格
- 若反思后准确率下降 → 设 `enabled: false` 回退，并记录到 spec/复盘文档

- [ ] **Step 6: Commit 最终结果与配置**

```bash
git add config/config.yaml
git commit -m "chore(config): tune reflection threshold based on regression result"
```

---

## Notes for the executor

- **测试中不要真实调用 LLM**：所有 LLM 调用都走 `MagicMock`，避免烧 token 和触发网络错误
- **import 顺序**：Task 4 的 `ReflectionPromptBuilder` 和 Task 3 的 `should_reflect` 必须在 Task 6 集成前完成，因为 `FinancialQAAgent.__init__` 会引用它们
- **运行测试的 Python 环境**：项目根目录有 `.venv`，确保 `pytest` 命令在对应环境内运行
- **diagnostics CSV 新增字段不会破坏旧 CSV**：旧文件没有这些列，新文件追加，CSV reader 用 `DictReader` 自动处理缺失列
- **Task 8 Step 2 的临时配置改动**：完成对照组跑批后务必把 `enabled` 改回 `true`，否则后续提交会跑回 baseline
