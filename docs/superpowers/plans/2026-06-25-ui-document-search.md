# UI 文档内查找(Ctrl+F)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Streamlit 解析质量检查应用的三种文档视图(仅 PDF / 仅 Markdown / 双栏对比)加入「文档内查找」(Ctrl+F):Markdown 与 PDF 两侧均支持高亮、计数、上/下一个跳转定位;双栏视图一个查找框同时查两侧。

**Architecture:** 方案 A —— 查找逻辑全部在 iframe 组件内的 JS 执行,不触发 Streamlit 重跑。新增一份共享引擎 `ui/search.js`,靠 DOM 中的 `data-search-root` 属性自动发现搜索区域;PDF 通过叠加 pdf.js 文字层获得可查找文本。`render.py` 在各模板的 `{{SEARCH_JS}}` 占位符处注入引擎,`views.py` 把仅 Markdown 视图也改成组件以挂载查找栏。

**Tech Stack:** Python 3.12 · Streamlit · pytest · pdf.js 3.11.174(CDN)· 原生 JS(无构建步骤)

## Global Constraints

- pdf.js 固定版本 `3.11.174`,经 cdnjs 引入(沿用现有模板,勿升级)。
- 答案文件用 `utf-8-sig` 读;其余文本文件 `utf-8`。
- 渲染纯函数在 `ui/render.py`,唯一文件系统访问在 `ui/data_index.py`;保持该分层。
- 测试沿用现有 pytest 纯函数模式(`tests/ui/test_render.py`),不引入浏览器端 JS 测试;JS 行为靠手动验证。
- PDF 渲染保持 `scale: 1.4`;compare 视图的页码徽标(`pageTops` / `currentPage`)行为不变。
- 提交信息以 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` 结尾。
- 范围边界:`render_split` 中 PDF > 8MB 的降级分支**保持现状**(左侧 `st.markdown` 无查找栏),本计划不改动该分支。

---

### Task 1: 共享查找引擎 `ui/search.js` + `load_search_js()`

**Files:**
- Create: `ui/search.js`
- Modify: `ui/render.py`(新增 `load_search_js()`)
- Test: `tests/ui/test_render.py`

**Interfaces:**
- Produces:
  - `ui/search.js` —— 自执行脚本。运行时:① 创建并插入顶部查找栏 `#search-bar`(输入框 + `#search-count` + 上/下/清除按钮);② 注入高亮 CSS(`mark.hit` / `mark.hit.active` / `#search-bar`);③ 自动发现所有 `[data-search-root]` 元素作为搜索区;④ 监听 `document` 上的 `searchcontentready` 事件以重建索引。依赖每个 root 上可选的 `data-search-label` 属性用于分项计数。
  - `render.load_search_js() -> str` —— 读取并返回 `ui/search.js` 文本内容(`utf-8`)。

- [ ] **Step 1: 写 `ui/search.js`(完整内容)**

```javascript
/* afac-search-engine: in-document find (Ctrl+F) shared across md/pdf/compare templates */
(function () {
  "use strict";

  // ---- styles -------------------------------------------------------------
  var style = document.createElement("style");
  style.textContent =
    "#search-bar{flex:none;display:flex;gap:6px;align-items:center;" +
    "background:#fff;border-bottom:1px solid #ddd;padding:6px 8px;" +
    "font:13px/1.4 sans-serif;}" +
    "#search-bar input{flex:0 1 240px;padding:3px 6px;font:inherit;}" +
    "#search-bar button{cursor:pointer;border:1px solid #ccc;background:#f7f7f7;" +
    "border-radius:4px;padding:2px 8px;font:inherit;}" +
    "#search-count{color:#555;min-width:120px;}" +
    "mark.hit{background:rgba(255,230,0,.45);color:inherit;padding:0;}" +
    "mark.hit.active{background:rgba(255,145,0,.8);}";
  document.head.appendChild(style);

  // ---- toolbar ------------------------------------------------------------
  var bar = document.createElement("div");
  bar.id = "search-bar";
  bar.innerHTML =
    '<input id="search-input" type="text" placeholder="查找文档内容…" />' +
    '<span id="search-count"></span>' +
    '<button id="search-prev" title="上一个 (Shift+Enter)">↑</button>' +
    '<button id="search-next" title="下一个 (Enter)">↓</button>' +
    '<button id="search-clear" title="清除 (Esc)">✕</button>';
  document.body.insertBefore(bar, document.body.firstChild);

  var input = bar.querySelector("#search-input");
  var countEl = bar.querySelector("#search-count");
  var roots = Array.prototype.slice.call(
    document.querySelectorAll("[data-search-root]")
  );

  var matches = []; // { markEl, rootIndex }
  var current = -1;
  var query = "";

  // ---- clear previous marks ----------------------------------------------
  function clearMarks() {
    for (var r = 0; r < roots.length; r++) {
      var marks = roots[r].querySelectorAll("mark.hit");
      for (var i = 0; i < marks.length; i++) {
        var m = marks[i];
        var parent = m.parentNode;
        while (m.firstChild) parent.insertBefore(m.firstChild, m);
        parent.removeChild(m);
        parent.normalize();
      }
    }
    matches = [];
    current = -1;
  }

  // ---- collect text nodes under a root -----------------------------------
  function textNodesIn(root) {
    var walker = document.createTreeWalker(
      root,
      NodeFilter.SHOW_TEXT,
      {
        acceptNode: function (node) {
          if (!node.nodeValue || !node.nodeValue.trim())
            return NodeFilter.FILTER_REJECT;
          var p = node.parentNode;
          if (p && (p.nodeName === "SCRIPT" || p.nodeName === "STYLE"))
            return NodeFilter.FILTER_REJECT;
          return NodeFilter.FILTER_ACCEPT;
        },
      }
    );
    var nodes = [];
    var n;
    while ((n = walker.nextNode())) nodes.push(n);
    return nodes;
  }

  // ---- wrap occurrences of query inside one text node --------------------
  function wrapInNode(node, rootIndex, q) {
    var text = node.nodeValue;
    var hay = text.toLowerCase();
    var idx = hay.indexOf(q);
    if (idx === -1) return;
    var frag = document.createDocumentFragment();
    var pos = 0;
    while (idx !== -1) {
      if (idx > pos)
        frag.appendChild(document.createTextNode(text.slice(pos, idx)));
      var mark = document.createElement("mark");
      mark.className = "hit";
      mark.textContent = text.slice(idx, idx + q.length);
      frag.appendChild(mark);
      matches.push({ markEl: mark, rootIndex: rootIndex });
      pos = idx + q.length;
      idx = hay.indexOf(q, pos);
    }
    if (pos < text.length)
      frag.appendChild(document.createTextNode(text.slice(pos)));
    node.parentNode.replaceChild(frag, node);
  }

  // ---- run a search -------------------------------------------------------
  function runSearch() {
    clearMarks();
    var q = query.toLowerCase();
    if (q) {
      for (var r = 0; r < roots.length; r++) {
        var nodes = textNodesIn(roots[r]);
        for (var i = 0; i < nodes.length; i++) wrapInNode(nodes[i], r, q);
      }
      if (matches.length) activate(0, false);
    }
    updateCount();
  }

  function activate(i, scroll) {
    if (!matches.length) return;
    if (current >= 0 && matches[current])
      matches[current].markEl.classList.remove("active");
    current = (i + matches.length) % matches.length;
    var m = matches[current].markEl;
    m.classList.add("active");
    if (scroll !== false)
      m.scrollIntoView({ block: "center", inline: "nearest" });
    updateCount();
  }

  function updateCount() {
    if (!query) {
      countEl.textContent = "";
      return;
    }
    if (!matches.length) {
      countEl.textContent = "无结果";
      return;
    }
    var pos = (current + 1) + "/" + matches.length;
    if (roots.length > 1) {
      var per = [];
      for (var r = 0; r < roots.length; r++) {
        var label = roots[r].getAttribute("data-search-label") || ("区" + (r + 1));
        var c = 0;
        for (var i = 0; i < matches.length; i++)
          if (matches[i].rootIndex === r) c++;
        per.push(label + " " + c);
      }
      countEl.textContent = per.join(" / ") + "  (" + pos + ")";
    } else {
      countEl.textContent = pos;
    }
  }

  // ---- events -------------------------------------------------------------
  var debounceTimer = null;
  input.addEventListener("input", function () {
    query = input.value;
    if (debounceTimer) clearTimeout(debounceTimer);
    debounceTimer = setTimeout(runSearch, 120);
  });
  input.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (e.shiftKey) activate(current - 1, true);
      else activate(current + 1, true);
    } else if (e.key === "Escape") {
      e.preventDefault();
      input.value = "";
      query = "";
      clearMarks();
      updateCount();
    }
  });
  bar.querySelector("#search-next").addEventListener("click", function () {
    activate(current + 1, true);
  });
  bar.querySelector("#search-prev").addEventListener("click", function () {
    activate(current - 1, true);
  });
  bar.querySelector("#search-clear").addEventListener("click", function () {
    input.value = "";
    query = "";
    clearMarks();
    updateCount();
  });

  // PDF text layers render asynchronously; re-index when they signal ready.
  document.addEventListener("searchcontentready", function () {
    if (query) runSearch();
  });
})();
```

- [ ] **Step 2: 在 `ui/render.py` 新增 `load_search_js()`**

在 `_PDF_TEMPLATE_PATH` 定义附近加路径常量与读取函数(放在 `load_pdf_template` 之后):

```python
_SEARCH_JS_PATH = Path(__file__).with_name("search.js")


def load_search_js() -> str:
    return _SEARCH_JS_PATH.read_text(encoding="utf-8")
```

- [ ] **Step 3: 写失败测试**

在 `tests/ui/test_render.py` 顶部 import 里加入 `load_search_js`,并新增:

```python
def test_load_search_js_nonempty_with_markers():
    js = load_search_js()
    assert "afac-search-engine" in js
    assert "searchcontentready" in js
    assert "data-search-root" in js
```

- [ ] **Step 4: 跑测试确认通过(文件已先写好,应直接 PASS)**

Run: `python -m pytest tests/ui/test_render.py::test_load_search_js_nonempty_with_markers -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add ui/search.js ui/render.py tests/ui/test_render.py
git commit -m "$(printf 'feat(ui): shared in-document search engine (search.js)\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 2: Markdown 视图模板 `ui/md_component.html` + `build_md_html()`

**Files:**
- Create: `ui/md_component.html`
- Modify: `ui/render.py`(新增 `build_md_html`;为 `build_pdf_html`/`build_compare_html` 增加 `search_js` 注入参数)
- Test: `tests/ui/test_render.py`

**Interfaces:**
- Consumes: `load_search_js()`(Task 1)、`load_compare_template()`、`load_pdf_template()`、现有 `build_pdf_html`/`build_compare_html`。
- Produces:
  - `render.load_md_template() -> str`
  - `render.build_md_html(md_html: str, template: str | None = None, search_js: str | None = None) -> str` —— 用 `md_component.html` 替换 `{{MD_HTML}}` 与 `{{SEARCH_JS}}`。
  - `build_pdf_html` / `build_compare_html` 新增 `search_js: str | None = None` 参数,额外替换 `{{SEARCH_JS}}`(参数为 `None` 时调用 `load_search_js()`;模板无该占位符时为无副作用 no-op)。

- [ ] **Step 1: 写 `ui/md_component.html`(完整内容)**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body { height: 100%; margin: 0; display: flex; flex-direction: column; }
  #md {
    flex: 1; min-height: 0; overflow: auto;
    border: 1px solid #ddd; padding: 12px; box-sizing: border-box; background: #fff;
  }
  #md img { max-width: 100%; }
  #md table { border-collapse: collapse; }
  #md td, #md th { border: 1px solid #ccc; padding: 4px; }
</style>
</head>
<body>
  <div id="md" data-search-root data-search-label="MD">{{MD_HTML}}</div>
  <script>{{SEARCH_JS}}</script>
</body>
</html>
```

- [ ] **Step 2: 写失败测试**

在 `tests/ui/test_render.py` import 里加 `build_md_html`, `load_md_template`,新增:

```python
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
```

- [ ] **Step 3: 跑测试确认失败**

Run: `python -m pytest tests/ui/test_render.py::test_build_md_html_substitutes -v`
Expected: FAIL(`ImportError: cannot import name 'build_md_html'`)

- [ ] **Step 4: 在 `ui/render.py` 实现**

新增模板路径与函数,并改造两个已有 builder。完整替换 `render.py` 中模板相关段落为:

```python
_TEMPLATE_PATH = Path(__file__).with_name("compare_component.html")
_PDF_TEMPLATE_PATH = Path(__file__).with_name("pdf_component.html")
_MD_TEMPLATE_PATH = Path(__file__).with_name("md_component.html")
_SEARCH_JS_PATH = Path(__file__).with_name("search.js")


def load_compare_template() -> str:
    return _TEMPLATE_PATH.read_text(encoding="utf-8")


def load_pdf_template() -> str:
    return _PDF_TEMPLATE_PATH.read_text(encoding="utf-8")


def load_md_template() -> str:
    return _MD_TEMPLATE_PATH.read_text(encoding="utf-8")


def load_search_js() -> str:
    return _SEARCH_JS_PATH.read_text(encoding="utf-8")


def build_compare_html(
    md_html: str,
    pdf_b64: str,
    template: str | None = None,
    search_js: str | None = None,
) -> str:
    tpl = template if template is not None else load_compare_template()
    js = search_js if search_js is not None else load_search_js()
    return (
        tpl.replace("{{MD_HTML}}", md_html)
        .replace("{{PDF_B64}}", pdf_b64)
        .replace("{{SEARCH_JS}}", js)
    )


def build_pdf_html(
    pdf_b64: str,
    template: str | None = None,
    search_js: str | None = None,
) -> str:
    tpl = template if template is not None else load_pdf_template()
    js = search_js if search_js is not None else load_search_js()
    return tpl.replace("{{PDF_B64}}", pdf_b64).replace("{{SEARCH_JS}}", js)


def build_md_html(
    md_html: str,
    template: str | None = None,
    search_js: str | None = None,
) -> str:
    tpl = template if template is not None else load_md_template()
    js = search_js if search_js is not None else load_search_js()
    return tpl.replace("{{MD_HTML}}", md_html).replace("{{SEARCH_JS}}", js)
```

> 注意:已删去旧的 `load_search_js`(Task 1 临时加的)若重复需保留一份;此段为权威定义,确保 `render.py` 中只存在一份每个函数。

- [ ] **Step 5: 跑全部 render 测试确认通过**

Run: `python -m pytest tests/ui/test_render.py -v`
Expected: 全部 PASS(含 Task 1 与既有 8 项;既有 `test_build_pdf_html_substitutes`/`test_build_compare_html_substitutes` 因模板字符串不含 `{{SEARCH_JS}}`,注入为 no-op,仍 PASS)

- [ ] **Step 6: 提交**

```bash
git add ui/md_component.html ui/render.py tests/ui/test_render.py
git commit -m "$(printf 'feat(ui): markdown view component with search bar\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 3: PDF 视图模板加查找栏与文字层

**Files:**
- Modify: `ui/pdf_component.html`
- Test: `tests/ui/test_render.py`

**Interfaces:**
- Consumes: `build_pdf_html`(已支持 `search_js`,Task 2)。
- Produces: `pdf_component.html` 渲染后每页为 `.page`(canvas + `.textLayer`),`#pdf` 标记 `data-search-root data-search-label="PDF"`,全部页渲染完成后派发 `searchcontentready`,含 `{{SEARCH_JS}}` 占位符。

- [ ] **Step 1: 写失败测试**

在 `tests/ui/test_render.py` 新增:

```python
def test_load_pdf_template_has_search_and_textlayer():
    tpl = load_pdf_template()
    assert "{{SEARCH_JS}}" in tpl
    assert "data-search-root" in tpl
    assert "renderTextLayer" in tpl
    assert "searchcontentready" in tpl
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/ui/test_render.py::test_load_pdf_template_has_search_and_textlayer -v`
Expected: FAIL(断言找不到 `{{SEARCH_JS}}` / `renderTextLayer`)

- [ ] **Step 3: 用以下内容完整替换 `ui/pdf_component.html`**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body { height: 100%; margin: 0; display: flex; flex-direction: column; }
  #pdf {
    flex: 1; min-height: 0; overflow: auto;
    border: 1px solid #ddd; padding: 12px; box-sizing: border-box; background: #f5f5f5;
  }
  .page {
    position: relative; margin: 0 auto 8px;
    box-shadow: 0 0 3px rgba(0,0,0,.3); background: #fff;
  }
  .page canvas { display: block; }
  .textLayer {
    position: absolute; left: 0; top: 0; right: 0; bottom: 0;
    overflow: hidden; line-height: 1;
  }
  .textLayer span {
    position: absolute; white-space: pre; color: transparent; transform-origin: 0 0;
  }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
</head>
<body>
<div id="pdf" data-search-root data-search-label="PDF"></div>
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

  const pdfEl = document.getElementById("pdf");
  pdfjsLib.getDocument({ data: b64ToBytes(PDF_B64) }).promise.then(async (pdf) => {
    for (let n = 1; n <= pdf.numPages; n++) {
      const page = await pdf.getPage(n);
      const viewport = page.getViewport({ scale: 1.4 });
      const pageDiv = document.createElement("div");
      pageDiv.className = "page";
      pageDiv.style.width = viewport.width + "px";
      pageDiv.style.height = viewport.height + "px";
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      pageDiv.appendChild(canvas);
      pdfEl.appendChild(pageDiv);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      const textContent = await page.getTextContent();
      const textLayerDiv = document.createElement("div");
      textLayerDiv.className = "textLayer";
      pageDiv.appendChild(textLayerDiv);
      await pdfjsLib.renderTextLayer({
        textContent, container: textLayerDiv, viewport, textDivs: [],
      }).promise;
    }
    document.dispatchEvent(new Event("searchcontentready"));
  });
</script>
<script>{{SEARCH_JS}}</script>
</body>
</html>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/ui/test_render.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ui/pdf_component.html tests/ui/test_render.py
git commit -m "$(printf 'feat(ui): PDF view search bar + pdf.js text layer\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 4: 双栏对比模板加查找栏与文字层(一个框查两侧)

**Files:**
- Modify: `ui/compare_component.html`
- Test: `tests/ui/test_render.py`

**Interfaces:**
- Consumes: `build_compare_html`(已支持 `search_js`,Task 2)。
- Produces: `compare_component.html` —— `#md` 与 `#pdf` 均为 `data-search-root`(label 分别 `MD`/`PDF`),PDF 侧每页 `.page` + `.textLayer`,渲染完成派发 `searchcontentready`;页码徽标 `pageTops` 改用 `pageDiv.offsetTop`;含 `{{SEARCH_JS}}`。

- [ ] **Step 1: 写失败测试**

在 `tests/ui/test_render.py` 新增:

```python
def test_load_compare_template_has_search_and_textlayer():
    tpl = load_compare_template()
    assert "{{SEARCH_JS}}" in tpl
    assert tpl.count("data-search-root") == 2
    assert 'data-search-label="MD"' in tpl
    assert 'data-search-label="PDF"' in tpl
    assert "renderTextLayer" in tpl
    assert "searchcontentready" in tpl
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/ui/test_render.py::test_load_compare_template_has_search_and_textlayer -v`
Expected: FAIL

- [ ] **Step 3: 用以下内容完整替换 `ui/compare_component.html`**

```html
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8" />
<style>
  html, body { height: 100%; margin: 0; display: flex; flex-direction: column; }
  #wrap { flex: 1; min-height: 0; display: flex; gap: 8px; }
  #md, #pdf { flex: 1; overflow: auto; border: 1px solid #ddd; padding: 12px; box-sizing: border-box; }
  #md { background: #fff; }
  #pdf { position: relative; background: #f5f5f5; }
  #md img { max-width: 100%; }
  #md table { border-collapse: collapse; }
  #md td, #md th { border: 1px solid #ccc; padding: 4px; }
  .page {
    position: relative; margin: 0 auto 8px;
    box-shadow: 0 0 3px rgba(0,0,0,.3); background: #fff;
  }
  .page canvas { display: block; }
  .textLayer {
    position: absolute; left: 0; top: 0; right: 0; bottom: 0;
    overflow: hidden; line-height: 1;
  }
  .textLayer span {
    position: absolute; white-space: pre; color: transparent; transform-origin: 0 0;
  }
  #pagebadge {
    position: sticky; top: 0; float: right; z-index: 10;
    background: rgba(33,33,33,.82); color: #fff; font: 12px/1.4 sans-serif;
    padding: 3px 10px; border-radius: 12px; pointer-events: none;
    margin-bottom: -24px;
  }
</style>
<script src="https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js"></script>
</head>
<body>
<div id="wrap">
  <div id="md" data-search-root data-search-label="MD">{{MD_HTML}}</div>
  <div id="pdf" data-search-root data-search-label="PDF"><div id="pagebadge">第 - / - 页</div></div>
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

  const pdfEl = document.getElementById("pdf");
  const badgeEl = document.getElementById("pagebadge");

  // pageDiv.offsetTop for each rendered page, in PDF pane scroll coordinates.
  const pageTops = [];
  let numPages = 0;

  pdfjsLib.getDocument({ data: b64ToBytes(PDF_B64) }).promise.then(async (pdf) => {
    numPages = pdf.numPages;
    for (let n = 1; n <= numPages; n++) {
      const page = await pdf.getPage(n);
      const viewport = page.getViewport({ scale: 1.4 });
      const pageDiv = document.createElement("div");
      pageDiv.className = "page";
      pageDiv.style.width = viewport.width + "px";
      pageDiv.style.height = viewport.height + "px";
      const canvas = document.createElement("canvas");
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      pageDiv.appendChild(canvas);
      pdfEl.appendChild(pageDiv);
      await page.render({ canvasContext: canvas.getContext("2d"), viewport }).promise;
      const textContent = await page.getTextContent();
      const textLayerDiv = document.createElement("div");
      textLayerDiv.className = "textLayer";
      pageDiv.appendChild(textLayerDiv);
      await pdfjsLib.renderTextLayer({
        textContent, container: textLayerDiv, viewport, textDivs: [],
      }).promise;
      pageTops.push(pageDiv.offsetTop);
    }
    updateBadge();
    document.dispatchEvent(new Event("searchcontentready"));
  });

  // 当前视口顶部所在的 PDF 页码(1-based)。无映射数据,按实际页面位置判定。
  function currentPage() {
    if (!pageTops.length) return 0;
    const top = pdfEl.scrollTop + pdfEl.clientHeight * 0.25; // 视口偏上一点作判定点
    let page = 1;
    for (let i = 0; i < pageTops.length; i++) {
      if (pageTops[i] <= top) page = i + 1;
      else break;
    }
    return page;
  }

  function updateBadge() {
    if (!numPages) return;
    badgeEl.textContent = "第 " + currentPage() + " / " + numPages + " 页";
  }

  // 两栏各自独立滚动,不做跟随同步。仅在 PDF 栏滚动时刷新页码徽标。
  pdfEl.addEventListener("scroll", updateBadge);
</script>
<script>{{SEARCH_JS}}</script>
</body>
</html>
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/ui/test_render.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add ui/compare_component.html tests/ui/test_render.py
git commit -m "$(printf 'feat(ui): compare view unified search bar + PDF text layer\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

### Task 5: `views.py` 接线(md-only 改用组件)+ README + 手动验证

**Files:**
- Modify: `ui/views.py`(`render_md_only`)
- Modify: `ui/README.md`
- Test: 手动验证(沿用项目约定,views 层无自动化测试)

**Interfaces:**
- Consumes: `render.build_md_html`(Task 2)、`_COMPONENT_HEIGHT`、`components.html`。

- [ ] **Step 1: 修改 `render_md_only`**

在 `ui/views.py` 的 import 块把 `build_md_html` 加入 `from ui.render import (...)`,然后用组件渲染替换函数体:

```python
def render_md_only(data_root: Path, domain: str, entry: DocEntry) -> None:
    if not entry.has_md:
        st.warning("该文档缺少 markdown")
        return
    md_html = md_to_html(_read_md(data_root, domain, entry.doc_id))
    components.html(build_md_html(md_html), height=_COMPONENT_HEIGHT, scrolling=False)
```

> 说明:`render_split` 中 >8MB 降级分支仍调用 `st.markdown(md_to_html(...))` 渲染左侧 —— **保持不变**(Global Constraints 范围边界)。

- [ ] **Step 2: 跑全套测试确认无回归**

Run: `python -m pytest tests/ui -q`
Expected: 全部 PASS(应为 16 + 新增 4 = 20 项)

- [ ] **Step 3: 启动应用手动验证**

Run: `streamlit run ui/app.py`

逐项确认:
1. **仅 Markdown 视图**:顶部出现查找栏;输入关键词 → 命中高亮、计数 `n/N`;`Enter`/`↓` 跳下一个并滚动定位、`Shift+Enter`/`↑` 跳上一个;`Esc`/✕ 清除。
2. **仅 PDF 视图**:PDF 正常逐页显示;输入关键词 → canvas 上对应文字出现黄色高亮框;上/下一个跳转定位;计数 `n/N`。
3. **双栏对比视图**:顶部一个查找栏;输入关键词 → MD 与 PDF 两侧同时高亮;计数显示分项 `MD x / PDF y  (n/总)`;上/下一个按文档顺序跨两侧循环;页码徽标随 PDF 滚动正常更新。
4. PDF 未渲染完即输入关键词 → 渲染完成后高亮自动补上(`searchcontentready` 重建索引)。

- [ ] **Step 4: 更新 `ui/README.md`**

在 README 中文档查看说明处补一段(紧接现有视图说明之后):

```markdown
## 文档内查找

三种视图(仅 PDF / 仅 Markdown / 双栏对比)顶部均有查找栏:

- 输入关键词即时高亮全部命中(大小写不敏感)。
- `Enter` / `↓` 下一个,`Shift+Enter` / `↑` 上一个,自动滚动定位;`Esc` / ✕ 清除。
- 双栏视图一个查找框同时查 Markdown 与 PDF 两侧,计数分项显示(如 `MD 5 / PDF 3`)。
- PDF 查找基于 pdf.js 文字层;命中限于单个文本节点内,跨节点短语可能漏匹配。
- 注:PDF > 8MB 的并排降级视图,左侧 Markdown 不带查找栏。
```

- [ ] **Step 5: 提交**

```bash
git add ui/views.py ui/README.md
git commit -m "$(printf 'feat(ui): wire markdown-only view to search component + docs\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Self-Review

**Spec coverage**
- 文档内查找 / Ctrl+F → Task 1(引擎)。✅
- Markdown 侧查找 → Task 2(md 组件)。✅
- PDF 侧查找(文字层)→ Task 3。✅
- 双栏一个框查两侧、分项计数 → Task 4 + 引擎 `updateCount` 分项逻辑。✅
- 高亮 + 计数 + 上/下一个跳转定位 → 引擎 `activate`/`updateCount` + 键盘/按钮(Task 1)。✅
- DOM 自动发现 `data-search-root` → 引擎 + 各模板属性(Tasks 1–4)。✅
- PDF 异步补索引 `searchcontentready` → Tasks 1/3/4。✅
- render/views 接线、md-only 改组件 → Tasks 2/5。✅
- >8MB 降级分支保持现状 → Task 5 Step 1 说明 + 不改该分支。✅
- 测试(纯函数 + 模板占位/标记检查)→ 每个 Task 的测试步骤。✅

**Placeholder scan:** 无 TBD/TODO;每个代码步骤含完整代码;命令含预期输出。✅

**Type consistency:** `build_md_html` / `build_pdf_html` / `build_compare_html` 三者签名一致(`template`/`search_js` 均 `str | None = None`);`load_search_js`/`load_md_template` 命名在各 Task 一致;模板属性 `data-search-root` / `data-search-label` 拼写统一;事件名 `searchcontentready` 在引擎与两个 PDF 模板一致。✅

> 实现提示:Task 1 Step 2 临时加入的 `load_search_js`/`_SEARCH_JS_PATH` 与 Task 2 Step 4 的权威定义重叠 —— Task 2 用整段替换 render.py 模板区,确保最终每个函数只存在一份(执行 Task 2 后无重复定义)。
