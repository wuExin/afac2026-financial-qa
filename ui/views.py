"""Streamlit 三种视图:仅 PDF / 仅 Markdown / 双栏对比。"""
from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from ui.data_index import DocEntry, md_path, pdf_path
from ui.questions import Question
from ui.render import (
    build_compare_html,
    build_md_html,
    build_pdf_html,
    is_pdf_too_large,
    md_to_html,
    pdf_to_base64,
)

_COMPONENT_HEIGHT = 900


def render_question_panel(q: Question) -> None:
    """顶部题目区:题号/题型、题干、选项 A-D、正确答案、关联文档。"""
    meta = f"`{q.qid}`" + (f" · {q.qtype}" if q.qtype else "")
    st.markdown(meta)
    st.markdown(f"#### {q.question}")
    for key, text in q.options.items():
        st.markdown(f"- **{key}.** {text}")
    if q.answer:
        st.success(f"正确答案:{q.answer}")
    if q.doc_ids:
        st.caption("关联文档:" + " · ".join(q.doc_ids))


def _read_md(data_root: Path, domain: str, doc_id: str) -> str:
    return md_path(data_root, domain, doc_id).read_text(encoding="utf-8")


def _read_pdf(data_root: Path, domain: str, doc_id: str) -> bytes:
    return pdf_path(data_root, domain, doc_id).read_bytes()


def _pdf_pane(pdf_bytes: bytes, height: int = _COMPONENT_HEIGHT) -> None:
    # Render via pdf.js (canvas), not a data: URI iframe — Chrome blocks
    # navigating an iframe to a data:application/pdf URL.
    html = build_pdf_html(pdf_to_base64(pdf_bytes))
    components.html(html, height=height, scrolling=False)


def render_pdf_only(data_root: Path, domain: str, entry: DocEntry) -> None:
    if not entry.has_pdf:
        st.warning("该文档缺少 PDF")
        return
    _pdf_pane(_read_pdf(data_root, domain, entry.doc_id))


def render_md_only(data_root: Path, domain: str, entry: DocEntry) -> None:
    if not entry.has_md:
        st.warning("该文档缺少 markdown")
        return
    md_html = md_to_html(_read_md(data_root, domain, entry.doc_id))
    components.html(build_md_html(md_html), height=_COMPONENT_HEIGHT, scrolling=False)


def render_split(data_root: Path, domain: str, entry: DocEntry) -> None:
    if not entry.has_md:
        st.warning("该文档缺少 markdown,无法对比")
        return
    if not entry.has_pdf:
        st.warning("该文档缺少 PDF,仅显示 markdown")
        render_md_only(data_root, domain, entry)
        return
    pdf_bytes = _read_pdf(data_root, domain, entry.doc_id)
    if is_pdf_too_large(pdf_bytes):
        st.info("PDF 超过 8MB,降级为并排显示(不同步滚动)")
        col_md, col_pdf = st.columns(2)
        with col_md:
            st.markdown(
                md_to_html(_read_md(data_root, domain, entry.doc_id)),
                unsafe_allow_html=True,
            )
        with col_pdf:
            _pdf_pane(pdf_bytes)
        return
    md_html = md_to_html(_read_md(data_root, domain, entry.doc_id))
    html = build_compare_html(md_html, pdf_to_base64(pdf_bytes))
    components.html(html, height=_COMPONENT_HEIGHT, scrolling=False)
