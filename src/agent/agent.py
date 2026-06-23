"""
Agent 模块：金融长文本问答 Agent 核心逻辑（Baseline 版本）。

Baseline 策略：
1. A 组直接读取 doc_ids 对应的已解析文档内容
2. 单轮 LLM 调用，文档 + 问题 + 选项 → 答案
3. 简单截断策略控制上下文长度
4. 答案规范化与校验
"""

import os
import math
import re
import json
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

from src.utils.llm_client import LLMClient
from src.utils.helpers import load_config, normalize_answer, count_tokens
from src.agent.retrieval_logger import RetrievalLogger


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


class HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts: List[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self._parts)


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


def _option_values(options) -> List[str]:
    if isinstance(options, dict):
        return [str(v) for v in options.values()]
    if isinstance(options, list):
        return [str(v) for v in options]
    return []


class RollingWindowRetriever:
    """全文关键词检索 + 滚动窗口召回。"""

    CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
    WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%/-]*")

    STOPWORDS = {
        "以下", "关于", "根据", "下列", "哪些", "哪个", "哪项", "是否",
        "正确", "错误", "不正确", "符合", "不符合", "描述", "说法",
        "选项", "判断", "分别", "其中", "进行", "有关", "的是",
    }

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.window_chars = int(cfg.get("window_chars", 3000))
        self.window_overlap = int(cfg.get("window_overlap", 800))
        self.top_k = int(cfg.get("top_k", 8))
        self.per_doc_top_k = int(cfg.get("per_doc_top_k", 3))
        self.max_total_chars = int(cfg.get("max_total_chars", 60000))
        self.min_score = float(cfg.get("min_score", 1.0))

    def retrieve(self, question: Dict, evidence: List[Evidence]) -> Tuple[List[Evidence], Dict]:
        terms = self._build_terms(question)
        windows = []

        for ev in evidence:
            doc_windows = self._rank_doc_windows(ev, terms)
            selected = [
                item for item in doc_windows[: self.per_doc_top_k]
                if item["score"] >= self.min_score
            ]
            if not selected and doc_windows:
                selected = doc_windows[:1]
            windows.extend(selected)

        windows.sort(key=lambda item: item["score"], reverse=True)

        selected_windows = []
        used_chars = 0
        for item in windows[: self.top_k]:
            content_len = len(item["content"])
            if selected_windows and used_chars + content_len > self.max_total_chars:
                continue
            selected_windows.append(item)
            used_chars += content_len

        by_doc: Dict[str, List[Dict]] = {}
        for item in selected_windows:
            by_doc.setdefault(item["doc_id"], []).append(item)

        retrieved = []
        for ev in evidence:
            items = sorted(by_doc.get(ev.doc_id, []), key=lambda item: item["start"])
            if not items:
                content = ev.content[: min(self.window_chars, len(ev.content))]
                retrieved.append(
                    Evidence(
                        doc_id=ev.doc_id,
                        content=f"[未命中高相关窗口，保留文档开头]\n{content}",
                        source=ev.source,
                        relevance_score=0.0,
                    )
                )
                continue

            parts = []
            for idx, item in enumerate(items, 1):
                parts.append(
                    f"[召回片段 {idx} | 位置 {item['start']}-{item['end']} | "
                    f"score={item['score']:.2f}]\n{item['content']}"
                )
            retrieved.append(
                Evidence(
                    doc_id=ev.doc_id,
                    content="\n\n".join(parts),
                    source=ev.source,
                    relevance_score=max(item["score"] for item in items),
                )
            )

        stats = {
            "retrieval_method": "keyword_rolling_window",
            "retrieval_terms": terms[:50],
            "retrieved_windows": len(selected_windows),
            "retrieved_chars": sum(len(item["content"]) for item in selected_windows),
            "retrieval_doc_stats": {
                doc_id: {
                    "windows": len(items),
                    "chars": sum(len(item["content"]) for item in items),
                    "max_score": max(item["score"] for item in items) if items else 0.0,
                }
                for doc_id, items in by_doc.items()
            },
        }
        return retrieved, stats

    def _rank_doc_windows(self, evidence: Evidence, terms: List[str]) -> List[Dict]:
        text = evidence.content
        if not text:
            return []

        step = max(1, self.window_chars - self.window_overlap)
        windows = []
        for start in range(0, len(text), step):
            end = min(len(text), start + self.window_chars)
            content = self._expand_to_line_boundary(text, start, end)
            score = self._score(content, terms)
            windows.append(
                {
                    "doc_id": evidence.doc_id,
                    "source": evidence.source,
                    "start": start,
                    "end": end,
                    "score": score,
                    "content": content.strip(),
                }
            )
            if end >= len(text):
                break
        return sorted(windows, key=lambda item: item["score"], reverse=True)

    def _expand_to_line_boundary(self, text: str, start: int, end: int) -> str:
        left = text.rfind("\n", 0, start)
        right = text.find("\n", end)
        if left == -1:
            left = start
        if right == -1:
            right = end
        return text[left:right]

    def _score(self, content: str, terms: List[str]) -> float:
        score = 0.0
        lowered = content.lower()
        for term in terms:
            haystack = lowered if term.isascii() else content
            needle = term.lower() if term.isascii() else term
            count = haystack.count(needle)
            if not count:
                continue
            weight = 1.0
            if len(term) >= 6:
                weight += 1.0
            if any(ch.isdigit() for ch in term):
                weight += 1.0
            score += min(count, 4) * weight
        return score

    def _build_terms(self, question: Dict) -> List[str]:
        text_parts = [question.get("question", "")]
        text_parts.extend(str(v) for v in question.get("options", {}).values())
        text = "\n".join(text_parts)

        terms = []
        terms.extend(self.WORD_RE.findall(text))

        for match in self.CJK_RE.findall(text):
            clean = match.strip()
            if len(clean) < 2:
                continue
            if len(clean) <= 12:
                terms.append(clean)
            for size in (2, 3, 4, 6):
                if len(clean) < size:
                    continue
                for idx in range(0, len(clean) - size + 1):
                    terms.append(clean[idx: idx + size])

        unique = []
        seen = set()
        for term in terms:
            term = term.strip()
            if len(term) < 2 or term in self.STOPWORDS:
                continue
            if term in seen:
                continue
            seen.add(term)
            unique.append(term)
        unique.sort(key=lambda item: (any(ch.isdigit() for ch in item), len(item)), reverse=True)
        return unique[:120]


DEFAULT_INTENT_TERMS = {
    "financial_reports": {
        "financial_compare": ["主要会计数据", "财务指标", "营业收入", "营业总收入", "净利润", "归母净利润", "归属于上市公司股东的净利润", "同比", "增长率"],
        "cashflow": ["现金流量表", "经营活动产生的现金流量净额", "经营现金流", "现金流量净额"],
        "rd": ["研发投入", "研发费用", "研发投入占营业收入比例", "研发投入金额", "研发人员"],
        "dividend": ["利润分配", "利润分配方案", "现金分红", "分红比例", "每股现金分红", "末期股息", "股东回报", "股份回购", "回购金额"],
        "scale": ["总资产", "净资产", "企业规模", "资产总额"],
    },
    "financial_contracts": {
        "issuance": ["发行人", "发行主体", "发行规模", "发行金额", "注册金额", "主体信用评级", "债项评级", "信用评级", "主承销商", "受托管理人", "中介机构"],
        "clause": ["违约责任", "违约赔偿", "赎回条款", "回售条款", "保护性条款", "债券持有人", "信息披露", "募集资金用途"],
        "convertible": ["可转换公司债券", "可转债", "转股价格", "初始转股价格", "转股价格向下修正", "有条件赎回", "有条件回售"],
        "security_info": ["股票代码", "证券代码", "股票简称", "证券简称", "发行日期", "公告日期"],
        "financial_data": ["资产负债率", "财务指标", "利润总额", "净利润", "董事及高级管理人员", "真实性"],
    },
    "insurance": {
        "claim": ["保险责任", "赔付比例", "保险金额", "身故保险金", "赔偿处理", "赔偿上限", "责任范围"],
        "waiting_period": ["等待期", "意外伤害", "非意外"],
        "surrender": ["现金价值", "退保", "退保费用", "账户价值", "保单账户价值", "已交保费", "犹豫期"],
        "medical": ["免赔额", "免赔率", "医保报销", "自费", "医疗费用", "住院", "门诊", "特定药品费用"],
        "policy_status": ["宽限期", "效力中止", "复效", "保单贷款"],
        "annuity": ["养老年金", "领取日", "开始领取日", "变更"],
        "exclusion": ["责任免除", "免责", "故意自伤", "自杀", "除外"],
        "property_auto": ["车上人员责任险", "施救费用", "特种车", "家庭财产", "水管爆裂"],
    },
    "regulatory": {
        "deadline": ["报告期限", "工作日", "自然日", "施行日期", "实施日期", "自发布之日起施行", "过渡期"],
        "aml": ["反洗钱", "客户尽职调查", "客户身份资料", "交易记录保存", "受益所有人", "高风险", "非自然人客户"],
        "payment_clearing": ["非银行支付机构", "银行卡清算机构", "收费标准", "董事", "监事", "高级管理人员", "变更报告"],
        "data_security": ["数据安全", "敏感数据", "照片", "终端设备", "统一规范管理"],
        "listed_company": ["上市公司", "公司治理", "治理准则", "章程指引", "股东大会", "股东会", "董事会", "董事候选人"],
        "disclosure_report": ["定期报告", "半年度报告", "年度报告", "信息披露", "利润分配", "现金分红"],
        "enforcement": ["行政处罚", "市场禁入", "分类监管", "分类评价", "证券公司", "审计机构", "法律责任"],
        "obligation": ["应当", "不得", "金融机构", "监管部门", "内部审批", "金额门槛"],
    },
    "research": {
        "market": ["市场规模", "行业规模", "同比", "复合增速", "复合增长率", "渗透率", "市占率", "市场份额", "预测"],
        "bank_insurance": ["银保渠道", "银行 IT", "金融信创", "ICT", "金融机构配置", "上市险企", "保险行业"],
        "technology": ["光通信", "数据中心", "半导体", "芯片", "IP 授权", "网络安全", "安全运营", "检测规则", "解析规则"],
        "consumption": ["服务消费", "居民收入", "宠物医疗", "消费趋势"],
        "brokerage_fund": ["上市券商", "客户资金杠杆", "自有资产净利率", "基金份额", "主动型", "新成立基金"],
        "ev_chemical": ["电动车", "新能源渗透率", "宁德时代", "碳酸锂", "锂电", "PE 估值", "大宗商品", "油价", "化工品"],
        "company": ["营收", "净利润", "出货量", "销量", "财务表现"],
    },
}


def _flatten_intent_terms(domain_terms: Dict[str, List[str]]) -> List[str]:
    terms = []
    seen = set()
    for bucket_terms in domain_terms.values():
        for term in bucket_terms:
            if term in seen:
                continue
            seen.add(term)
            terms.append(term)
    return terms


class IntentTermSelector:
    """让 LLM 从白名单意图词里挑选 BM25 追加查询词。"""

    def __init__(self, enabled: bool = False, max_terms: int = 8):
        self.enabled = enabled
        self.max_terms = max(0, int(max_terms))
        self.last_usage = None

    def select(self, question: Dict, llm, intent_terms: Dict[str, Dict[str, List[str]]]) -> List[str]:
        if not self.enabled or self.max_terms <= 0:
            return []

        domain = question.get("domain", "")
        domain_terms = intent_terms.get(domain, {})
        candidates = _flatten_intent_terms(domain_terms)
        if not candidates:
            return []

        prompt = self._build_prompt(question, domain_terms)
        try:
            response = llm.chat(
                [{"role": "user", "content": prompt}],
                max_tokens=512,
                temperature=0.0,
            )
            self.last_usage = getattr(response, "usage", None)
        except Exception as exc:
            print(f"  [intent_terms_failed] {exc}")
            self.last_usage = None
            return []

        return self._parse_terms(response.content, candidates)

    def _build_prompt(self, question: Dict, domain_terms: Dict[str, List[str]]) -> str:
        options_text = "\n".join(_option_values(question.get("options", {})))
        candidate_lines = []
        for name, terms in domain_terms.items():
            candidate_lines.append(f"{name}: " + "、".join(terms))

        return (
            "你是金融问答检索意图分析器。请根据题目和选项，从候选意图词白名单中选择需要追加到 BM25 查询的关键词。\n"
            "只允许选择候选词中的原词，不要创造新词。若题目本身已经足够明确，也可以少选。\n"
            f"最多选择 {self.max_terms} 个，按重要性排序。\n"
            "输出必须是 JSON，格式为 {\"terms\": [\"关键词1\", \"关键词2\"]}。\n\n"
            f"题目：{question.get('question', '')}\n\n"
            f"选项：\n{options_text}\n\n"
            "候选意图词：\n" + "\n".join(candidate_lines)
        )

    def _parse_terms(self, raw: str, candidates: List[str]) -> List[str]:
        candidate_set = set(candidates)
        parsed_terms = []
        try:
            payload = json.loads((raw or "").strip())
            if isinstance(payload, dict):
                parsed_terms = payload.get("terms", [])
            elif isinstance(payload, list):
                parsed_terms = payload
        except json.JSONDecodeError:
            parsed_terms = [term for term in candidates if term in (raw or "")]

        selected = []
        seen = set()
        for term in parsed_terms:
            term = str(term).strip()
            if term not in candidate_set or term in seen:
                continue
            seen.add(term)
            selected.append(term)
            if len(selected) >= self.max_terms:
                break
        return selected


class BM25Retriever:
    """BM25 粗召回 + 窗口扩展 + 去重合并 + 跨文档配额。"""

    CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
    WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._%/-]*")
    HEADING_RE = re.compile(r"(保险责任|责任免除|等待期|免赔额|现金价值|退保|主要会计数据|财务指标|营业收入|净利润|研发|现金流|募集资金|发行规模|票面利率|评级|担保|受益所有人|客户尽职调查|保存期限)")

    STOPWORDS = RollingWindowRetriever.STOPWORDS | {
        "文档", "第一份", "第二份", "第三份", "第四份", "材料", "内容",
        "均为", "均有", "均未", "不是", "属于", "包括", "以及", "或者",
    }

    INTENT_TERMS = DEFAULT_INTENT_TERMS
    DOMAIN_TERMS = {
        domain: _flatten_intent_terms(domain_terms)
        for domain, domain_terms in DEFAULT_INTENT_TERMS.items()
    }

    def __init__(self, config: Optional[Dict] = None):
        cfg = config or {}
        self.k1 = float(cfg.get("bm25_k1", 1.5))
        self.b = float(cfg.get("bm25_b", 0.75))
        self.chunk_size_chars = cfg.get("chunk_size_chars", 1400)
        self.chunk_overlap_chars = int(cfg.get("chunk_overlap_chars", 250))
        self.min_chunk_chars = int(cfg.get("min_chunk_chars", 200))
        self.expand_before_chars = int(cfg.get("expand_before_chars", 800))
        self.expand_after_chars = int(cfg.get("expand_after_chars", 1200))
        self.merge_gap_chars = int(cfg.get("merge_gap_chars", 500))
        self.per_doc_min = int(cfg.get("per_doc_min", 1))
        self.per_doc_max = cfg.get("per_doc_max", 3)
        self.global_top_k = cfg.get("global_top_k", cfg.get("top_k", 8))
        self.max_total_chars = cfg.get("max_total_chars", 60000)
        self.min_score = float(cfg.get("min_score", 0.1))
        self.max_query_terms = int(cfg.get("max_query_terms", 120))

        # 检索日志（默认开；失败静默不影响主流程）
        log_cfg = (cfg.get("logging") or {})
        self.log_retrieval = bool(log_cfg.get("log_retrieval", True))
        self.retrieval_logger = RetrievalLogger(
            log_dir=log_cfg.get("retrieval_log_dir", "logs"),
            enabled=self.log_retrieval,
        )

    def retrieve(self, question: Dict, evidence: List[Evidence]) -> Tuple[List[Evidence], Dict]:
        answer_format = question.get("answer_format", "mcq")
        domain = question.get("domain", "")
        chunks = self._build_chunks(evidence, domain)
        queries = self._build_queries(question)

        if not chunks or not queries:
            empty_stats = self._empty_stats("bm25", len(chunks), len(queries))
            if self.log_retrieval:
                self.retrieval_logger.dump(
                    qid=question.get("qid", ""),
                    question=question,
                    queries=queries,
                    chunks=[],
                    stats=empty_stats,
                )
            return evidence, empty_stats

        idf, avgdl = self._build_idf(chunks)
        candidates = self._score_chunks(chunks, queries, idf, avgdl)
        selected = self._select_candidates(candidates, evidence, answer_format)
        merged = self._merge_windows(selected)
        limited = self._limit_total_chars(merged, answer_format)

        by_doc: Dict[str, List[Dict]] = defaultdict(list)
        for item in limited:
            by_doc[item["doc_id"]].append(item)

        retrieved = []
        for ev in evidence:
            items = sorted(by_doc.get(ev.doc_id, []), key=lambda item: item["start"])
            if not items:
                fallback = ev.content[: min(self._chunk_size(domain), len(ev.content))]
                retrieved.append(
                    Evidence(
                        doc_id=ev.doc_id,
                        content=f"[BM25 未命中高相关窗口，保留文档开头]\n{fallback}",
                        source=ev.source,
                        relevance_score=0.0,
                    )
                )
                continue

            parts = []
            for idx, item in enumerate(items, 1):
                query_types = ",".join(sorted(item.get("query_types", [])))
                parts.append(
                    f"[BM25片段 {idx} | 位置 {item['start']}-{item['end']} | "
                    f"score={item['score']:.2f} | query={query_types}]\n{item['content']}"
                )
            retrieved.append(
                Evidence(
                    doc_id=ev.doc_id,
                    content="\n\n".join(parts),
                    source=ev.source,
                    relevance_score=max(item["score"] for item in items),
                )
            )

        selected_sources = [
            {
                "doc_id": item["doc_id"],
                "start": item["start"],
                "end": item["end"],
                "score": round(item["score"], 4),
                "query_types": sorted(item.get("query_types", [])),
            }
            for item in limited
        ]

        sorted_scores = sorted(
            (item["score"] for item in limited), reverse=True
        )
        top1_score = sorted_scores[0] if sorted_scores else 0.0
        top2_score = sorted_scores[1] if len(sorted_scores) >= 2 else 0.0

        stats = {
            "retrieval_method": "bm25_window",
            "query_count": len(queries),
            "chunk_count": len(chunks),
            "candidate_count": len(candidates),
            "retrieved_windows": len(limited),
            "retrieved_chars": sum(len(item["content"]) for item in limited),
            "doc_coverage": len({item["doc_id"] for item in limited}),
            "max_bm25_score": max((item["score"] for item in limited), default=0.0),
            "avg_bm25_score": (
                sum(item["score"] for item in limited) / len(limited) if limited else 0.0
            ),
            "top1_score": top1_score,
            "top2_score": top2_score,
            "selected_sources": selected_sources,
            "retrieval_doc_stats": {
                doc_id: {
                    "windows": len(items),
                    "chars": sum(len(item["content"]) for item in items),
                    "max_score": max(item["score"] for item in items) if items else 0.0,
                }
                for doc_id, items in by_doc.items()
            },
        }
        if self.log_retrieval:
            self.retrieval_logger.dump(
                qid=question.get("qid", ""),
                question=question,
                queries=queries,
                chunks=limited,
                stats=stats,
            )

        return retrieved, stats

    def _empty_stats(self, method: str, chunk_count: int, query_count: int) -> Dict:
        return {
            "retrieval_method": method,
            "query_count": query_count,
            "chunk_count": chunk_count,
            "candidate_count": 0,
            "retrieved_windows": 0,
            "retrieved_chars": 0,
            "doc_coverage": 0,
            "max_bm25_score": 0.0,
            "avg_bm25_score": 0.0,
            "top1_score": 0.0,
            "top2_score": 0.0,
            "selected_sources": [],
            "retrieval_doc_stats": {},
        }

    def _build_chunks(self, evidence: List[Evidence], domain: str) -> List[Dict]:
        chunks = []
        chunk_size = self._chunk_size(domain)
        step = max(1, chunk_size - self.chunk_overlap_chars)

        for ev in evidence:
            text = ev.content or ""
            if not text:
                continue
            for start in range(0, len(text), step):
                end = min(len(text), start + chunk_size)
                content = self._expand_to_line_boundary(text, start, end).strip()
                if len(content) < self.min_chunk_chars and end < len(text):
                    continue
                tokens = self._tokenize(content)
                if not tokens:
                    continue
                chunks.append(
                    {
                        "doc_id": ev.doc_id,
                        "source": ev.source,
                        "text": text,
                        "content": content,
                        "start": start,
                        "end": end,
                        "tokens": tokens,
                        "tf": Counter(tokens),
                        "length": len(tokens),
                    }
                )
                if end >= len(text):
                    break
        return chunks

    def _build_queries(self, question: Dict) -> List[Dict]:
        q_text = question.get("question", "")
        options = question.get("options", {})
        option_values = _option_values(options)
        answer_format = question.get("answer_format", "mcq")
        domain = question.get("domain", "")

        query_specs = []
        if answer_format == "multi" and isinstance(options, dict):
            for key, value in sorted(options.items()):
                query_specs.append((f"option_{key}", f"{q_text}\n{value}"))
        else:
            merged_options = "\n".join(option_values)
            query_specs.append(("question_options", f"{q_text}\n{merged_options}"))

        full_text = f"{q_text}\n" + "\n".join(option_values)
        numeric_text = " ".join(self.WORD_RE.findall(full_text))
        if numeric_text:
            query_specs.append(("numbers", numeric_text))

        for term in self.DOMAIN_TERMS.get(domain, []):
            if term in full_text:
                query_specs.append(("domain_terms", term))

        selected_intent_terms = [
            str(term).strip()
            for term in question.get("_intent_terms", [])
            if str(term).strip()
        ]
        if selected_intent_terms:
            query_specs.append(("intent_terms", "\n".join(selected_intent_terms)))

        queries = []
        for query_type, text in query_specs:
            tokens = self._tokenize(text)
            if not tokens:
                continue
            unique_tokens = []
            seen = set()
            for token in tokens:
                if token in seen:
                    continue
                seen.add(token)
                unique_tokens.append(token)
            queries.append(
                {
                    "query_type": query_type,
                    "tokens": unique_tokens[: self.max_query_terms],
                }
            )
        return queries

    def _build_idf(self, chunks: List[Dict]) -> Tuple[Dict[str, float], float]:
        df = Counter()
        for chunk in chunks:
            df.update(set(chunk["tokens"]))
        total_docs = len(chunks)
        avgdl = sum(chunk["length"] for chunk in chunks) / max(total_docs, 1)
        idf = {
            token: math.log(1 + (total_docs - freq + 0.5) / (freq + 0.5))
            for token, freq in df.items()
        }
        return idf, avgdl

    def _score_chunks(
        self,
        chunks: List[Dict],
        queries: List[Dict],
        idf: Dict[str, float],
        avgdl: float,
    ) -> List[Dict]:
        candidates = []
        for chunk in chunks:
            score = 0.0
            query_types = set()
            matched_terms = set()
            for query in queries:
                query_score = 0.0
                for token in query["tokens"]:
                    freq = chunk["tf"].get(token, 0)
                    if not freq:
                        continue
                    denom = freq + self.k1 * (1 - self.b + self.b * chunk["length"] / max(avgdl, 1e-9))
                    term_score = idf.get(token, 0.0) * (freq * (self.k1 + 1)) / denom
                    if any(ch.isdigit() for ch in token):
                        term_score *= 1.25
                    if len(token) >= 4:
                        term_score *= 1.15
                    query_score += term_score
                    matched_terms.add(token)
                if query_score > 0:
                    query_types.add(query["query_type"])
                score += query_score

            if score <= 0:
                continue
            score += self._rule_bonus(chunk["content"], matched_terms)
            if score < self.min_score:
                continue

            expanded = self._expand_candidate(chunk, score, query_types)
            candidates.append(expanded)

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates

    def _select_candidates(
        self,
        candidates: List[Dict],
        evidence: List[Evidence],
        answer_format: str,
    ) -> List[Dict]:
        per_doc_max = self._typed_int(self.per_doc_max, answer_format, 3)
        global_top_k = self._typed_int(self.global_top_k, answer_format, 8)
        selected = []
        selected_ids = set()

        by_doc: Dict[str, List[Dict]] = defaultdict(list)
        for item in candidates:
            by_doc[item["doc_id"]].append(item)

        for ev in evidence:
            doc_items = by_doc.get(ev.doc_id, [])
            for item in doc_items[: self.per_doc_min]:
                selected.append(item)
                selected_ids.add(id(item))

        doc_counts = Counter(item["doc_id"] for item in selected)
        for item in candidates:
            if id(item) in selected_ids:
                continue
            if doc_counts[item["doc_id"]] >= per_doc_max:
                continue
            selected.append(item)
            selected_ids.add(id(item))
            doc_counts[item["doc_id"]] += 1
            if len(selected) >= global_top_k:
                break

        selected.sort(key=lambda item: item["score"], reverse=True)
        return selected[:global_top_k]

    def _merge_windows(self, items: List[Dict]) -> List[Dict]:
        by_doc: Dict[str, List[Dict]] = defaultdict(list)
        for item in items:
            by_doc[item["doc_id"]].append(item)

        merged = []
        for doc_id, doc_items in by_doc.items():
            doc_items.sort(key=lambda item: item["start"])
            current = None
            for item in doc_items:
                if current is None:
                    current = dict(item)
                    current["query_types"] = set(item.get("query_types", set()))
                    continue
                if item["start"] <= current["end"] + self.merge_gap_chars:
                    current["end"] = max(current["end"], item["end"])
                    current["score"] = max(current["score"], item["score"])
                    current["query_types"].update(item.get("query_types", set()))
                    current["content"] = current["text"][current["start"]: current["end"]].strip()
                else:
                    merged.append(current)
                    current = dict(item)
                    current["query_types"] = set(item.get("query_types", set()))
            if current is not None:
                merged.append(current)

        merged.sort(key=lambda item: item["score"], reverse=True)
        return merged

    def _limit_total_chars(self, items: List[Dict], answer_format: str) -> List[Dict]:
        max_total_chars = self._typed_int(self.max_total_chars, answer_format, 60000)
        selected = []
        used = 0
        for item in items:
            item_len = len(item["content"])
            if selected and used + item_len > max_total_chars:
                continue
            selected.append(item)
            used += item_len
        selected.sort(key=lambda item: (item["doc_id"], item["start"]))
        return selected

    def _expand_candidate(self, chunk: Dict, score: float, query_types: set) -> Dict:
        text = chunk["text"]
        start = max(0, chunk["start"] - self.expand_before_chars)
        end = min(len(text), chunk["end"] + self.expand_after_chars)
        start, end = self._line_bounds(text, start, end)
        return {
            "doc_id": chunk["doc_id"],
            "source": chunk["source"],
            "text": text,
            "start": start,
            "end": end,
            "score": score,
            "query_types": set(query_types),
            "content": text[start:end].strip(),
        }

    def _rule_bonus(self, content: str, matched_terms: set) -> float:
        bonus = 0.0
        if self.HEADING_RE.search(content):
            bonus += 0.5
        if any(any(ch.isdigit() for ch in term) for term in matched_terms):
            bonus += 0.5
        if len(matched_terms) >= 8:
            bonus += 0.5
        return bonus

    def _tokenize(self, text: str) -> List[str]:
        tokens = []
        for word in self.WORD_RE.findall(text):
            word = word.lower().strip()
            if len(word) >= 2 and word not in self.STOPWORDS:
                tokens.append(word)

        for match in self.CJK_RE.findall(text):
            clean = match.strip()
            if len(clean) < 2:
                continue
            if len(clean) <= 12 and clean not in self.STOPWORDS:
                tokens.append(clean)
            for size in (2, 3, 4, 6):
                if len(clean) < size:
                    continue
                for idx in range(0, len(clean) - size + 1):
                    term = clean[idx: idx + size]
                    if term not in self.STOPWORDS:
                        tokens.append(term)
        return tokens

    def _chunk_size(self, domain: str) -> int:
        return self._domain_int(self.chunk_size_chars, domain, 1400)

    def _expand_to_line_boundary(self, text: str, start: int, end: int) -> str:
        left, right = self._line_bounds(text, start, end)
        return text[left:right]

    def _line_bounds(self, text: str, start: int, end: int) -> Tuple[int, int]:
        left = text.rfind("\n", 0, start)
        right = text.find("\n", end)
        if left == -1:
            left = start
        if right == -1:
            right = end
        return left, right

    def _typed_int(self, value, answer_format: str, default: int) -> int:
        if isinstance(value, dict):
            return int(value.get(answer_format, value.get("default", default)))
        return int(value)

    def _domain_int(self, value, domain: str, default: int) -> int:
        if isinstance(value, dict):
            return int(value.get(domain, value.get("default", default)))
        return int(value)


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


class ReflectionPromptBuilder:
    """反思 prompt 构建器：让 LLM 对初答做二次核验。"""

    def build_prompt(
        self,
        question: Dict,
        evidence: List[Evidence],
        first_answer: str,
        context_manager: Optional[ContextManager] = None,
    ) -> str:
        """构建反思提示词。

        结构：文档 + 问题 + 选项 + 初答 + KEEP/CHANGE 指令。
        """
        context_parts = []
        for ev in evidence:
            content = ev.content
            if context_manager:
                content = context_manager.truncate_doc(content)
            context_parts.append(f"【文档 {ev.doc_id}】\n{content}")
        context = "\n\n".join(context_parts)

        options_text = "\n".join(
            [f"{k}. {v}" for k, v in question.get("options", {}).items()]
        )

        prompt = (
            "你是一位金融文档分析专家。请对下面的初答进行复核。\n\n"
            "要求：\n"
            "1. 仔细阅读文档，找出支持初答的具体证据（引用原文片段）\n"
            "2. 对每个选项逐一判断：是否有明确证据支持或反驳\n"
            "3. 如果初答正确，输出 \"KEEP {答案字母}\"\n"
            "4. 如果发现错误，输出 \"CHANGE {答案字母}\"\n"
            "5. 最终输出格式必须为 KEEP 或 CHANGE 开头，紧跟答案字母（多选按字母顺序排列）\n\n"
            f"{context}\n\n"
            f"问题：{question['question']}\n\n"
            f"选项：\n{options_text}\n\n"
            f"初答：{first_answer}\n\n"
            f"反思结论："
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


def should_reflect(retrieval_stats: Dict, reflection_config: Dict) -> Tuple[bool, str]:
    """根据 BM25 检索 stats 和 reflection 配置判断是否需要反思。

    返回 (是否触发, 触发原因)。
    原因可能是 ""（不触发）、"low_score"、"small_gap"。
    """
    if not reflection_config.get("enabled", False):
        return False, ""

    retrieved_windows = retrieval_stats.get("retrieved_windows", 0)
    if retrieved_windows == 0:
        return False, ""

    low_threshold = float(reflection_config.get("low_score_threshold", 80.0))
    top_gap_ratio = float(reflection_config.get("top_gap_ratio", 0.15))

    top1 = float(retrieval_stats.get("top1_score", 0.0))
    top2 = float(retrieval_stats.get("top2_score", 0.0))

    if top1 < low_threshold:
        return True, "low_score"

    # 仅当存在第二个候选时才比较 gap
    if top2 > 0 and top1 > 0:
        gap = (top1 - top2) / top1
        if gap < top_gap_ratio:
            return True, "small_gap"

    return False, ""


def _parse_reflection_decision(
    raw: str, first_answer: str, answer_format: str,
) -> Tuple[str, str]:
    """从反思输出中提取决策和答案。

    支持格式：
    - "KEEP A" / "keep A"  → ("KEEP", "A")
    - "CHANGE B"           → ("CHANGE", "B")
    - "KEEP ABC" (multi)   → ("KEEP", "ABC")
    - 无法解析              → ("PARSE_FAIL", first_answer)  # fail-safe
    """
    import re

    text = raw.strip().upper()
    # 匹配 KEEP 或 CHANGE 开头，后跟可选分隔符，再跟 1-4 个 A-D 字母
    match = re.search(r"\b(KEEP|CHANGE)\s+([A-D]{1,4})\b", text)
    if not match:
        return "PARSE_FAIL", first_answer

    decision = match.group(1)
    raw_letters = match.group(2)

    # 走 normalize_answer + validate 以确保格式合法
    normalized = normalize_answer(raw_letters, answer_format)
    try:
        AnswerValidator.validate(normalized, answer_format)
    except ValueError:
        return "PARSE_FAIL", first_answer

    return decision, normalized


class FinancialQAAgent:
    """金融长文本问答 Agent（Baseline No-Tool 版）"""

    @staticmethod
    def _resolve_api_key(base_url: str, model: str) -> Optional[str]:
        is_qwen_endpoint = "dashscope.aliyuncs.com" in base_url or model.lower().startswith("qwen")
        api_key = (
            os.getenv("DASHSCOPE_API_KEY")
            if is_qwen_endpoint
            else (os.getenv("LLM_API_KEY") or os.getenv("GLM_API_KEY"))
        )
        if not api_key and not is_qwen_endpoint:
            api_key = os.getenv("DASHSCOPE_API_KEY")
        return api_key

    @classmethod
    def _create_llm_client(cls, model_cfg: Dict, fallback_env: str = "FALLBACK_MODEL_NAME"):
        base_url = os.getenv(
            "API_BASE_URL",
            model_cfg.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        )
        model = os.getenv("MODEL_NAME", model_cfg.get("name", "qwen-plus"))
        return LLMClient(
            api_key=cls._resolve_api_key(base_url, model),
            base_url=base_url,
            model=model,
            temperature=model_cfg.get("temperature", 0.0),
            fallback_model=os.getenv(fallback_env, model_cfg.get("fallback_name", "")),
        )

    @classmethod
    def _create_intent_llm_client(cls, intent_cfg: Dict, default_llm):
        model = intent_cfg.get("model")
        if not model:
            return default_llm

        base_url = intent_cfg.get("api_base", "https://open.bigmodel.cn/api/paas/v4")
        return LLMClient(
            api_key=cls._resolve_api_key(base_url, model),
            base_url=base_url,
            model=model,
            temperature=intent_cfg.get("temperature", 0.0),
            fallback_model=intent_cfg.get("fallback_name", ""),
        )

    def __init__(self, config: Optional[Dict] = None):
        self.config = config or load_config()
        self.context_manager = ContextManager(
            max_chars=self.config.get("model", {}).get("max_context_tokens", 320000),
            max_doc_chars=self.config.get("retrieval", {}).get("max_doc_chars", 16000),
        )
        retrieval_cfg = self.config.get("retrieval", {})
        self.retrieval_enabled = retrieval_cfg.get("enabled", True)
        retrieval_method = retrieval_cfg.get("method", "bm25")
        if retrieval_method == "keyword_window":
            self.retriever = RollingWindowRetriever(retrieval_cfg)
        else:
            self.retriever = BM25Retriever(retrieval_cfg)
        intent_cfg = retrieval_cfg.get("intent_terms", {}) or {}
        self.intent_selector = IntentTermSelector(
            enabled=bool(intent_cfg.get("enabled", False)),
            max_terms=int(intent_cfg.get("max_terms", 8)),
        )
        self.prompt_builder = PromptBuilder()
        self.reflection_prompt_builder = ReflectionPromptBuilder()
        reflection_cfg = self.config.get("reflection", {}) or {}
        self.reflection_enabled = bool(reflection_cfg.get("enabled", False))
        self.reflection_config = {
            "enabled": self.reflection_enabled,
            "low_score_threshold": float(reflection_cfg.get("low_score_threshold", 80.0)),
            "top_gap_ratio": float(reflection_cfg.get("top_gap_ratio", 0.15)),
            "log_decisions": bool(reflection_cfg.get("log_decisions", True)),
        }
        self.memory = MemoryState()

        model_cfg = self.config.get("model", {})
        self.llm = self._create_llm_client(model_cfg)
        self.intent_llm = self._create_intent_llm_client(intent_cfg, self.llm)

    def _read_document(self, domain: str, doc_id: str) -> str:
        """读取已解析的文档内容，尝试多个数据路径"""
        roots = [
            Path(self.config["data"].get("markdown_dir", "data/merged_md")),
            Path("data/merged_md"),
        ]

        for root in roots:
            parsed_dir = root / domain / str(doc_id)
            if parsed_dir.is_dir():
                content = self._read_document_dir(parsed_dir)
                if content:
                    return content

            direct_file = self._find_document_file(root / domain, str(doc_id))
            if direct_file:
                return self._read_document_file(direct_file)

            domain_dir = root / domain
            if domain_dir.exists():
                for child in domain_dir.iterdir():
                    if child.is_dir() and child.name.lstrip("0") == str(doc_id).lstrip("0"):
                        content = self._read_document_dir(child)
                        if content:
                            return content

        return f"[文档 {doc_id} 未找到]"

    def _read_document_dir(self, doc_dir: Path) -> str:
        pages = sorted(doc_dir.glob("page_*.md"))
        if not pages:
            pages = sorted(
                path for path in doc_dir.iterdir()
                if path.is_file() and path.suffix.lower() in {".md", ".txt", ".html", ".htm"}
            )

        texts = []
        for page in pages:
            content = self._read_document_file(page)
            if content:
                texts.append(content)
        return "\n\n".join(texts)

    def _find_document_file(self, domain_dir: Path, doc_id: str) -> Optional[Path]:
        if not domain_dir.exists():
            return None

        suffixes = (".md", ".txt", ".html", ".htm")
        candidates = []
        for suffix in suffixes:
            candidates.append(domain_dir / f"{doc_id}{suffix}")
        if doc_id.isdigit():
            normalized = str(int(doc_id))
            for suffix in suffixes:
                candidates.append(domain_dir / f"{normalized}{suffix}")

        for candidate in candidates:
            if candidate.is_file():
                return candidate

        normalized_doc_id = doc_id.lstrip("0")
        for path in domain_dir.rglob("*"):
            if (
                path.is_file()
                and path.suffix.lower() in suffixes
                and path.stem.lstrip("0") == normalized_doc_id
            ):
                return path
        return None

    def _read_document_file(self, path: Path) -> str:
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return ""
        if path.suffix.lower() in {".html", ".htm"}:
            parser = HTMLTextExtractor()
            parser.feed(content)
            return parser.get_text()
        return content

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

    def _retrieve_evidence_with_intent_terms(
        self,
        question: Dict,
        evidence: List[Evidence],
    ) -> Tuple[Dict, List[Evidence], Dict]:
        active_question = dict(question)
        intent_terms = []
        intent_selector = getattr(self, "intent_selector", IntentTermSelector(enabled=False))
        if isinstance(self.retriever, BM25Retriever) or not isinstance(intent_selector, IntentTermSelector):
            intent_terms = intent_selector.select(
                active_question,
                getattr(self, "intent_llm", self.llm),
                BM25Retriever.INTENT_TERMS,
            )
            if intent_terms:
                active_question["_intent_terms"] = intent_terms

        retrieved_evidence, retrieval_stats = self.retriever.retrieve(active_question, evidence)
        retrieval_stats = dict(retrieval_stats)
        retrieval_stats["intent_terms"] = intent_terms
        retrieval_stats["intent_term_count"] = len(intent_terms)

        usage = getattr(intent_selector, "last_usage", None)
        if usage:
            retrieval_stats["intent_prompt_tokens"] = getattr(usage, "prompt_tokens", 0)
            retrieval_stats["intent_completion_tokens"] = getattr(usage, "completion_tokens", 0)
            retrieval_stats["intent_total_tokens"] = getattr(usage, "total_tokens", 0)
        return active_question, retrieved_evidence, retrieval_stats

    def answer_question(
        self,
        question: Dict,
    ) -> Tuple[str, List[Evidence], Dict]:
        """回答单个问题，返回 (答案, 证据, Token 统计)"""
        # 1. 加载证据
        evidence = self._load_evidence(question)
        active_question = question
        retrieval_stats = {
            "retrieval_method": "head_truncate",
            "query_count": 0,
            "chunk_count": 0,
            "candidate_count": 0,
            "retrieved_windows": 0,
            "retrieved_chars": 0,
            "doc_coverage": 0,
            "max_bm25_score": 0.0,
            "avg_bm25_score": 0.0,
            "selected_sources": [],
            "retrieval_doc_stats": {},
            "intent_terms": [],
            "intent_term_count": 0,
        }
        if self.retrieval_enabled:
            active_question, evidence, retrieval_stats = self._retrieve_evidence_with_intent_terms(
                question,
                evidence,
            )

        local_context = ContextManager(
            max_chars=self.context_manager.max_chars,
            max_doc_chars=self.context_manager.max_doc_chars,
        )

        # 2. 构建 prompt
        prompt = self.prompt_builder.build_prompt(active_question, evidence, local_context)
        prompt = local_context.truncate(prompt)

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
            old_max = local_context.max_doc_chars
            local_context.max_doc_chars = max(1000, old_max // 2)
            prompt = self.prompt_builder.build_prompt(active_question, evidence, local_context)
            prompt = local_context.truncate(prompt)
            response = self.llm.chat([{"role": "user", "content": prompt}], max_tokens=4096)
            local_context.max_doc_chars = old_max

        # 4. 从内容中提取答案
        first_answer = self._parse_answer(response, active_question.get("answer_format", "mcq"))
        answer = first_answer

        reflected = False
        reflection_decision = ""
        reflection_trigger_reason = ""
        total_prompt = response.usage.prompt_tokens + retrieval_stats.get("intent_prompt_tokens", 0)
        total_completion = response.usage.completion_tokens + retrieval_stats.get("intent_completion_tokens", 0)
        total_tokens = response.usage.total_tokens + retrieval_stats.get("intent_total_tokens", 0)

        # 5. 反思环节：BM25 低置信度时让 LLM 自评修正
        if self.reflection_enabled:
            triggered, reason = should_reflect(retrieval_stats, self.reflection_config)
            reflection_trigger_reason = reason
            if triggered:
                try:
                    reflect_prompt = self.reflection_prompt_builder.build_prompt(
                        active_question, evidence, first_answer, local_context,
                    )
                    reflect_prompt = local_context.truncate(reflect_prompt)
                    reflect_response = self.llm.chat(
                        [{"role": "user", "content": reflect_prompt}], max_tokens=4096,
                    )
                    decision, parsed_answer = _parse_reflection_decision(
                        reflect_response.content, first_answer,
                        active_question.get("answer_format", "mcq"),
                    )
                    reflected = True
                    reflection_decision = decision
                    if decision != "PARSE_FAIL":
                        answer = parsed_answer
                    # PARSE_FAIL 时保留首轮 answer（fail-safe）

                    total_prompt += reflect_response.usage.prompt_tokens
                    total_completion += reflect_response.usage.completion_tokens
                    total_tokens += reflect_response.usage.total_tokens
                except Exception as reflect_err:
                    # 反思调用失败时保留 first_answer，不丢弃首轮结果
                    print(f"  [reflection_failed] {reflect_err}")
                    reflected = True
                    reflection_decision = "LLM_ERROR"

        token_usage = {
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "total_tokens": total_tokens,
            "retries": retry_count,
            "reflected": reflected,
            "first_answer": first_answer,
            "reflection_decision": reflection_decision,
            "reflection_trigger_reason": reflection_trigger_reason,
            **retrieval_stats,
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
                print(f"  -> answer: {answer} | Tokens: {token_usage['total_tokens']}")
            except Exception as e:
                print(f"  ERROR: {e}")
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
