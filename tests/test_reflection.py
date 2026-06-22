"""反思机制单元测试。"""
import pytest
from unittest.mock import MagicMock

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
    agent.reflection_enabled = True
    agent.reflection_config = {
        "enabled": True,
        "low_score_threshold": 80.0,
        "top_gap_ratio": 0.15,
        "log_decisions": True,
    }
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
    agent.reflection_enabled = True
    agent.reflection_config = {
        "enabled": True,
        "low_score_threshold": 80.0,
        "top_gap_ratio": 0.15,
        "log_decisions": True,
    }
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
    agent.reflection_enabled = True
    agent.reflection_config = {
        "enabled": True,
        "low_score_threshold": 80.0,
        "top_gap_ratio": 0.15,
        "log_decisions": True,
    }
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
