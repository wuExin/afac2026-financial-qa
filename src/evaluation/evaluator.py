"""
评测与提交生成模块。

职责：
1. 答案评测（准确率计算）
2. Token 消耗统计
3. 综合评分计算（FinalScore）
4. 生成符合赛题要求的提交文件（answer.json, evidence.json, CSV）
5. 分领域/分组别的分项统计
"""

from typing import Dict, List, Tuple
from dataclasses import dataclass
import json
import csv
from pathlib import Path


@dataclass
class EvalResult:
    """评测结果"""
    qid: str
    predicted: str
    ground_truth: str
    is_correct: bool
    domain: str
    split: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Evaluator:
    """评测器"""

    def __init__(self, token_budget: int = 5_000_000):
        self.token_budget = token_budget

    def compute_accuracy(self, results: List[EvalResult]) -> float:
        """计算准确率"""
        if not results:
            return 0.0
        correct = sum(1 for r in results if r.is_correct)
        return correct / len(results)

    def compute_token_score(self, total_tokens: int) -> float:
        """计算 Token 效率分"""
        return max(0.0, min(1.0, (self.token_budget - total_tokens) / self.token_budget))

    def compute_final_score(self, accuracy: float, total_tokens: int) -> float:
        """计算综合评分"""
        token_score = self.compute_token_score(total_tokens)
        return 100.0 * accuracy * (0.7 + 0.3 * token_score)

    def evaluate(
        self,
        predictions: Dict[str, str],
        ground_truth: Dict[str, Dict],
        token_stats: Dict[str, Dict[str, int]],
    ) -> List[EvalResult]:
        """逐题评测"""
        results = []
        for qid, pred in predictions.items():
            gt = ground_truth.get(qid, {})
            answer = gt.get("answer", "")
            is_correct = pred == answer
            stats = token_stats.get(qid, {})
            results.append(
                EvalResult(
                    qid=qid,
                    predicted=pred,
                    ground_truth=answer,
                    is_correct=is_correct,
                    domain=gt.get("domain", ""),
                    split=gt.get("split", ""),
                    prompt_tokens=stats.get("prompt_tokens", 0),
                    completion_tokens=stats.get("completion_tokens", 0),
                )
            )
        return results

    def by_domain(self, results: List[EvalResult]) -> Dict[str, Dict]:
        """按领域分项统计"""
        from collections import defaultdict
        domain_stats = defaultdict(lambda: {"correct": 0, "total": 0})
        for r in results:
            domain_stats[r.domain]["total"] += 1
            if r.is_correct:
                domain_stats[r.domain]["correct"] += 1
        return {
            d: {
                "correct": s["correct"],
                "total": s["total"],
                "accuracy": s["correct"] / s["total"] if s["total"] else 0.0,
            }
            for d, s in domain_stats.items()
        }

    def by_split(self, results: List[EvalResult]) -> Dict[str, Dict]:
        """按组别分项统计"""
        from collections import defaultdict
        split_stats = defaultdict(lambda: {"correct": 0, "total": 0})
        for r in results:
            split_stats[r.split]["total"] += 1
            if r.is_correct:
                split_stats[r.split]["correct"] += 1
        return {
            s: {
                "correct": st["correct"],
                "total": st["total"],
                "accuracy": st["correct"] / st["total"] if st["total"] else 0.0,
            }
            for s, st in split_stats.items()
        }


class SubmissionGenerator:
    """提交文件生成器"""

    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_answer_json(
        self,
        answers: Dict[str, str],
        token_usage: Dict[str, int],
    ) -> Path:
        """生成 answer.json"""
        output = {
            "answers": answers,
            "token_usage": token_usage,
        }
        path = self.output_dir / "answer.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        return path

    def generate_evidence_json(
        self,
        evidence: Dict[str, List[Dict]],
    ) -> Path:
        """生成 evidence.json（证据可追溯性）"""
        path = self.output_dir / "evidence.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(evidence, f, ensure_ascii=False, indent=2)
        return path

    def generate_csv(
        self,
        answers: Dict[str, str],
        token_stats: Dict[str, Dict[str, int]],
    ) -> Path:
        """生成包含 Token 统计的 CSV"""
        path = self.output_dir / "submission.csv"

        total_prompt = sum(s.get("prompt_tokens", 0) for s in token_stats.values())
        total_completion = sum(s.get("completion_tokens", 0) for s in token_stats.values())
        total_tokens = sum(s.get("total_tokens", 0) for s in token_stats.values())

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["qid", "answer", "prompt_tokens", "completion_tokens", "total_tokens"])
            writer.writerow(["summary", "", total_prompt, total_completion, total_tokens])
            for qid in sorted(answers.keys()):
                stats = token_stats.get(qid, {})
                writer.writerow([
                    qid,
                    answers.get(qid, ""),
                    stats.get("prompt_tokens", 0),
                    stats.get("completion_tokens", 0),
                    stats.get("total_tokens", 0),
                ])
        return path

    def generate_all(
        self,
        answers: Dict[str, str],
        evidence: Dict[str, List[Dict]],
        token_stats: Dict[str, Dict[str, int]],
    ) -> Dict[str, Path]:
        """生成全部提交文件"""
        token_usage_summary = {
            "prompt_tokens": sum(s.get("prompt_tokens", 0) for s in token_stats.values()),
            "completion_tokens": sum(s.get("completion_tokens", 0) for s in token_stats.values()),
            "total_tokens": sum(s.get("total_tokens", 0) for s in token_stats.values()),
        }
        return {
            "answer_json": self.generate_answer_json(answers, token_usage_summary),
            "evidence_json": self.generate_evidence_json(evidence),
            "csv": self.generate_csv(answers, token_stats),
        }
