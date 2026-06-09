"""
Agent 模块：金融长文本问答 Agent 核心逻辑（Baseline 版本）。

Baseline 策略：
1. A 组直接读取 doc_ids 对应的已解析文档内容
2. 单轮 LLM 调用，文档 + 问题 + 选项 → 答案
3. 简单截断策略控制上下文长度
4. 答案规范化与校验
"""

import os
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from pathlib import Path

from src.utils.llm_client import LLMClient
from src.utils.helpers import load_config, normalize_answer, count_tokens


@dataclass
class TokenUsage:
    """Token 消耗追踪"""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Evidence:
    """证据条目"""
    doc_id: str
    content: str
    source: str
    relevance_score: float = 0.0


@dataclass
class MemoryState:
    """Agent 记忆状态"""
    retrieved_evidence: List[Evidence] = field(default_factory=list)
    key_facts: Dict[str, str] = field(default_factory=dict)
    reasoning_chain: List[str] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)


class ContextManager:
    """上下文管理器：简单截断（Baseline）"""

    def __init__(self, max_chars: int = 320000, max_doc_chars: int = 4000):
        # 总 prompt 截断上限
        self.max_chars = max_chars
        # 单篇文档截断上限（避免 content_filter）
        self.max_doc_chars = max_doc_chars

    def truncate_doc(self, text: str) -> str:
        """单篇文档截断"""
        if len(text) <= self.max_doc_chars:
            return text
        return text[: self.max_doc_chars] + "\n\n[文档后续内容已省略]"

    def truncate(self, text: str) -> str:
        if len(text) <= self.max_chars:
            return text
        return text[: self.max_chars] + "\n\n[文档内容已截断]"


class PromptBuilder:
    """提示词构建器（Baseline）"""

    def build_prompt(
        self,
        question: Dict,
        evidence: List[Evidence],
        context_manager: Optional[ContextManager] = None,
    ) -> str:
        """构建单题问答提示词"""
        # 拼接文档内容，对每篇文档单独截断
        context_parts = []
        for ev in evidence:
            content = ev.content
            if context_manager:
                content = context_manager.truncate_doc(content)
            context_parts.append(f"【文档 {ev.doc_id}】\n{content}")
        context = "\n\n".join(context_parts)

        # 选项格式化
        options_text = "\n".join(
            [f"{k}. {v}" for k, v in question.get("options", {}).items()]
        )

        prompt = (
            "你是一位金融文档分析专家。请根据以下提供的文档内容，回答问题。\n"
            "要求：\n"
            "1. 仔细阅读文档中的相关条款、数据和事实\n"
            "2. 对每个选项进行独立判断\n"
            "3. 只输出最终答案字母，不要解释过程\n"
            "4. 多选题答案按字母顺序排列，不加分隔符\n\n"
            f"{context}\n\n"
            f"问题：{question['question']}\n\n"
            f"选项：\n{options_text}\n\n"
            f"答案："
        )
        return prompt


class AnswerValidator:
    """答案校验器"""

    @staticmethod
    def validate_mcq(answer: str) -> bool:
        return answer in {"A", "B", "C", "D"}

    @staticmethod
    def validate_multi(answer: str) -> bool:
        return all(c in "ABCD" for c in answer) and answer == "".join(sorted(answer)) and answer != ""

    @staticmethod
    def validate_tf(answer: str) -> bool:
        return answer in {"A", "B"}

    @staticmethod
    def validate(answer: str, answer_format: str) -> str:
        validators = {
            "mcq": AnswerValidator.validate_mcq,
            "multi": AnswerValidator.validate_multi,
            "tf": AnswerValidator.validate_tf,
        }
        validator = validators.get(answer_format)
        if validator and validator(answer):
            return answer
        raise ValueError(f"Invalid answer '{answer}' for format '{answer_format}'")


class FinancialQAAgent:
    """金融长文本问答 Agent（Baseline No-Tool 版）"""

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or load_config()
        self.context_manager = ContextManager(
            max_chars=self.config.get("model", {}).get("max_context_tokens", 320000),
            max_doc_chars=4000,
        )
        self.prompt_builder = PromptBuilder()
        self.memory = MemoryState()

        model_cfg = self.config.get("model", {})
        self.llm = LLMClient(
            api_key=os.getenv("DASHSCOPE_API_KEY"),
            base_url=os.getenv("API_BASE_URL", model_cfg.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1")),
            model=os.getenv("MODEL_NAME", model_cfg.get("name", "qwen-plus")),
            temperature=model_cfg.get("temperature", 0.0),
        )

    def _read_document(self, domain: str, doc_id: str) -> str:
        """读取已解析的文档内容，尝试多个数据路径"""
        roots = [
            Path(self.config["data"].get("processed_pymupdf4llm_dir", "data/processed_pymupdf4llm")),
            Path("design-draft/data/processed_pymupdf4llm"),
            Path("data/processed_pymupdf4llm"),
        ]

        for root in roots:
            parsed_dir = root / domain / str(doc_id)
            if parsed_dir.exists():
                pages = sorted(parsed_dir.glob("page_*.md"))
                texts = []
                for page in pages:
                    try:
                        texts.append(page.read_text(encoding="utf-8"))
                    except Exception:
                        continue
                return "\n\n".join(texts)

            domain_dir = root / domain
            if domain_dir.exists():
                for child in domain_dir.iterdir():
                    if child.name.lstrip("0") == str(doc_id).lstrip("0"):
                        pages = sorted(child.glob("page_*.md"))
                        texts = []
                        for page in pages:
                            try:
                                texts.append(page.read_text(encoding="utf-8"))
                            except Exception:
                                continue
                        return "\n\n".join(texts)

        return f"[文档 {doc_id} 未找到]"

    def _load_evidence(self, question: Dict) -> List[Evidence]:
        """加载题目指定的文档证据（A 组）"""
        domain = question["domain"]
        doc_ids = question.get("doc_ids", [])
        evidence = []
        for doc_id in doc_ids:
            content = self._read_document(domain, doc_id)
            evidence.append(
                Evidence(
                    doc_id=str(doc_id),
                    content=content,
                    source=f"{domain}/{doc_id}",
                )
            )
        return evidence

    def answer_question(
        self,
        question: Dict,
    ) -> Tuple[str, List[Evidence], Dict]:
        """回答单个问题，返回 (答案, 证据, Token 统计)"""
        # 1. 加载证据
        evidence = self._load_evidence(question)

        # 2. 构建 prompt
        prompt = self.prompt_builder.build_prompt(question, evidence, self.context_manager)
        prompt = self.context_manager.truncate(prompt)

        # 3. 调用 LLM（带重试），直接输出文本答案
        messages = [{"role": "user", "content": prompt}]
        response = self.llm.chat(messages, max_tokens=4096)

        # 检测 content_filter 或空输出，尝试缩短文档重试
        retry_count = 0
        max_retries = 2
        while (
            response.finish_reason in ("content_filter", "length")
            or not response.content.strip()
        ) and retry_count < max_retries:
            retry_count += 1
            old_max = self.context_manager.max_doc_chars
            self.context_manager.max_doc_chars = max(1000, old_max // 2)
            prompt = self.prompt_builder.build_prompt(question, evidence, self.context_manager)
            prompt = self.context_manager.truncate(prompt)
            response = self.llm.chat([{"role": "user", "content": prompt}], max_tokens=4096)
            self.context_manager.max_doc_chars = old_max

        # 4. 从内容中提取答案
        answer = self._parse_answer(response, question.get("answer_format", "mcq"))

        token_usage = {
            "prompt_tokens": response.usage.prompt_tokens,
            "completion_tokens": response.usage.completion_tokens,
            "total_tokens": response.usage.total_tokens,
            "retries": retry_count,
        }

        return answer, evidence, token_usage

    def _parse_answer(self, response, answer_format: str) -> str:
        """从 LLM 响应文本中解析答案"""

        raw = (response.content or "").strip()
        answer = normalize_answer(raw, answer_format)
        try:
            AnswerValidator.validate(answer, answer_format)
            return answer
        except ValueError:
            return self._extract_answer_fallback(raw, answer_format)

    def _extract_answer_fallback(self, raw: str, answer_format: str) -> str:
        """从模型输出中暴力提取答案字母"""
        import re
        letters = "".join(sorted(set(re.findall(r"[A-D]", raw.upper()))))
        if answer_format in ("mcq", "tf"):
            for c in letters:
                if c in "ABCD":
                    return c
            return "A"  # 实在找不到，默认 A
        elif answer_format == "multi":
            return letters if letters else "A"
        return "A"

    def run(
        self,
        questions: List[Dict],
        split: str = "A",
    ) -> Dict[str, Dict]:
        """批量运行问答，返回结果字典"""
        results = {}
        for idx, question in enumerate(questions):
            qid = question["qid"]
            print(f"[{idx+1}/{len(questions)}] 处理 {qid} ...")
            try:
                answer, evidence, token_usage = self.answer_question(question)
                results[qid] = {
                    "qid": qid,
                    "answer": answer,
                    "domain": question.get("domain", ""),
                    "split": split,
                    **token_usage,
                }
                print(f"  → 答案: {answer} | Tokens: {token_usage['total_tokens']}")
            except Exception as e:
                print(f"  ✗ 错误: {e}")
                results[qid] = {
                    "qid": qid,
                    "answer": "",
                    "domain": question.get("domain", ""),
                    "split": split,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "error": str(e),
                }
        return results
