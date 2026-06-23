"""Service helpers for the local BM25 debug UI."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.agent.agent import BM25Retriever, Evidence, FinancialQAAgent, IntentTermSelector
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
STEP_KEYS = ("intent", "bm25", "answer")


def normalize_steps(steps: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    steps = steps or {}
    return {key: bool(steps.get(key, True)) for key in STEP_KEYS}


def _public_question(question: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "qid": question.get("qid", ""),
        "domain": question.get("domain", ""),
        "answer_format": question.get("answer_format", ""),
        "question": question.get("question", ""),
        "options": question.get("options", {}),
        "doc_ids": question.get("doc_ids", []),
        "split": question.get("split", ""),
    }


def load_debug_questions(config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base_config = config or load_config()
    questions_dir = Path(base_config["data"]["questions_dir"])
    questions: List[Dict[str, Any]] = []
    counts = {"mcq": 0, "multi": 0, "tf": 0}

    for path in sorted(questions_dir.glob("*_questions.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            continue
        for item in data:
            if not isinstance(item, dict):
                continue
            answer_format = item.get("answer_format")
            if answer_format not in counts:
                continue
            questions.append(_public_question(item))
            counts[answer_format] += 1

    questions.sort(key=lambda item: str(item.get("qid", "")))
    return {"ok": True, "questions": questions, "stats": {"total": len(questions), **counts}}


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


def build_debug_config(
    base_config: Dict[str, Any],
    params: Optional[Dict[str, Any]],
    steps: Optional[Dict[str, bool]],
) -> Dict[str, Any]:
    debug_config = deepcopy(base_config)
    retrieval_cfg = build_retrieval_config(debug_config, params)
    step_flags = normalize_steps(steps)
    intent_cfg = dict(retrieval_cfg.get("intent_terms", {}) or {})
    intent_cfg["enabled"] = bool(intent_cfg.get("enabled", False)) and step_flags["intent"]
    retrieval_cfg["intent_terms"] = intent_cfg
    debug_config["retrieval"] = retrieval_cfg
    return debug_config


def _serialize_usage(usage: Any) -> Dict[str, int]:
    if not usage:
        return {}
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0)),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0)),
        "total_tokens": int(getattr(usage, "total_tokens", 0)),
    }


def run_intent_selection(agent: FinancialQAAgent, question: Dict[str, Any]) -> Dict[str, Any]:
    active_question = dict(question)
    selector = getattr(agent, "intent_selector", IntentTermSelector(enabled=False))
    terms = selector.select(
        active_question,
        getattr(agent, "intent_llm", agent.llm),
        BM25Retriever.INTENT_TERMS,
    )
    if terms:
        active_question["_intent_terms"] = terms
    return {
        "enabled": True,
        "terms": terms,
        "token_usage": _serialize_usage(getattr(selector, "last_usage", None)),
        "question": active_question,
    }


def _serialize_answer_result(answer: str, token_usage: Dict[str, Any]) -> Dict[str, Any]:
    result = {
        "enabled": True,
        "answer": answer,
    }
    result.update(token_usage or {})
    return result


def run_question_flow(
    question: Dict[str, Any],
    steps: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    question = validate_question(question)
    step_flags = normalize_steps(steps)
    base_config = deepcopy(config or load_config())
    debug_config = build_debug_config(base_config, params, step_flags)
    response: Dict[str, Any] = {
        "ok": True,
        "question": _public_question(question),
        "steps": step_flags,
        "intent": {"enabled": False, "terms": [], "token_usage": {}},
        "bm25": {"enabled": False},
        "answer": {"enabled": False},
    }

    active_question = dict(question)

    if step_flags["intent"]:
        agent = FinancialQAAgent(debug_config)
        intent_result = run_intent_selection(agent, active_question)
        active_question = intent_result.pop("question")
        response["intent"] = intent_result

    if step_flags["bm25"]:
        bm25_payload = run_debug_search(active_question, params, debug_config)
        response["bm25"] = {
            "enabled": True,
            "stats": bm25_payload.get("stats", {}),
            "chunks": bm25_payload.get("chunks", []),
            "retrieved": bm25_payload.get("retrieved", []),
        }

    if step_flags["answer"]:
        agent = FinancialQAAgent(debug_config)
        answer, _evidence, token_usage = agent.answer_question(question)
        response["answer"] = _serialize_answer_result(answer, token_usage)

    return response
