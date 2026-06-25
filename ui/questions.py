"""加载题目并合并答案,返回有序 Question 列表。纯逻辑,可单测。"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Question:
    qid: str
    question: str
    options: dict[str, str]
    qtype: str
    doc_ids: tuple[str, ...]
    answer: str | None


def questions_path(data_root: Path, domain: str) -> Path:
    return Path(data_root) / "questions" / "group_a" / f"{domain}_questions.json"


def answers_path(data_root: Path) -> Path:
    return Path(data_root) / "answers" / "group_a_answers.json"


def _load_answer_map(data_root: Path) -> dict[str, str]:
    path = answers_path(data_root)
    if not path.is_file():
        return {}
    # 答案文件带 UTF-8 BOM,必须用 utf-8-sig 读
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return {item["qid"]: item.get("answer") for item in data if item.get("qid")}


def load_questions(data_root: Path, domain: str) -> list[Question]:
    path = questions_path(data_root, domain)
    if not path.is_file():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    answers = _load_answer_map(data_root)
    return [
        Question(
            qid=item.get("qid", ""),
            question=item.get("question", ""),
            options=dict(item.get("options", {})),
            qtype=item.get("type", ""),
            doc_ids=tuple(item.get("doc_ids", [])),
            answer=answers.get(item.get("qid", "")),
        )
        for item in raw
    ]
