"""Service helpers for the local BM25 debug UI."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List, Optional

from src.agent.agent import BM25Retriever, Evidence, FinancialQAAgent
from src.utils.helpers import load_config


FLOAT_PARAM_KEYS = {"bm25_k1", "bm25_b", "min_score"}
INT_PARAM_KEYS = {
    "chunk_size_chars",
    "chunk_overlap_chars",
    "min_chunk_chars",
    "expand_before_chars",
    "expand_after_chars",
    "merge_gap_chars",
    "per_doc_min",
    "per_doc_max",
    "global_top_k",
    "max_total_chars",
    "max_query_terms",
}
ALLOWED_PARAM_KEYS = FLOAT_PARAM_KEYS | INT_PARAM_KEYS


def validate_question(question: object) -> Dict[str, Any]:
    if not isinstance(question, dict):
        raise ValueError("question 必须是对象")
    if not question.get("domain"):
        raise ValueError("question.domain 不能为空")
    if not question.get("question"):
        raise ValueError("question.question 不能为空")
    doc_ids = question.get("doc_ids")
    if not isinstance(doc_ids, list) or not doc_ids:
        raise ValueError("question.doc_ids 不能为空")
    return question


def build_retrieval_config(
    base_config: Dict[str, Any],
    params: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    retrieval_cfg = deepcopy(base_config.get("retrieval", {}))
    params = params or {}
    for key, value in params.items():
        if key in FLOAT_PARAM_KEYS:
            retrieval_cfg[key] = float(value)
        elif key in INT_PARAM_KEYS:
            retrieval_cfg[key] = int(value)
    retrieval_cfg["logging"] = {"log_retrieval": False}
    return retrieval_cfg


def serialize_evidence(evidence: Evidence) -> Dict[str, Any]:
    return {
        "doc_id": evidence.doc_id,
        "source": evidence.source,
        "relevance_score": evidence.relevance_score,
        "content": evidence.content,
    }


def serialize_debug_response(
    question: Dict[str, Any],
    retrieved: List[Evidence],
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    chunks = []
    selected_sources = stats.get("selected_sources", [])
    source_by_key = {
        (str(item.get("doc_id")), int(item.get("start", 0)), int(item.get("end", 0))): item
        for item in selected_sources
    }
    for evidence in retrieved:
        for key, source in source_by_key.items():
            if key[0] != str(evidence.doc_id):
                continue
            marker = f"位置 {key[1]}-{key[2]}"
            if marker not in evidence.content:
                continue
            chunks.append(
                {
                    "doc_id": evidence.doc_id,
                    "source": evidence.source,
                    "start": source.get("start", 0),
                    "end": source.get("end", 0),
                    "score": source.get("score", 0.0),
                    "query_types": source.get("query_types", []),
                    "text": evidence.content,
                }
            )
            break
    return {
        "ok": True,
        "question": {
            "qid": question.get("qid", ""),
            "domain": question.get("domain", ""),
            "answer_format": question.get("answer_format", ""),
            "doc_ids": question.get("doc_ids", []),
            "question": question.get("question", ""),
            "options": question.get("options", {}),
        },
        "stats": stats,
        "chunks": chunks,
        "retrieved": [serialize_evidence(item) for item in retrieved],
    }


def _build_agent_without_llm(base_config: Dict[str, Any]) -> FinancialQAAgent:
    """Construct a FinancialQAAgent shell that can run _load_evidence without an LLM client.

    The debug UI does not call the LLM, so we bypass __init__ (which would require API keys)
    and only wire up the fields _load_evidence touches.
    """
    agent = FinancialQAAgent.__new__(FinancialQAAgent)
    agent.config = base_config
    return agent


def run_debug_search(
    question: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    question = validate_question(question)
    base_config = deepcopy(config or load_config())
    retrieval_cfg = build_retrieval_config(base_config, params)
    agent = _build_agent_without_llm(base_config)
    evidence = agent._load_evidence(question)
    retriever = BM25Retriever(retrieval_cfg)
    retrieved, stats = retriever.retrieve(question, evidence)
    return serialize_debug_response(question, retrieved, stats)
