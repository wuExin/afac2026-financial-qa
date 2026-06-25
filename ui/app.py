"""Streamlit 入口:streamlit run ui/app.py"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from ui.data_index import DOMAINS, build_index
from ui import views

DATA_ROOT = Path(__file__).resolve().parents[1] / "data"


def _label(entry) -> str:
    tags = []
    if not entry.has_pdf:
        tags.append("无PDF")
    if not entry.has_md:
        tags.append("无MD")
    return entry.doc_id + (f"  ⚠️{'/'.join(tags)}" if tags else "")


def main() -> None:
    st.set_page_config(page_title="解析质量检查", layout="wide")

    if not DATA_ROOT.is_dir():
        st.error(f"未找到 data 目录:{DATA_ROOT}")
        return

    index = build_index(DATA_ROOT)

    with st.sidebar:
        st.header("导航")
        domain = st.selectbox("领域", DOMAINS)
        entries = index.get(domain, [])
        if not entries:
            st.warning("该领域下没有文档")
            return
        entry = st.selectbox(
            "文档", entries, format_func=_label
        )

    mode = st.radio(
        "模式", ["📄 PDF", "📝 Markdown"], horizontal=True, label_visibility="collapsed"
    )

    if mode == "📄 PDF":
        views.render_pdf_only(DATA_ROOT, domain, entry)
    else:
        layout = st.radio(
            "布局", ["单栏", "双栏(对比 PDF)"], horizontal=True
        )
        if layout == "单栏":
            views.render_md_only(DATA_ROOT, domain, entry)
        else:
            views.render_split(DATA_ROOT, domain, entry)


if __name__ == "__main__":
    main()
