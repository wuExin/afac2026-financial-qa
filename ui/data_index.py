"""扫描 data/ 目录,构建 {domain: [DocEntry]} 索引。唯一访问文件系统的模块。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DOMAINS: list[str] = [
    "insurance",
    "regulatory",
    "financial_contracts",
    "financial_reports",
    "research",
]


@dataclass(frozen=True)
class DocEntry:
    doc_id: str
    has_pdf: bool
    has_md: bool


def pdf_path(data_root: Path, domain: str, doc_id: str) -> Path:
    return Path(data_root) / "pdf" / domain / f"{doc_id}.pdf"


def md_path(data_root: Path, domain: str, doc_id: str) -> Path:
    return Path(data_root) / "markdown" / domain / f"{doc_id}.md"


def _ids_in(dir_path: Path, suffix: str) -> set[str]:
    if not dir_path.is_dir():
        return set()
    return {p.stem for p in dir_path.glob(f"*{suffix}")}


def _sort_key(doc_id: str) -> tuple[int, int | str]:
    # 数值 id 按数值排序,非数值 id 排后面按字符串排序
    return (0, int(doc_id)) if doc_id.isdigit() else (1, doc_id)


def build_index(data_root: Path) -> dict[str, list[DocEntry]]:
    data_root = Path(data_root)
    index: dict[str, list[DocEntry]] = {}
    for domain in DOMAINS:
        pdf_ids = _ids_in(data_root / "pdf" / domain, ".pdf")
        md_ids = _ids_in(data_root / "markdown" / domain, ".md")
        all_ids = sorted(pdf_ids | md_ids, key=_sort_key)
        index[domain] = [
            DocEntry(doc_id=i, has_pdf=i in pdf_ids, has_md=i in md_ids)
            for i in all_ids
        ]
    return index
