"""
Agent 运行入口（v0.1 No-Tool 版）：批量处理赛题问题并生成结果（并发版本）。
去掉了 Tool Use，直接文本输出答案。
"""
import argparse
import csv
import json
import os
import random
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.utils.helpers import load_config, load_json, save_json, setup_logging
from src.agent.agent import FinancialQAAgent


def load_questions(questions_dir: Path, split: str) -> list:
    """加载指定组别的所有题目"""
    questions = []
    for domain_file in sorted(questions_dir.glob("*_questions.json")):
        data = load_json(domain_file)
        for q in data:
            if q.get("split", "").upper() == split.upper():
                questions.append(q)
    questions.sort(key=lambda x: x["qid"])
    return questions


def process_one(agent: FinancialQAAgent, question: dict, idx: int, total: int) -> dict:
    """处理单道题，返回结果字典"""
    qid = question["qid"]
    try:
        answer, evidence, token_usage = agent.answer_question(question)
        print(f"[{idx+1}/{total}] {qid} -> answer: {answer} | Tokens: {token_usage['total_tokens']}")
        return {
            "qid": qid,
            "answer": answer,
            "domain": question.get("domain", ""),
            "split": question.get("split", ""),
            "answer_format": question.get("answer_format", ""),
            "doc_ids": question.get("doc_ids", []),
            **token_usage,
        }
    except Exception as e:
        print(f"[{idx+1}/{total}] {qid} ERROR: {e}")
        return {
            "qid": qid,
            "answer": "",
            "domain": question.get("domain", ""),
            "split": question.get("split", ""),
            "answer_format": question.get("answer_format", ""),
            "doc_ids": question.get("doc_ids", []),
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "error": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="AFAC2026 金融长文本问答 Agent")
    parser.add_argument("--split", type=str, default="A", help="运行组别: A 或 B")
    parser.add_argument("--output", type=str, default=None, help="输出文件路径")
    parser.add_argument("--workers", type=int, default=20, help="并发线程数（默认 20）")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 道题，其余随机填充（方便快速测试）")
    parser.add_argument("--tag", type=str, default=None, help="输出文件名后缀，便于保留多次实验结果")
    args = parser.parse_args()

    logger = setup_logging()
    config = load_config()

    # 加载环境变量
    from src.utils.helpers import load_env
    load_env()

    # 加载题目
    questions_dir = Path(config["data"]["questions_dir"])
    all_questions = load_questions(questions_dir, args.split)
    logger.info(f"加载 {args.split} 组题目: {len(all_questions)} 道")

    if not all_questions:
        logger.warning("未找到题目，请检查数据路径")
        return

    # 如果指定了 limit，只处理前 N 道，其余随机填充
    if args.limit is not None and args.limit < len(all_questions):
        questions = all_questions[:args.limit]
        logger.info(f"--limit={args.limit}，只处理前 {args.limit} 道题，其余 {len(all_questions) - args.limit} 道随机填充")
    else:
        questions = all_questions

    # 运行 Agent（并发）
    agent = FinancialQAAgent(config)
    results = {}

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_qid = {
            executor.submit(process_one, agent, q, i, len(questions)): q["qid"]
            for i, q in enumerate(questions)
        }
        for future in as_completed(future_to_qid):
            result = future.result()
            results[result["qid"]] = result

    # 随机填充未处理的题目
    if args.limit is not None and args.limit < len(all_questions):
        for q in all_questions[args.limit:]:
            answer_format = q.get("answer_format", "mcq")
            if answer_format == "multi":
                # 多选：随机选 1-3 个选项
                n = random.randint(1, 3)
                rand_answer = "".join(sorted(random.sample("ABCD", n)))
            elif answer_format == "tf":
                rand_answer = random.choice(["A", "B"])
            else:
                rand_answer = random.choice(["A", "B", "C", "D"])
            results[q["qid"]] = {
                "qid": q["qid"],
                "answer": rand_answer,
                "domain": q.get("domain", ""),
                "split": q.get("split", ""),
                "answer_format": q.get("answer_format", ""),
                "doc_ids": q.get("doc_ids", []),
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "random_fill": True,
            }

    # 统计
    total_prompt = sum(r.get("prompt_tokens", 0) for r in results.values())
    total_completion = sum(r.get("completion_tokens", 0) for r in results.values())
    total_tokens = sum(r.get("total_tokens", 0) for r in results.values())
    errors = sum(1 for r in results.values() if "error" in r)
    logger.info(
        f"完成。成功率: {len(questions)-errors}/{len(questions)}, "
        f"Total prompt={total_prompt}, completion={total_completion}, "
        f"total={total_tokens}"
    )

    # 保存结果
    output_dir = Path(config["data"]["output_dir"])
    output_dir.mkdir(parents=True, exist_ok=True)

    suffix = f"_{args.tag}" if args.tag else ""
    output_path = Path(args.output) if args.output else output_dir / f"results_{args.split.lower()}{suffix}.json"
    save_json(results, output_path)
    logger.info(f"结果已保存: {output_path}")

    # 同时生成 CSV（提交格式）
    csv_path = output_dir / f"submission_{args.split.lower()}{suffix}.csv"
    generate_csv(results, csv_path)
    logger.info(f"提交 CSV 已保存: {csv_path}")

    diagnostics_path = output_dir / f"diagnostics_{args.split.lower()}{suffix}.csv"
    generate_diagnostics_csv(results, diagnostics_path)
    logger.info(f"诊断 CSV 已保存: {diagnostics_path}")


def generate_csv(results: dict, csv_path: Path):
    """生成赛题要求的 CSV 格式"""
    total_prompt = sum(r.get("prompt_tokens", 0) for r in results.values())
    total_completion = sum(r.get("completion_tokens", 0) for r in results.values())
    total_tokens = sum(r.get("total_tokens", 0) for r in results.values())

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["qid", "answer", "prompt_tokens", "completion_tokens", "total_tokens"])
        # Summary row
        writer.writerow(["summary", "", total_prompt, total_completion, total_tokens])
        for qid in sorted(results.keys()):
            r = results[qid]
            writer.writerow([
                qid,
                r.get("answer", ""),
                r.get("prompt_tokens", 0),
                r.get("completion_tokens", 0),
                r.get("total_tokens", 0),
            ])


def generate_diagnostics_csv(results: dict, csv_path: Path):
    """生成实验诊断 CSV，便于按 qid 对比检索效果与 token 消耗。"""
    fields = [
        "qid",
        "domain",
        "answer_format",
        "doc_ids",
        "answer",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "retrieval_method",
        "query_count",
        "chunk_count",
        "candidate_count",
        "retrieved_windows",
        "retrieved_chars",
        "doc_coverage",
        "max_bm25_score",
        "avg_bm25_score",
        "selected_sources",
        "retrieval_doc_stats",
        "retries",
        "random_fill",
        "error",
    ]

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for qid in sorted(results.keys()):
            r = results[qid]
            writer.writerow({
                "qid": qid,
                "domain": r.get("domain", ""),
                "answer_format": r.get("answer_format", ""),
                "doc_ids": "|".join(map(str, r.get("doc_ids", []))),
                "answer": r.get("answer", ""),
                "prompt_tokens": r.get("prompt_tokens", 0),
                "completion_tokens": r.get("completion_tokens", 0),
                "total_tokens": r.get("total_tokens", 0),
                "retrieval_method": r.get("retrieval_method", ""),
                "query_count": r.get("query_count", ""),
                "chunk_count": r.get("chunk_count", ""),
                "candidate_count": r.get("candidate_count", ""),
                "retrieved_windows": r.get("retrieved_windows", ""),
                "retrieved_chars": r.get("retrieved_chars", ""),
                "doc_coverage": r.get("doc_coverage", ""),
                "max_bm25_score": r.get("max_bm25_score", ""),
                "avg_bm25_score": r.get("avg_bm25_score", ""),
                "selected_sources": json.dumps(r.get("selected_sources", []), ensure_ascii=False),
                "retrieval_doc_stats": json.dumps(r.get("retrieval_doc_stats", {}), ensure_ascii=False),
                "retries": r.get("retries", ""),
                "random_fill": r.get("random_fill", ""),
                "error": r.get("error", ""),
            })


if __name__ == "__main__":
    main()
