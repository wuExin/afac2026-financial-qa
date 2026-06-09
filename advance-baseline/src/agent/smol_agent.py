"""smolagents CodeAgent 主逻辑 — grep 版

参考 baseline-v0.11 的设计：
- grep_document（系统 grep）替代 BM25 检索
- read_page 读取整页
- run_python 做数值计算
- 简洁中文 system prompt
- 多策略答案提取
"""

import os
import sys
import json
import re
import yaml
import importlib.resources
from pathlib import Path
from typing import Dict, List, Optional
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.config import load_config
from utils.llm_client import get_model
from data.loader import load_questions
from agent.tools import grep_document, read_page, run_python, init_tools


# ---- System Prompt ----
SYSTEM_PROMPT = """你是一个金融文档分析 Agent。你的任务是根据给定的文档回答选择题。

## 可用工具

- `grep_document(query, doc_path)`: 在文档目录中搜索关键词。最常用。
- `read_page(page_path)`: 读取单个页面的完整内容。
- `run_python(code)`: 执行 Python 进行数值计算。所有计算必须用它。

## 答题规则

1. 单选题只输出一个字母，如 "A"
2. 多选题按字母顺序排列，如 "ABD"（无分隔符）
3. 判断题：A=正确，B=错误
4. **多选题无部分分**：漏选、多选、错选均不得分

## 工作流程

1. 用 grep_document 搜索选项中的关键词
2. 必要时用 read_page 读取完整页面
3. 涉及数值计算用 run_python
4. 用 final_answer() 输出最终答案

## 注意事项

- 所有答案必须有文档原文支撑，不要凭常识
- 注意否定词："不适用""除外""不承担"
- 注意年份对应
- 多文档题每个文档都要检索
- 多选题逐选项判断"""


# ---- 答案提取 ----
_LETTERS_RE = re.compile(r"[ABCD]+")


def _validate(answer: str, fmt: str) -> Optional[str]:
    answer = "".join(sorted(set(answer.upper())))
    if fmt == "mcq":
        return answer if answer in "ABCD" and len(answer) == 1 else None
    elif fmt == "tf":
        return answer if answer in "AB" and len(answer) == 1 else None
    elif fmt == "multi":
        return answer if all(c in "ABCD" for c in answer) and answer != "" else None
    return None


def _extract_answer(raw_output: str, answer_format: str) -> str:
    text = raw_output.strip()
    lines = text.split("\n")
    for line in reversed(lines):
        stripped = line.strip()
        letters = "".join(sorted(set(_LETTERS_RE.findall(stripped))))
        if letters:
            validated = _validate(letters, answer_format)
            if validated:
                return validated
    all_letters = _LETTERS_RE.findall(text)
    for candidate in reversed(all_letters):
        validated = _validate(candidate, answer_format)
        if validated:
            return validated
    return "A" if answer_format != "tf" else "A"


# ---- Prompt 构建 ----
def _build_prompt(question: dict, doc_base: Path) -> str:
    qid = question["qid"]
    question_text = question["question"]
    options = question.get("options", {})
    doc_ids = question.get("doc_ids", [])
    domain = question.get("domain", "")

    option_lines = [f"{k}. {options[k]}" for k in sorted(options.keys())]
    options_text = "\n".join(option_lines)

    doc_paths = [str(doc_base / domain / str(did)) for did in doc_ids]
    doc_paths_text = "\n".join(f"- {p}" for p in doc_paths)

    fmt = question.get("type", "mcq")
    # type 可能是中文（"多选题"/"单选题"/"判断题"）或英文（"multi"/"mcq"/"tf"）
    if fmt in ("多选题", "multi"):
        fmt = "multi"
        fmt_hint = "多选题，按字母顺序排列（如 ABD）。"
    elif fmt in ("判断题", "tf"):
        fmt = "tf"
        fmt_hint = "判断题：A=正确，B=错误。"
    else:
        fmt = "mcq"
        fmt_hint = "单选题，只输出一个字母。"

    return (
        f"题目ID：{qid}\n"
        f"题目：{question_text}\n\n"
        f"选项：\n{options_text}\n\n"
        f"文档路径：\n{doc_paths_text}\n\n"
        f"{fmt_hint}\n"
        f"请分析文档后输出最终答案。"
    )


# ---- 单题处理 ----
def _process_one(agent, question: dict, doc_base: Path) -> dict:
    qid = question["qid"]
    prompt = _build_prompt(question, doc_base)
    answer_format = question.get("type", "mcq")
    if answer_format in ("多选题", "multi"):
        answer_format = "multi"
    elif answer_format in ("判断题", "tf"):
        answer_format = "tf"
    else:
        answer_format = "mcq"

    try:
        result = agent.run(prompt)
        raw = str(result) if result else ""
        answer = _extract_answer(raw, answer_format)
        return {"qid": qid, "answer": answer, "raw": raw}
    except Exception as e:
        return {"qid": qid, "answer": "A", "raw": f"ERROR: {e}"}


# ---- 主入口 ----
def main():
    load_dotenv()
    config = load_config()
    model = get_model(config)

    print("正在加载题目...")
    questions = load_questions(config)

    doc_base = Path(config.get("paths", {}).get("processed_docs", "data/processed_pymupdf4llm"))
    init_tools(doc_base)

    from smolagents import CodeAgent

    # 加载默认模板，替换 system_prompt
    default_prompts = yaml.safe_load(
        importlib.resources.files("smolagents.prompts").joinpath("code_agent.yaml").read_text()
    )
    default_prompts["system_prompt"] = SYSTEM_PROMPT

    agent = CodeAgent(
        tools=[grep_document, read_page, run_python],
        model=model,
        additional_authorized_imports=["math", "re", "json", "collections", "subprocess", "pathlib"],
        max_steps=config.get("agent", {}).get("max_steps", 12),
        prompt_templates=default_prompts,
    )

    print(f"开始处理 {len(questions)} 道题目...")
    results = []
    errors = []

    for i, q in enumerate(questions):
        qid = q.get("qid", f"q_{i}")
        print(f"[{i+1}/{len(questions)}] 处理 {qid}...")
        result = _process_one(agent, q, doc_base)
        results.append(result)
        print(f"  -> {result['answer']}")
        if result["answer"] == "A" and "ERROR" in str(result.get("raw", "")):
            errors.append(qid)

    output_dir = Path(config.get("paths", {}).get("output_dir", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)

    answers = {r["qid"]: r["answer"] for r in results}
    with open(output_dir / "answer.json", "w", encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False, indent=2)

    # 生成 submission.csv
    import csv
    with open(output_dir / "submission.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["qid", "answer"])
        for qid in sorted(answers):
            w.writerow([qid, answers[qid]])

    print(f"\n处理完成！总计: {len(results)} 题, 错误: {len(errors)} 题")
    print(f"结果已保存到 {output_dir}/")


if __name__ == "__main__":
    main()