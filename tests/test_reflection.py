"""反思机制单元测试。"""
import pytest

from src.agent.agent import (
    BM25Retriever,
    Evidence,
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
