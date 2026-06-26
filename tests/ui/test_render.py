import base64

from ui.render import (
    PDF_INLINE_MAX_BYTES,
    build_compare_html,
    build_md_html,
    build_pdf_html,
    is_pdf_too_large,
    load_compare_template,
    load_md_template,
    load_pdf_template,
    load_search_js,
    md_to_html,
    pdf_to_base64,
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


def test_load_compare_template_has_placeholders():
    tpl = load_compare_template()
    assert "{{MD_HTML}}" in tpl
    assert "{{PDF_B64}}" in tpl
    assert "pdf.js" in tpl or "pdfjs" in tpl


def test_build_compare_html_substitutes():
    tpl = "left={{MD_HTML}} pdf={{PDF_B64}}"
    out = build_compare_html("<p>hi</p>", "QUJD", template=tpl)
    assert out == "left=<p>hi</p> pdf=QUJD"
    assert "{{MD_HTML}}" not in out
    assert "{{PDF_B64}}" not in out


def test_load_pdf_template_has_placeholder():
    tpl = load_pdf_template()
    assert "{{PDF_B64}}" in tpl
    assert "pdf.js" in tpl or "pdfjs" in tpl


def test_build_pdf_html_substitutes():
    tpl = "data={{PDF_B64}}"
    out = build_pdf_html("QUJD", template=tpl)
    assert out == "data=QUJD"
    assert "{{PDF_B64}}" not in out


def test_load_search_js_nonempty_with_markers():
    js = load_search_js()
    assert "afac-search-engine" in js
    assert "searchcontentready" in js
    assert "data-search-root" in js


def test_load_md_template_has_placeholders():
    tpl = load_md_template()
    assert "{{MD_HTML}}" in tpl
    assert "{{SEARCH_JS}}" in tpl
    assert "data-search-root" in tpl


def test_build_md_html_substitutes():
    out = build_md_html("<p>hi</p>", template="md={{MD_HTML}} js={{SEARCH_JS}}", search_js="ENGINE")
    assert out == "md=<p>hi</p> js=ENGINE"
    assert "{{MD_HTML}}" not in out
    assert "{{SEARCH_JS}}" not in out


def test_load_pdf_template_has_search_and_textlayer():
    tpl = load_pdf_template()
    assert "{{SEARCH_JS}}" in tpl
    assert "data-search-root" in tpl
    assert "renderTextLayer" in tpl
    assert "searchcontentready" in tpl
    assert "PDF 加载失败" in tpl


def test_load_compare_template_has_search_and_textlayer():
    tpl = load_compare_template()
    assert "{{SEARCH_JS}}" in tpl
    assert tpl.count("data-search-root") == 2
    assert 'data-search-label="MD"' in tpl
    assert 'data-search-label="PDF"' in tpl
    assert "renderTextLayer" in tpl
    assert "searchcontentready" in tpl
    assert "PDF 加载失败" in tpl
