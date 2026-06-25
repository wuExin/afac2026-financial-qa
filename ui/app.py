"""Streamlit 入口:streamlit run ui/app.py"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui.data_index import DOMAINS, DocEntry, build_index
from ui.questions import load_questions
from ui import views

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def _label(entry: DocEntry) -> str:
    tags = []
    if not entry.has_pdf:
        tags.append("无PDF")
    if not entry.has_md:
        tags.append("无MD")
    return entry.doc_id + (f"  ⚠️{'/'.join(tags)}" if tags else "")


def _render_doc(domain: str, entry: DocEntry, mode: str, layout: str) -> None:
    if mode == "📄 PDF":
        views.render_pdf_only(DATA_ROOT, domain, entry)
    elif layout == "单栏":
        views.render_md_only(DATA_ROOT, domain, entry)
    else:
        views.render_split(DATA_ROOT, domain, entry)


def _question_view(domain: str, index: dict, mode: str, layout: str) -> None:
    questions = load_questions(DATA_ROOT, domain)
    if not questions:
        st.warning("该领域暂无题目")
        return

    def _opt_label(i: int) -> str:
        q = questions[i]
        stem = q.question[:30] + ("…" if len(q.question) > 30 else "")
        return f"{i + 1}. [{q.qid}] {stem}"

    # 下拉选题,按 domain 独立保存选择
    idx = st.selectbox(
        f"题目(共 {len(questions)} 题)",
        range(len(questions)),
        format_func=_opt_label,
        key=f"qsel_{domain}",
    )

    q = questions[idx]
    views.render_question_panel(q)
    st.divider()

    if not q.doc_ids:
        st.info("该题未关联文档")
        return

    lookup = {e.doc_id: e for e in index.get(domain, [])}
    tabs = st.tabs(list(q.doc_ids))
    for tab, doc_id in zip(tabs, q.doc_ids):
        with tab:
            entry = lookup.get(doc_id) or DocEntry(
                doc_id=doc_id, has_pdf=False, has_md=False
            )
            _render_doc(domain, entry, mode, layout)


def main() -> None:
    st.set_page_config(page_title="解析质量检查", layout="wide")

    if not DATA_ROOT.is_dir():
        st.error(f"未找到 data 目录:{DATA_ROOT}")
        return

    index = build_index(DATA_ROOT)

    with st.sidebar:
        st.header("导航")
        domain = st.selectbox("领域", DOMAINS)
        browse = st.radio("浏览模式", ["按题目", "按文档"])
        entry = None
        if browse == "按文档":
            entries = index.get(domain, [])
            if entries:
                entry = st.selectbox("文档", entries, format_func=_label)

    mode = st.radio(
        "模式", ["📄 PDF", "📝 Markdown"], horizontal=True, label_visibility="collapsed"
    )
    layout = "单栏"
    if mode == "📝 Markdown":
        layout = st.radio("布局", ["单栏", "双栏(对比 PDF)"], horizontal=True)

    if browse == "按题目":
        _question_view(domain, index, mode, layout)
    else:
        if entry is None:
            st.warning("该领域下没有文档")
            return
        _render_doc(domain, entry, mode, layout)


if __name__ == "__main__":
    main()
