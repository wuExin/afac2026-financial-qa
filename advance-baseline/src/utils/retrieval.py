"""BM25 检索引擎"""
import jieba
from rank_bm25 import BM25Okapi
from typing import List


class BM25Retriever:
    def _tokenize(self, text: str) -> List[str]:
        return list(jieba.cut(text))

    def _chunk_document(self, text: str, chunk_size: int = 500) -> List[str]:
        sentences = text.split("\n")
        chunks = []
        current = ""
        for s in sentences:
            if len(current) + len(s) < chunk_size:
                current += s + "\n"
            else:
                if current:
                    chunks.append(current.strip())
                current = s + "\n"
        if current:
            chunks.append(current.strip())
        return chunks

    def search(self, query: str, doc_text: str, top_k: int = 5) -> List[str]:
        chunks = self._chunk_document(doc_text)
        if not chunks:
            return []
        tokenized = [self._tokenize(c) for c in chunks]
        bm25 = BM25Okapi(tokenized)
        scores = bm25.get_scores(self._tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        return [chunks[i] for i, s in ranked if s > 0]