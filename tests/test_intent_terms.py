from types import SimpleNamespace

import src.agent.agent as agent_module
from src.agent.agent import BM25Retriever, FinancialQAAgent, IntentTermSelector


def test_build_queries_uses_selected_intent_terms():
    retriever = BM25Retriever({"logging": {"log_retrieval": False}})
    question = {
        "qid": "fr_001",
        "domain": "financial_reports",
        "answer_format": "mcq",
        "question": "公司研发投入占营业收入比例是多少？",
        "options": {"A": "1.2%", "B": "2.3%"},
        "_intent_terms": ["研发投入", "研发费用", "营业收入"],
    }

    queries = retriever._build_queries(question)

    intent_queries = [item for item in queries if item["query_type"] == "intent_terms"]
    assert len(intent_queries) == 1
    assert "研发投入" in intent_queries[0]["tokens"]
    assert "研发费用" in intent_queries[0]["tokens"]
    assert "营业收入" in intent_queries[0]["tokens"]


def test_intent_selector_filters_llm_output_to_candidate_terms():
    selector = IntentTermSelector(enabled=True, max_terms=5)
    llm = SimpleNamespace(
        chat=lambda messages, max_tokens, temperature: SimpleNamespace(
            content='{"terms": ["研发费用", "不存在的词", "营业收入", "研发费用"]}'
        )
    )
    question = {
        "domain": "financial_reports",
        "question": "研发费用和营收有什么关系？",
        "options": {"A": "A", "B": "B"},
    }

    terms = selector.select(question, llm, BM25Retriever.INTENT_TERMS)

    assert terms == ["研发费用", "营业收入"]


def test_agent_adds_intent_terms_before_retrieval():
    class FakeSelector:
        def select(self, question, llm, intent_terms):
            assert question["qid"] == "q1"
            assert "financial_reports" in intent_terms
            return ["研发投入", "营业收入"]

    class FakeRetriever:
        def retrieve(self, question, evidence):
            assert question["_intent_terms"] == ["研发投入", "营业收入"]
            return evidence, {
                "retrieval_method": "bm25_window",
                "query_count": 1,
                "chunk_count": 1,
                "candidate_count": 1,
                "retrieved_windows": 1,
                "retrieved_chars": 20,
                "doc_coverage": 1,
                "max_bm25_score": 1.0,
                "avg_bm25_score": 1.0,
                "selected_sources": [],
                "retrieval_doc_stats": {},
            }

    agent = FinancialQAAgent.__new__(FinancialQAAgent)
    agent.retrieval_enabled = True
    agent.intent_selector = FakeSelector()
    agent.retriever = FakeRetriever()
    agent.llm = object()

    question = {"qid": "q1", "domain": "financial_reports"}
    evidence = []

    enriched_question, _, stats = agent._retrieve_evidence_with_intent_terms(question, evidence)

    assert question.get("_intent_terms") is None
    assert enriched_question["_intent_terms"] == ["研发投入", "营业收入"]
    assert stats["intent_terms"] == ["研发投入", "营业收入"]


def test_agent_can_configure_dedicated_intent_llm(monkeypatch):
    class FakeLLM:
        def __init__(self, api_key, base_url, model, temperature, fallback_model):
            self.api_key = api_key
            self.base_url = base_url
            self.model = model
            self.temperature = temperature
            self.fallback_model = fallback_model

    monkeypatch.setattr(agent_module, "LLMClient", FakeLLM)
    monkeypatch.setenv("GLM_API_KEY", "test-key")
    config = {
        "model": {
            "name": "glm-4.7",
            "api_base": "https://open.bigmodel.cn/api/coding/paas/v4",
            "temperature": 0.0,
            "fallback_name": "",
            "max_context_tokens": 80000,
        },
        "retrieval": {
            "enabled": True,
            "method": "bm25",
            "max_doc_chars": 45000,
            "intent_terms": {
                "enabled": True,
                "max_terms": 8,
                "model": "glm-4.7-flash",
                "api_base": "https://open.bigmodel.cn/api/coding/paas/v4",
                "temperature": 0.0,
            },
        },
        "data": {"markdown_dir": "data/merged_md"},
        "reflection": {"enabled": False},
    }

    agent = FinancialQAAgent(config)

    assert agent.llm.model == "glm-4.7"
    assert agent.llm.base_url == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert agent.intent_llm.model == "glm-4.7-flash"
    assert agent.intent_llm.base_url == "https://open.bigmodel.cn/api/coding/paas/v4"
    assert agent.intent_llm is not agent.llm
