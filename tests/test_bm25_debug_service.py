import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.agent.bm25_debug_service import (
    build_retrieval_config,
    load_debug_questions,
    normalize_steps,
    run_intent_selection,
    run_question_flow,
    validate_question,
)


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


def test_normalize_steps_defaults_all_enabled():
    assert normalize_steps(None) == {"intent": True, "bm25": True, "answer": True}


def test_normalize_steps_accepts_false_values():
    assert normalize_steps({"intent": False, "bm25": True, "answer": False}) == {
        "intent": False,
        "bm25": True,
        "answer": False,
    }


def test_load_debug_questions_includes_all_supported_answer_formats(tmp_path):
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
    assert [item["qid"] for item in result["questions"]] == ["q1", "q2", "q3"]
    assert result["stats"] == {"total": 3, "mcq": 1, "multi": 1, "tf": 1}


def test_run_intent_selection_defaults_to_disabled_selector_without_error():
    agent = SimpleNamespace(llm=object())
    question = {
        "qid": "q1",
        "domain": "financial_reports",
        "question": "营业收入是多少？",
        "options": {"A": "100 亿元"},
        "answer_format": "mcq",
        "doc_ids": ["doc1"],
    }

    result = run_intent_selection(agent, question)

    assert result["enabled"] is True
    assert result["terms"] == []
    assert result["question"] == question


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

    monkeypatch.setattr(
        FinancialQAAgent,
        "_create_llm_client",
        classmethod(lambda cls, model_cfg, fallback_env="FALLBACK_MODEL_NAME": object()),
    )
    monkeypatch.setattr(
        FinancialQAAgent,
        "_create_intent_llm_client",
        classmethod(lambda cls, intent_cfg, default_llm: object()),
    )

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
