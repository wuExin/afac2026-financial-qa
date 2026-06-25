"""Streamlit 三种视图:仅 PDF / 仅 Markdown / 双栏对比。"""
from __future__ import annotations

from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

from ui.data_index import DocEntry, md_path, pdf_path
from ui.render import (
    build_compare_html,
    is_pdf_too_large,
    md_to_html,
    pdf_to_base64,
)

_COMPONENT_HEIGHT = 900


def _read_md(data_root: Path, domain: str, doc_id: str) -> str:
    return md_path(data_root, domain, doc_id).read_text(encoding="utf-8")


def _read_pdf(data_root: Path, domain: str, doc_id: str) -> bytes:
    return pdf_path(data_root, domain, doc_id).read_bytes()


def _pdf_iframe(pdf_bytes: bytes, height: int = _COMPONENT_HEIGHT) -> None:
    b64 = pdf_to_base64(pdf_bytes)
    components.html(
        f'<iframe src="data:application/pdf;base64,{b64}" '
        f'width="100%" height="{height}px" style="border:none;"></iframe>',
        height=height + 10,
    )


def render_pdf_only(data_root: Path, domain: str, entry: DocEntry) -> None:
    if not entry.has_pdf:
        st.warning("该文档缺少 PDF")
        return
    _pdf_iframe(_read_pdf(data_root, domain, entry.doc_id))


def render_md_only(data_root: Path, domain: str, entry: DocEntry) -> None:
    if not entry.has_md:
        st.warning("该文档缺少 markdown")
        return
    st.markdown(
        md_to_html(_read_md(data_root, domain, entry.doc_id)),
        unsafe_allow_html=True,
    )


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
        st.info("PDF 超过 8MB,降级为原生预览(不同步滚动)")
        col_md, col_pdf = st.columns(2)
        with col_md:
            st.markdown(
                md_to_html(_read_md(data_root, domain, entry.doc_id)),
                unsafe_allow_html=True,
            )
        with col_pdf:
            _pdf_iframe(pdf_bytes)
        return
    md_html = md_to_html(_read_md(data_root, domain, entry.doc_id))
    html = build_compare_html(md_html, pdf_to_base64(pdf_bytes))
    components.html(html, height=_COMPONENT_HEIGHT, scrolling=False)
