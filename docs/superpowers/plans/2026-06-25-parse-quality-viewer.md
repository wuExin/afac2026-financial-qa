# 解析质量检查查看器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建一个本地 Streamlit 工具,把 mineru 解析出的 markdown 与源 PDF 并排对比(比例同步滚动),用于检查解析质量。

**Architecture:** Streamlit 作外壳(侧边栏选领域/文档 + 顶部 PDF/Markdown 模式切换)。文件系统访问集中在 `data_index.py`;纯函数(markdown→HTML、PDF→base64、阈值判断、对比 HTML 拼装)放 `render.py`,可单测;`views.py` 负责三种 Streamlit 视图;双栏对比是一个 `st.components.v1.html` 嵌入的自包含 HTML/JS,用 pdf.js 渲染 PDF 并按滚动百分比同步。

**Tech Stack:** Python 3.12, Streamlit, python-markdown, pdf.js(前端 CDN), pytest 9。

## Global Constraints

- 数据布局:`data/pdf/<domain>/<id>.pdf` ↔ `data/markdown/<domain>/<id>.md`,同 id 一一对应。
- 5 个领域:`insurance`、`regulatory`、`financial_contracts`、`financial_reports`、`research`。
- 工具代码放仓库根下新目录 `ui/`,**不得改动 `src/` 的赛题代码**。
- 测试放 `tests/ui/`,用 pytest 运行(`python -m pytest`)。
- PDF 内联阈值:`8 * 1024 * 1024` 字节(8MB),超限走原生 iframe 降级、不同步。
- markdown 内的远程图片(`cdn-mineru.openxlab.org.cn`)直接加载,不做处理。
- 同步策略:比例同步,markdown 驱动 PDF;无版面映射数据,不做逐行精确同步。
- 提交信息使用英文,结尾附 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。

## File Structure

```
ui/
├── __init__.py
├── data_index.py            # 扫描 data/,构建索引,路径与缺失标注(唯一碰文件系统)
├── render.py                # 纯函数:md->html / pdf->data / 阈值 / 拼对比 HTML
├── views.py                 # Streamlit 视图:render_pdf_only / render_md_only / render_split
├── app.py                   # Streamlit 入口:侧边栏导航 + 模式切换 + 分发
└── compare_component.html   # 双栏对比模板(pdf.js + 比例同步 JS)
tests/
└── ui/
    ├── __init__.py
    ├── test_data_index.py
    └── test_render.py
```

---

### Task 1: 依赖与 data_index 索引模块

**Files:**
- Create: `ui/__init__.py`
- Create: `ui/data_index.py`
- Create: `tests/ui/__init__.py`
- Create: `tests/ui/test_data_index.py`
- Modify: `requirements.txt`(追加 UI 工具依赖)

**Interfaces:**
- Produces:
  - `DOMAINS: list[str]` —— 5 个领域(固定顺序)。
  - `@dataclass DocEntry(doc_id: str, has_pdf: bool, has_md: bool)`
  - `build_index(data_root: Path) -> dict[str, list[DocEntry]]` —— 每个领域下 DocEntry 列表,按 id 数值升序;id 为 pdf 与 md 文件名(去扩展名)的并集。
  - `pdf_path(data_root: Path, domain: str, doc_id: str) -> Path`
  - `md_path(data_root: Path, domain: str, doc_id: str) -> Path`

- [ ] **Step 1: 写失败测试**

`tests/ui/__init__.py` 内容为空文件。

`tests/ui/test_data_index.py`:

```python
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
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/ui/test_data_index.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'ui'` 或导入错误。

- [ ] **Step 3: 写实现**

`ui/__init__.py` 内容为空文件。

`ui/data_index.py`:

```python
"""扫描 data/ 目录,构建 {domain: [DocEntry]} 索引。唯一访问文件系统的模块。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DOMAINS: list[str] = [
    "insurance",
    "regulatory",
    "financial_contracts",
    "financial_reports",
    "research",
]


@dataclass(frozen=True)
class DocEntry:
    doc_id: str
    has_pdf: bool
    has_md: bool


def pdf_path(data_root: Path, domain: str, doc_id: str) -> Path:
    return Path(data_root) / "pdf" / domain / f"{doc_id}.pdf"


def md_path(data_root: Path, domain: str, doc_id: str) -> Path:
    return Path(data_root) / "markdown" / domain / f"{doc_id}.md"


def _ids_in(dir_path: Path, suffix: str) -> set[str]:
    if not dir_path.is_dir():
        return set()
    return {p.stem for p in dir_path.glob(f"*{suffix}")}


def _sort_key(doc_id: str) -> tuple[int, int | str]:
    # 数值 id 按数值排序,非数值 id 排后面按字符串排序
    return (0, int(doc_id)) if doc_id.isdigit() else (1, doc_id)


def build_index(data_root: Path) -> dict[str, list[DocEntry]]:
    data_root = Path(data_root)
    index: dict[str, list[DocEntry]] = {}
    for domain in DOMAINS:
        pdf_ids = _ids_in(data_root / "pdf" / domain, ".pdf")
        md_ids = _ids_in(data_root / "markdown" / domain, ".md")
        all_ids = sorted(pdf_ids | md_ids, key=_sort_key)
        index[domain] = [
            DocEntry(doc_id=i, has_pdf=i in pdf_ids, has_md=i in md_ids)
            for i in all_ids
        ]
    return index
```

`requirements.txt` 末尾追加:

```
# UI 解析质量检查工具(ui/ 下,baseline 运行不需要)
streamlit>=1.30.0
markdown>=3.5
```

- [ ] **Step 4: 安装依赖并运行测试**

Run: `pip install "streamlit>=1.30.0" "markdown>=3.5"`
Run: `python -m pytest tests/ui/test_data_index.py -v`
Expected: PASS(4 passed)。

- [ ] **Step 5: 提交**

```bash
git add ui/__init__.py ui/data_index.py tests/ui/__init__.py tests/ui/test_data_index.py requirements.txt
git commit -m "feat(ui): add data_index for scanning pdf/markdown corpus

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: render 纯函数(md→HTML / PDF→base64 / 阈值)

**Files:**
- Create: `ui/render.py`
- Create: `tests/ui/test_render.py`

**Interfaces:**
- Consumes: 无(纯函数模块)。
- Produces:
  - `PDF_INLINE_MAX_BYTES: int = 8 * 1024 * 1024`
  - `md_to_html(md_text: str) -> str` —— python-markdown 转换,启用 `tables`、`fenced_code`;mineru 原生 `<table>` 块原样透传。
  - `pdf_to_base64(pdf_bytes: bytes) -> str` —— 返回纯 base64 字符串(不含 data: 前缀)。
  - `is_pdf_too_large(pdf_bytes: bytes, max_bytes: int = PDF_INLINE_MAX_BYTES) -> bool`

- [ ] **Step 1: 写失败测试**

`tests/ui/test_render.py`:

```python
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
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/ui/test_render.py -v`
Expected: FAIL —— `ModuleNotFoundError: No module named 'ui.render'`。

- [ ] **Step 3: 写实现**

`ui/render.py`:

```python
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
```

- [ ] **Step 4: 运行测试,确认通过**

Run: `python -m pytest tests/ui/test_render.py -v`
Expected: PASS(4 passed)。

- [ ] **Step 5: 提交**

```bash
git add ui/render.py tests/ui/test_render.py
git commit -m "feat(ui): add pure render helpers (md->html, pdf base64, size threshold)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: 对比组件模板与 HTML 拼装

**Files:**
- Create: `ui/compare_component.html`
- Modify: `ui/render.py`(新增 `build_compare_html`)
- Modify: `tests/ui/test_render.py`(新增拼装测试)

**Interfaces:**
- Consumes: `ui/compare_component.html` 模板文件(含占位符 `{{MD_HTML}}` 与 `{{PDF_B64}}`)。
- Produces:
  - `load_compare_template() -> str` —— 读取同目录 `compare_component.html`。
  - `build_compare_html(md_html: str, pdf_b64: str, template: str | None = None) -> str` —— 把占位符替换为内容;`template=None` 时调用 `load_compare_template()`。

- [ ] **Step 1: 写失败测试(追加到 test_render.py)**

在 `tests/ui/test_render.py` 末尾追加:

```python
from ui.render import build_compare_html, load_compare_template


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
```

- [ ] **Step 2: 运行,确认失败**

Run: `python -m pytest tests/ui/test_render.py -k compare -v`
Expected: FAIL —— `ImportError: cannot import name 'build_compare_html'`。

- [ ] **Step 3: 写模板**

`ui/compare_component.html`(双栏:左 markdown,右 pdf.js;比例同步,markdown 驱动 PDF):

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body { margin: 0; height: 100%; }
  #wrap { display: flex; height: 85vh; gap: 8px; }
  #md, #pdf { flex: 1; overflow: auto; border: 1px solid #ddd; padding: 12px; box-sizing: border-box; }
  #md { background: #fff; }
  #pdf { background: #f5f5f5; }
  #md img { max-width: 100%; }
  #md table { border-collapse: collapse; }
  #md td, #md th { border: 1px solid #ccc; padding: 4px; }
  #pdf canvas { display: block; margin: 0 auto 8px; max-width: 100%; box-shadow: 0 0 3px rgba(0,0,0,.3); }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
</head>
<body>
<div id="wrap">
  <div id="md">{{MD_HTML}}</div>
  <div id="pdf"></div>
</div>
<script>
  const PDF_B64 = "{{PDF_B64}}";
  pdfjsLib.GlobalWorkerOptions.workerSrc =
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const len = bin.length;
    const bytes = new Uint8Array(len);
    for (let i = 0; i < len; i++) bytes[i] = bin.charCodeAt(i);
    return bytes;
  }

  const mdEl = document.getElementById("md");
  const pdfEl = document.getElementById("pdf");

  pdfjsLib.getDocument({ data: b64ToBytes(PDF_B64) }).promise.then(async (pdf) => {
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n);
      const viewport = page.getViewport({ scale: 1.4 });
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      pdfEl.appendChild(canvas);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
    }
  });

  // 比例同步:markdown 驱动 PDF
  let syncing = false;
  mdEl.addEventListener("scroll", () => {
    if (syncing) return;
    syncing = true;
    const denom = mdEl.scrollHeight - mdEl.clientHeight;
    const ratio = denom > 0 ? mdEl.scrollTop / denom : 0;
    pdfEl.scrollTop = ratio * (pdfEl.scrollHeight - pdfEl.clientHeight);
    requestAnimationFrame(() => { syncing = false; });
  });
</script>
</body>
</html>
```

- [ ] **Step 4: 写拼装函数(追加到 ui/render.py)**

在 `ui/render.py` 顶部 import 区追加:

```python
from pathlib import Path
```

在文件末尾追加:

```python
_TEMPLATE_PATH = Path(__file__).with_name("compare_component.html")


def load_compare_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def build_compare_html(md_html: str, pdf_b64: str, template: str | None = None) -> str:
    tpl = template if template is not None else load_compare_template()
    return tpl.replace("{{MD_HTML}}", md_html).replace("{{PDF_B64}}", pdf_b64)
```

- [ ] **Step 5: 运行测试,确认通过**

Run: `python -m pytest tests/ui/test_render.py -v`
Expected: PASS(全部通过)。

- [ ] **Step 6: 提交**

```bash
git add ui/compare_component.html ui/render.py tests/ui/test_render.py
git commit -m "feat(ui): add compare component template and html builder

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Streamlit 视图与入口(手动验收)

**Files:**
- Create: `ui/views.py`
- Create: `ui/app.py`

**Interfaces:**
- Consumes: `data_index`(索引/路径)、`render`(md_to_html / pdf_to_base64 / is_pdf_too_large / build_compare_html)。
- Produces:
  - `views.render_pdf_only(data_root, domain, entry)`
  - `views.render_md_only(data_root, domain, entry)`
  - `views.render_split(data_root, domain, entry)`
  - `app.py` 作为 `streamlit run ui/app.py` 入口。

说明:Streamlit 视图与 pdf.js 渲染/滚动同步靠**手动验收**(Step 4),无自动化测试。本任务无 TDD 测试步骤。

- [ ] **Step 1: 写 views.py**

`ui/views.py`:

```python
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
    st.markdown(_read_md(data_root, domain, entry.doc_id), unsafe_allow_html=True)


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
                _read_md(data_root, domain, entry.doc_id),
                unsafe_allow_html=True,
            )
        with col_pdf:
            _pdf_iframe(pdf_bytes)
        return
    md_html = md_to_html(_read_md(data_root, domain, entry.doc_id))
    html = build_compare_html(md_html, pdf_to_base64(pdf_bytes))
    components.html(html, height=_COMPONENT_HEIGHT, scrolling=False)
```

- [ ] **Step 2: 写 app.py**

`ui/app.py`:

```python
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
```

- [ ] **Step 3: 冒烟检查(导入无误)**

Run: `python -c "import ui.app, ui.views; print('import ok')"`
Expected: 打印 `import ok`,无异常。

- [ ] **Step 4: 手动验收**

Run: `streamlit run ui/app.py`
逐项确认:
1. 侧边栏能选 5 个领域;切换领域后文档列表随之变化;缺失项带 ⚠️ 标注。
2. `📄 PDF` 模式能看到 PDF。
3. `📝 Markdown` + 单栏:渲染 markdown(表格成形、mineru 远程图加载)。
4. `📝 Markdown` + 双栏:左 markdown、右 PDF(pdf.js 渲染);**滚动 markdown,PDF 跟随**。
5. 选一个只有单侧的文档(如只有 md),双栏给出明确提示而非报错。

- [ ] **Step 5: 提交**

```bash
git add ui/views.py ui/app.py
git commit -m "feat(ui): add streamlit views and app entry point

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: 使用文档

**Files:**
- Create: `ui/README.md`

- [ ] **Step 1: 写 ui/README.md**

````markdown
# 解析质量检查查看器

并排对比 mineru 解析出的 markdown 与源 PDF,检查解析质量。

## 运行

```bash
pip install -r requirements.txt
streamlit run ui/app.py
```

浏览器打开后:侧边栏选领域和文档,顶部切换 `PDF` / `Markdown`;
Markdown 模式下可选「单栏」或「双栏(对比 PDF)」,双栏时滚动 markdown,PDF 按比例跟随。

## 说明

- 数据来自 `data/pdf/<domain>/<id>.pdf` 与 `data/markdown/<domain>/<id>.md`。
- 同步为比例(近似页级)同步;缺少版面映射数据,不做逐行精确同步。
- PDF 超过 8MB 时降级为原生预览、不同步。
- markdown 内图片为 mineru 远程链接,需联网加载。
````

- [ ] **Step 2: 提交**

```bash
git add ui/README.md
git commit -m "docs(ui): add usage readme for parse-quality viewer

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:**
- 顶部 PDF/Markdown 切换 → Task 4 `app.py` mode radio。✅
- 单栏/双栏 → Task 4 layout radio + `render_md_only`/`render_split`。✅
- 双栏 pdf.js + 比例同步 → Task 3 模板 + Task 4 `render_split`。✅
- 侧边栏领域/文档导航 → Task 4 `app.py`。✅
- 缺失一侧的处理 → Task 1 DocEntry 标注 + Task 4 视图提示。✅
- PDF 8MB 降级 → Task 2 `is_pdf_too_large` + Task 4 `render_split`。✅
- 远程图直接加载 → Task 4 `unsafe_allow_html=True` / 模板原样注入。✅
- data 目录缺失提示 → Task 4 `app.py` 守卫。✅
- 测试策略(data_index / 纯函数单测,UI 手动验收)→ Task 1/2/3 pytest + Task 4 手动。✅
- 依赖 streamlit/markdown/pdf.js → Task 1 requirements + Task 3 CDN。✅

**Placeholder scan:** 无 TBD/TODO;所有代码步骤含完整代码。✅

**Type consistency:** `build_index`→`DocEntry` 字段、`md_path`/`pdf_path`、`md_to_html`/`pdf_to_base64`/`is_pdf_too_large`/`build_compare_html` 在 Task 4 的调用签名与 Task 1-3 定义一致;模板占位符 `{{MD_HTML}}`/`{{PDF_B64}}` 在 Task 3 模板与 `build_compare_html` 一致。✅
