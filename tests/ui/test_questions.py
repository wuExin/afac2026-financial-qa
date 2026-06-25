from pathlib import Path

from ui.data_index import DOMAINS
from ui.questions import Question, load_questions

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def test_load_questions_first_financial_contract():
    qs = load_questions(DATA_ROOT, "financial_contracts")
    assert len(qs) > 0
    q = qs[0]
    assert isinstance(q, Question)
    assert q.qid == "fc_a_001"
    assert q.doc_ids == ("text01", "text02")
    assert set("ABCD") <= set(q.options.keys())
    assert q.question


def test_load_questions_merges_bom_encoded_answer():
    qs = load_questions(DATA_ROOT, "financial_contracts")
    # 答案取自带 BOM 的 group_a_answers.json,需 utf-8-sig 才能读到
    assert qs[0].answer == "ABD"


def test_load_questions_unknown_domain_returns_empty():
    assert load_questions(DATA_ROOT, "nonexistent") == []


def test_all_domains_loadable():
    for domain in DOMAINS:
        qs = load_questions(DATA_ROOT, domain)
        assert isinstance(qs, list)
        assert len(qs) > 0
