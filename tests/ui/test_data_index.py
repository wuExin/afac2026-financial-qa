from pathlib import Path

from ui.data_index import DOMAINS, DocEntry, build_index, pdf_path, md_path


def _make_tree(root: Path):
    # insurance: 1 有 pdf+md, 2 只有 pdf, 10 只有 md(验证数值排序)
    (root / "pdf" / "insurance").mkdir(parents=True)
    (root / "markdown" / "insurance").mkdir(parents=True)
    (root / "pdf" / "insurance" / "1.pdf").write_bytes(b"%PDF-1")
    (root / "markdown" / "insurance" / "1.md").write_text("# one", encoding="utf-8")
    (root / "pdf" / "insurance" / "2.pdf").write_bytes(b"%PDF-2")
    (root / "markdown" / "insurance" / "10.md").write_text("# ten", encoding="utf-8")


def test_domains_constant():
    assert DOMAINS == [
        "insurance",
        "regulatory",
        "financial_contracts",
        "financial_reports",
        "research",
    ]


def test_build_index_union_and_numeric_sort(tmp_path):
    _make_tree(tmp_path)
    index = build_index(tmp_path)
    ins = index["insurance"]
    assert [e.doc_id for e in ins] == ["1", "2", "10"]
    assert ins[0] == DocEntry(doc_id="1", has_pdf=True, has_md=True)
    assert ins[1] == DocEntry(doc_id="2", has_pdf=True, has_md=False)
    assert ins[2] == DocEntry(doc_id="10", has_pdf=False, has_md=True)


def test_build_index_missing_domain_dirs_empty(tmp_path):
    index = build_index(tmp_path)
    assert index == {d: [] for d in DOMAINS}


def test_path_helpers(tmp_path):
    assert pdf_path(tmp_path, "insurance", "1") == tmp_path / "pdf" / "insurance" / "1.pdf"
    assert md_path(tmp_path, "insurance", "1") == tmp_path / "markdown" / "insurance" / "1.md"
