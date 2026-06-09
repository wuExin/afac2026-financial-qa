"""数据加载模块"""
import json
from pathlib import Path
from typing import Dict, List, Any


def load_questions(config: dict) -> List[Dict[str, Any]]:
    data_dir = Path(config.get("paths", {}).get("raw_dataset", "data/raw_dataset"))
    questions_dir = data_dir / "questions" / "group_a"
    all_questions = []
    if questions_dir.exists():
        for json_file in sorted(questions_dir.glob("*.json")):
            with open(json_file, "r", encoding="utf-8") as f:
                questions = json.load(f)
                if isinstance(questions, list):
                    all_questions.extend(questions)
                elif isinstance(questions, dict):
                    all_questions.append(questions)
    return all_questions


def load_documents(config: dict) -> Dict[str, str]:
    processed_dir = Path(config.get("paths", {}).get("processed_docs", "data/processed_pymupdf4llm"))
    doc_registry = {}
    if not processed_dir.exists():
        return doc_registry
    for domain_dir in processed_dir.iterdir():
        if not domain_dir.is_dir():
            continue
        for doc_dir in domain_dir.iterdir():
            if not doc_dir.is_dir():
                continue
            doc_id = doc_dir.name
            pages = []
            for page_file in sorted(doc_dir.glob("page_*.md")):
                try:
                    with open(page_file, "r", encoding="utf-8") as f:
                        pages.append(f.read())
                except Exception:
                    continue
            if pages:
                doc_registry[doc_id] = "\n\n".join(pages)
    return doc_registry