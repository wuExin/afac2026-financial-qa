import base64

from ui.render import (
    PDF_INLINE_MAX_BYTES,
    md_to_html,
    pdf_to_base64,
    is_pdf_too_large,
)


def test_md_to_html_pipe_table():
    html = md_to_html("| a | b |\n|---|---|\n| 1 | 2 |")
    assert "<table>" in html
    assert "<td>1</td>" in html


def test_md_to_html_passthrough_raw_table():
    raw = "<table><tr><td>x</td></tr></table>"
    html = md_to_html(raw)
    assert "<td>x</td>" in html


def test_pdf_to_base64_roundtrip():
    data = b"%PDF-1.7 hello"
    b64 = pdf_to_base64(data)
    assert base64.b64decode(b64) == data


def test_is_pdf_too_large_threshold():
    assert is_pdf_too_large(b"x" * (PDF_INLINE_MAX_BYTES + 1)) is True
    assert is_pdf_too_large(b"x" * 10) is False
