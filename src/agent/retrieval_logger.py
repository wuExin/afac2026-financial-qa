"""BM25 检索结果落盘：把每题的检索上下文（含 chunk 文本）写到 logs/<qid>.json。

用于人工核对检索质量，定位"BM25 搜出来的对不对"。
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List


QUESTION_FIELDS_WHITELIST = (
    "qid",
    "domain",
    "split",
    "question",
    "options",
    "answer_format",
    "type",
    "doc_ids",
)


class RetrievalLogger:
    """把 BM25 检索结果（含 chunk 文本）落盘到 logs/<qid>.json。

    设计原则：
    - 单一职责：只负责把检索结果写到磁盘，不参与检索逻辑
    - 失败静默：任何 IO/序列化错误都不影响主流程
    - 覆盖写：每次运行覆盖旧文件，确保看到的是最近一次结果
    """

    def __init__(self, log_dir: str = "logs", enabled: bool = True):
        self.log_dir = Path(log_dir)
        self.enabled = enabled

    def dump(
        self,
        qid: str,
        question: Dict,
        queries: List[str],
        chunks: List[Dict],
        stats: Dict,
    ) -> None:
        """写 logs/<qid>.json。失败时打印 stderr 但不抛出。"""
        if not self.enabled:
            return
        if not qid:
            print("[RetrievalLogger] 跳过：qid 为空", file=sys.stderr)
            return
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            payload = self._build_payload(qid, question, queries, chunks, stats)
            out_path = self.log_dir / f"{qid}.json"
            out_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            print(f"[RetrievalLogger] 写 {qid} 失败: {e}", file=sys.stderr)

    def _build_payload(
        self,
        qid: str,
        question: Dict,
        queries: List[str],
        chunks: List[Dict],
        stats: Dict,
    ) -> Dict:
        return {
            "qid": qid,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "domain": question.get("domain", ""),
            "question_meta": {
                k: question[k]
                for k in QUESTION_FIELDS_WHITELIST
                if k in question
            },
            "question_text": question.get("question", ""),
            "options": question.get("options", []),
            "answer_format": question.get("answer_format", ""),
            "doc_ids": question.get("doc_ids", []),
            "queries": list(queries),
            "stats": stats,
            "chunks": [self._serialize_chunk(c) for c in chunks],
        }

    @staticmethod
    def _serialize_chunk(chunk: Dict) -> Dict:
        return {
            "doc_id": chunk.get("doc_id", ""),
            "start": chunk.get("start", 0),
            "end": chunk.get("end", 0),
            "score": round(float(chunk.get("score", 0.0)), 4),
            "query_types": sorted(chunk.get("query_types", []) or []),
            "text": chunk.get("content", ""),
        }
