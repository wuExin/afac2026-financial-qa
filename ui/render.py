"""纯函数:markdown→HTML、PDF→base64、内联大小阈值判断。无副作用,可单测。"""
from __future__ import annotations

import base64

import markdown as _md

PDF_INLINE_MAX_BYTES: int = 8 * 1024 * 1024


def md_to_html(md_text: str) -> str:
    return _md.markdown(md_text, extensions=["tables", "fenced_code"])


def pdf_to_base64(pdf_bytes: bytes) -> str:
    return base64.b64encode(pdf_bytes).decode("ascii")


def is_pdf_too_large(pdf_bytes: bytes, max_bytes: int = PDF_INLINE_MAX_BYTES) -> bool:
    return len(pdf_bytes) > max_bytes
