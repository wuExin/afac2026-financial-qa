# UI 文档内查找(Ctrl+F)设计

日期:2026-06-25
状态:已确认,待实现

## 背景

`ui/` 下的 Streamlit 解析质量检查应用提供三种文档视图(仅 PDF / 仅 Markdown / 双栏对比)。
当前无法在文档内定位关键词,只能手动滚动。本设计为三种视图加入「文档内查找」(类似浏览器 Ctrl+F)。

## 需求(已与用户确认)

- **查找范围**:文档内查找(在当前打开的文档里找词并定位),**非**跨文档全文检索、非按 ID 跳转、非搜题。
- **作用视图**:Markdown 与 PDF **两侧都支持**。
- **交互**:完整 Ctrl+F 体验 —— 高亮全部命中 + 计数(如 `3/12`)+ 上/下一个跳转并滚动定位。
- **双栏对比**:**一个查找框同时查两侧**,计数分项显示(如 `MD 5 / PDF 3`)。

## 方案选择

采用 **方案 A:组件内纯 JS 查找**。

- 查找逻辑全部在 iframe 组件内的 JS 执行,**不触发 Streamlit 重跑** —— 输入即时响应,不丢滚动位置,PDF 不会因每次按键而重新 base64 解码/重画 canvas。
- 已否决:方案 B(`st.text_input` + 重跑)每次按键重画 PDF,卡且跳转体验割裂;方案 C(靠浏览器原生 Ctrl+F)无法搜进 Streamlit 的 iframe 组件,不满足需求。

## 架构与文件改动

```
ui/
├── search.js              ← 新增:共享查找引擎(高亮/计数/上下跳转/键盘)
├── md_component.html      ← 新增:Markdown 视图模板(带查找栏)
├── pdf_component.html     ← 改:加查找栏 + PDF 文字层
├── compare_component.html ← 改:加查找栏 + PDF 文字层(一个框查两侧)
├── render.py             ← 改:注入 search.js;新增 build_md_html()
└── views.py             ← 改:render_md_only 改用组件
```

### 核心设计:引擎靠 DOM 自动发现搜索区域

`search.js` 只写一份。每个模板把「可滚动内容区」标上属性,引擎启动时自动发现并按文档顺序串成一个统一的命中列表:

```html
<div id="md"  data-search-root data-search-label="MD">…</div>
<div id="pdf" data-search-root data-search-label="PDF">…</div>
```

- **md_component**:一个 `#md` root → 计数显示当前序号 `3/12`。
- **pdf_component**:一个 `#pdf` root → 计数显示 `3/8`。
- **compare_component**:两个 root → 上/下一个跨两侧按文档顺序循环,计数分项 `MD 5 / PDF 3`。

模板表现的差异完全由「页面里有几个 `data-search-root`」决定,`search.js` 无需任何 per-template 分支。

render.py 沿用现有 template-path 模式,在每个模板的 `{{SEARCH_JS}}` 占位符处注入 `search.js` 内容。

## 共享查找引擎(search.js)行为

- **查找栏**:`position: sticky` 固定在组件顶部,含 输入框 · 计数 · ↑ · ↓ · ✕ 清除。
- **输入即查**:防抖约 120ms,大小写不敏感;CJK 直接子串匹配(中文财报场景,无需分词)。
- **高亮**:命中处包 `<mark class="hit">`;**当前项**额外加 `.hit.active`(更深底色)。
- **导航**:`Enter` / `↓` → 下一个,`Shift+Enter` / `↑` → 上一个,循环;当前项 `scrollIntoView({block:'center'})`。`Esc` → 清空查找。
- **计数**:多 root(有 label)显示分项(`MD 5 / PDF 3`)+ 当前序号(`3/8`);单 root 显示 `3/8`。
- **重建索引**:对外暴露可触发重新索引的机制,供 PDF 异步渲染完成后调用(见下)。

### 已知边界(v1)

- 命中必须落在**单个文本节点内**。跨节点的短语可能漏匹配:PDF 中一个词被拆到相邻两个文字层 span、或 Markdown 中短语跨越加粗/链接等内联元素边界。财报正文里少见,v1 接受此限制。

## PDF 文字层集成

PDF 当前为纯 canvas,无可查找文本。为每页 canvas 叠加 pdf.js 文字层:

- 每页:canvas 外包一个 `.page`(`position: relative`),内部叠 `.textLayer`(绝对定位,覆盖 canvas)。
- 用 `page.getTextContent()` + `pdfjsLib.renderTextLayer({ textContent, container, viewport })` 生成透明文字 span。
- 高亮 `<mark>` 用半透明黄底(约 `rgba(255,230,0,.45)`),罩在 canvas 字形上仍能看清底字。
- 内联标准 pdf.js textLayer CSS:`.textLayer{position:absolute;inset:0} .textLayer span{position:absolute;color:transparent;white-space:pre}`。
- **异步补索引**:PDF 逐页异步渲染,文字层后出现。所有页渲染完成后派发 `searchcontentready` 事件,引擎监听到即对当前关键词重新索引一次,保证「PDF 未画完就已输入关键词」在画完后补上高亮。
- 现有 `scale: 1.4` 与 compare 的 `pageTops` / 页码徽标逻辑保持不变,文字层仅叠加,不影响滚动与徽标。

## views / render 接线

**render.py**
- 新增 `load_search_js()` 读取 `ui/search.js`。
- `build_pdf_html`、`build_compare_html`、新增 `build_md_html(md_html)` 均在 `{{SEARCH_JS}}` 处注入 search.js。
- `build_md_html` 使用新模板 `md_component.html`(占位符 `{{MD_HTML}}` + `{{SEARCH_JS}}`)。

**views.py**
- `render_md_only`:从 `st.markdown(..., unsafe_allow_html=True)` 改为
  `components.html(build_md_html(md_to_html(...)), height=_COMPONENT_HEIGHT)`,使 md-only 视图也带查找栏。
- `_pdf_pane`、`render_split` 结构不变,模板换新后自动带查找。

### 范围边界:PDF > 8MB 降级分支(已与用户确认保持现状)

`render_split` 中 PDF 超过 8MB 的降级分支,用两个 `st.columns` 并排 `st.markdown` + `_pdf_pane`。
v1 **保持该降级路径现状**:PDF 侧(组件)有查找,左侧 `st.markdown`(主 DOM)无查找框。
理由:>8MB 为少数情况,且不影响正常路径的统一查找;改造会牺牲该情况下的并排同高滚动体验。

## 测试

沿用现有 pytest 模式(测纯函数,不测浏览器 JS):

- `load_search_js()` 返回非空内容。
- `build_md_html` / `build_pdf_html` / `build_compare_html` 注入后**不残留**占位符 `{{SEARCH_JS}}`、`{{MD_HTML}}`、`{{PDF_B64}}`。
- `md_component.html` 模板存在且包含 `data-search-root`。
- headless AppTest 跑通 md-only / pdf / split 三视图无异常(沿用现有 AppTest 套路)。

JS 行为靠手动验证:启动 app,在三视图分别输入关键词,确认高亮、计数、上/下一个跳转定位正常。

## 非目标(YAGNI)

- 跨文档全文检索、按文档 ID 过滤跳转、搜题。
- 正则 / 全字匹配 / 大小写敏感开关。
- 跨文本节点的短语匹配。
- PDF > 8MB 降级分支的 MD 侧查找。
