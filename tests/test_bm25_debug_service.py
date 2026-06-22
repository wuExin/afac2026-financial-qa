import pytest

from src.agent.bm25_debug_service import build_retrieval_config, validate_question


def test_validate_question_requires_doc_ids():
    question = {
        "qid": "q1",
        "domain": "financial_reports",
        "question": "题目",
        "answer_format": "mcq",
        "doc_ids": [],
    }

    with pytest.raises(ValueError, match="question.doc_ids 不能为空"):
        validate_question(question)


def test_validate_question_accepts_minimal_single_question():
    question = {
        "qid": "q1",
        "domain": "financial_reports",
        "question": "题目",
        "options": {"A": "是", "B": "否"},
        "answer_format": "mcq",
        "doc_ids": ["doc1"],
    }

    assert validate_question(question) is question


def test_build_retrieval_config_applies_allowed_numeric_overrides():
    config = {
        "retrieval": {
            "enabled": True,
            "method": "bm25",
            "bm25_k1": 1.5,
            "chunk_size_chars": {"default": 1400, "financial_reports": 2200},
            "global_top_k": {"mcq": 6, "multi": 10},
        }
    }

    result = build_retrieval_config(
        config,
        {
            "bm25_k1": "2.0",
            "chunk_size_chars": "500",
            "global_top_k": "3",
            "ignored": "999",
        },
    )

    assert result["bm25_k1"] == 2.0
    assert result["chunk_size_chars"] == 500
    assert result["global_top_k"] == 3
    assert "ignored" not in result
    assert result["logging"]["log_retrieval"] is False


from src.agent.bm25_debug_service import run_debug_search


def test_run_debug_search_returns_retrieval_stats(monkeypatch):
    from src.agent.agent import Evidence, FinancialQAAgent

    def fake_load_evidence(self, question):
        return [
            Evidence(
                doc_id="doc1",
                source="financial_reports/doc1",
                content=(
                    "公司2023年年度报告。\n"
                    "主要会计数据：营业收入100亿元，净利润8亿元，研发投入5亿元。"
                ),
            )
        ]

    monkeypatch.setattr(FinancialQAAgent, "_load_evidence", fake_load_evidence)
    question = {
        "qid": "q1",
        "domain": "financial_reports",
        "question": "公司2023年的营业收入和净利润是多少？",
        "options": {"A": "营业收入100亿元，净利润8亿元", "B": "未披露"},
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
        },
        "data": {},
        "model": {},
    }

    result = run_debug_search(question, {"global_top_k": 1}, config)

    assert result["ok"] is True
    assert result["stats"]["retrieval_method"] == "bm25_window"
    assert result["stats"]["retrieved_windows"] >= 1
    assert result["stats"]["max_bm25_score"] > 0
    assert result["retrieved"][0]["doc_id"] == "doc1"
