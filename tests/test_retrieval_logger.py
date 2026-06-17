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
    assert payload["chunks"][0]["score"] == 808.0685


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
    assert payload["question_meta"]["doc_ids"] == ["strict_v3_008_xxx", "strict_v3_017_yyy"]


def test_dump_silenced_on_io_error(tmp_path, basic_question, basic_chunks, basic_stats, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    logger = RetrievalLogger(log_dir=str(blocker), enabled=True)
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
