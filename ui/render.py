"""纯函数:markdown→HTML、PDF→base64、内联大小阈值判断。无副作用,可单测。"""
from __future__ import annotations

import base64
from pathlib import Path

import markdown as _md

PDF_INLINE_MAX_BYTES: int = 8 * 1024 * 1024


def md_to_html(md_text: str) -> str:
    return _md.markdown(md_text, extensions=["tables", "fenced_code"])


def pdf_to_base64(pdf_bytes: bytes) -> str:
    return base64.b64encode(pdf_bytes).decode("ascii")


def is_pdf_too_large(pdf_bytes: bytes, max_bytes: int = PDF_INLINE_MAX_BYTES) -> bool:
    return len(pdf_bytes) > max_bytes


_TEMPLATE_PATH = Path(__file__).with_name("compare_component.html")
_PDF_TEMPLATE_PATH = Path(__file__).with_name("pdf_component.html")


def load_compare_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def build_compare_html(md_html: str, pdf_b64: str, template: str | None = None) -> str:
    tpl = template if template is not None else load_compare_template()
    return tpl.replace("{{MD_HTML}}", md_html).replace("{{PDF_B64}}", pdf_b64)


def load_pdf_template() -> str:
    return _PDF_TEMPLATE_PATH.read_text(encoding="utf-8")


_SEARCH_JS_PATH = Path(__file__).with_name("search.js")


def load_search_js() -> str:
    return _SEARCH_JS_PATH.read_text(encoding="utf-8")


def build_pdf_html(pdf_b64: str, template: str | None = None) -> str:
    tpl = template if template is not None else load_pdf_template()
    return tpl.replace("{{PDF_B64}}", pdf_b64)
