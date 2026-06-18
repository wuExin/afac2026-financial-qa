"""跳过 LLM，只跑 BM25 检索并落盘 logs/<qid>.json。用于网页可视化排查检索质量。

用法： python scripts/run_retrieval_only.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.helpers import load_config, load_json  # noqa: E402
from src.agent.agent import FinancialQAAgent, BM25Retriever  # noqa: E402


def load_all_questions(questions_dir: Path) -> list:
    questions = []
    for f in sorted(questions_dir.glob("*_questions.json")):
        for q in load_json(f):
            questions.append(q)
    questions.sort(key=lambda x: x.get("qid", ""))
    return questions


def main():
    config = load_config()
    questions_dir = Path(config["data"]["questions_dir"])
    questions = load_all_questions(questions_dir)
    print(f"加载 {len(questions)} 道题")

    # 绕过 FinancialQAAgent.__init__ 跳过 LLM
    agent = FinancialQAAgent.__new__(FinancialQAAgent)
    agent.config = config
    agent.retriever = BM25Retriever(config)  # 自动初始化 retrieval_logger

    log_dir = Path(config.get("logging", {}).get("retrieval_log_dir", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    for i, q in enumerate(questions, 1):
        qid = q.get("qid", "")
        try:
            evidence = agent._load_evidence(q)
            retrieved, stats = agent.retriever.retrieve(q, evidence)
            n = len(stats.get("selected_sources", []))
            print(f"[{i}/{len(questions)}] {qid}: chunks={stats.get('chunk_count', 0)}, "
                  f"windows={stats.get('retrieved_windows', 0)}, max_score={stats.get('max_bm25_score', 0):.1f}")
        except Exception as e:
            print(f"[{i}/{len(questions)}] {qid} ERROR: {e}", file=sys.stderr)

    print(f"\n完成。logs/ 下生成 {len(list(log_dir.glob('*.json')))} 个 JSON")


if __name__ == "__main__":
    main()
