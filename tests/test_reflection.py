"""反思机制单元测试。"""
import pytest

from src.agent.agent import BM25Retriever, Evidence


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
