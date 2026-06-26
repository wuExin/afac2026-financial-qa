"""AppTest smoke test: verify the three view functions run without raising."""
import pathlib
import os

from streamlit.testing.v1 import AppTest

# Inject the repo root so the self-contained script can import ui.*
_REPO_ROOT = str(pathlib.Path(os.getcwd()).resolve())

_SCRIPT = f"""
import sys
sys.path.insert(0, {repr(_REPO_ROOT)})

import tempfile, pathlib
from ui.data_index import DocEntry
from ui import views

root = pathlib.Path(tempfile.mkdtemp())
(root / "markdown" / "insurance").mkdir(parents=True)
(root / "pdf" / "insurance").mkdir(parents=True)
(root / "markdown" / "insurance" / "1.md").write_text(
    "# 标题\\n\\nsome searchable text 关键词", encoding="utf-8"
)
(root / "pdf" / "insurance" / "1.pdf").write_bytes(b"%PDF-1.4 fixture bytes")
entry = DocEntry(doc_id="1", has_pdf=True, has_md=True)
views.render_md_only(root, "insurance", entry)
views.render_pdf_only(root, "insurance", entry)
views.render_split(root, "insurance", entry)
"""


def test_three_views_render_without_exception():
    at = AppTest.from_string(_SCRIPT)
    at.run()
    # at.exception is an ElementList — empty means no uncaught exceptions
    assert not at.exception, f"views raised: {list(at.exception)}"
