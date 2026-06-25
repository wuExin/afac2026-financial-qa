# 解析质量检查查看器 — 设计文档

- 日期:2026-06-25
- 状态:已确认设计,待写实现计划
- 来源需求:`docs/PRD_UI1.md`

## 1. 目标与定位

一个**本地 Streamlit 工具**,用于检查 mineru 把 PDF 解析成 markdown 的质量。
核心场景:把解析出的 markdown 与源 PDF **并排对比**,肉眼核对表格、数字、漏文等提取错误。

数据规模:5 个领域共 86 篇文档,布局为
`data/pdf/<domain>/<id>.pdf` ↔ `data/markdown/<domain>/<id>.md`,同 id 一一对应。

领域:`insurance` / `regulatory` / `financial_contracts` / `financial_reports` / `research`。

非目标(YAGNI):
- 不接入 questions/answers 数据(本期只做解析质量检查)。
- 不做逐行/逐块精确同步(缺少版面映射数据,见第 5 节)。
- 不做编辑、批注、导出。

## 2. 界面结构

- **侧边栏(导航)**:
  - 领域下拉:5 个领域。
  - 文档下拉:该领域下的 id 列表。
- **顶部模式切换按钮**(对应 PRD「切换 pdf/markdown」):
  - `📄 PDF` —— 只看 PDF。
  - `📝 Markdown` —— 看 markdown,带一个**单栏 / 双栏**子开关:
    - 单栏:只渲染 markdown。
    - 双栏:左 markdown + 右 PDF,**比例同步滚动**。

## 3. 组件划分(每个职责单一)

- `app.py` —— Streamlit 入口:侧边栏导航 + 顶部模式切换 + 分发到三种视图。
- `data_index.py` —— 扫描 `data/pdf` 与 `data/markdown`,构建 `{domain: [ids]}` 索引,
  提供 `md_path(domain, id)` / `pdf_path(domain, id)` 及缺失标注。**唯一碰文件系统的模块。**
- `views.py` —— 三个渲染函数:`render_pdf_only`、`render_md_only`、`render_split`。
- `compare_component.html`(模板)—— 双栏对比的自定义 HTML/JS:左侧注入渲染好的
  markdown,右侧用 **pdf.js** 渲染 PDF,JS 监听左侧 `scroll` 事件做比例同步。
  通过 `st.components.v1.html` 嵌入。

目录建议(放在仓库内,不污染 `src/` 的赛题代码):
```
ui/
├── app.py
├── data_index.py
├── views.py
└── compare_component.html
```

## 4. 数据流

1. 启动时 `data_index` 扫描两个文件夹 → 内存索引(`@st.cache_data` 缓存)。
2. 用户在侧边栏选 domain + id,顶部选模式。
3. `app.py` 读取对应 md 文本 / pdf 字节,调用对应 view。
4. 双栏视图:md 文本(Python 侧转成 HTML)+ PDF(base64 data URL)一起注入 HTML
   模板 → 组件内 pdf.js 渲染 + 比例滚动同步。markdown 内的 mineru 图片链接
   **直接加载远程 CDN 图**(`cdn-mineru.openxlab.org.cn`)。

## 5. 双栏对比与同步滚动实现细节

**为什么必须用 pdf.js**:浏览器原生 PDF 查看器(iframe)不向 JS 暴露内部滚动位置,
无法被程序控制。pdf.js 把每页渲染成 `<canvas>` 放进自有可滚动 `<div>`,才能读写其
`scrollTop`。

**同步精度——比例同步(已确认)**:
当前数据**没有 mineru 版面 JSON(bbox + page_idx)**,markdown 是纯文本流,不含到 PDF
页/坐标的映射。因此**逐行精确同步不可行**,采用**比例同步**:

```
ratio = left.scrollTop / (left.scrollHeight - left.clientHeight)
right.scrollTop = ratio * (right.scrollHeight - right.clientHeight)
```

即 markdown 滚到 X%,PDF 跟随到约 X% 处(近似页级)。对质量检查场景足够。

**组件内结构**(单个 `st.components.v1.html` 容器,固定高度如 `85vh`,左右两个独立
滚动区):
```
┌──────────────┬──────────────┐
│  markdown    │  pdf.js      │  ← 各自 overflow:auto
│  (HTML)      │  canvas×N    │
└──────────────┴──────────────┘
```

**防死循环**:用 `isSyncing` 标志位,避免左右 scroll 事件互相触发。同步方向以
**markdown 驱动 PDF** 为主(符合 PRD「markdown 滚动时 pdf 跟随」)。

**关键决策**:
- markdown → HTML:在 **Python 侧**用 `markdown` 库转换(保留 `<table>` 原样),
  避免组件内再引一套 JS markdown 解析器。
- PDF 传入:bytes → base64 → `data:application/pdf;base64,...` 作为 pdf.js source。
  单篇 PDF 多在 ~1MB 量级,内联可接受;设**大小阈值(8MB)**,超限走降级(见第 6 节)。
- pdf.js 来源:CDN 引入(与「直接加载远程图」一致,默认联网环境)。

## 6. 错误处理

- **id 只在一侧存在**(有 pdf 无 md 或反之):索引阶段标注;下拉仍可选,缺失一侧显示
  明确提示(「该文档缺少 markdown / PDF」),不崩溃。
- **PDF 过大(> 8MB)**:双栏降级为「PDF 用原生 iframe + 不同步」,并提示原因。
- **markdown 远程图加载失败**(离线 / CDN 不可用):浏览器自然显示破图,不阻塞文本;
  不做额外处理(YAGNI)。
- **data 目录为空 / 路径不存在**:首页给出友好提示,而非堆栈错误。

## 7. 测试策略

纯逻辑用 pytest,UI 渲染靠手动验收:

- `data_index`:给临时目录结构,断言索引正确、缺失项被标注、路径函数返回正确路径。
- base64 / 阈值判断:小文件内联、大文件走降级分支。
- HTML 模板注入:markdown→HTML 转换、PDF data URL 拼接等纯函数可单测。
- 同步滚动 JS、pdf.js 渲染:手动验收(打开双栏,滚 markdown 看 PDF 是否跟随)。

## 8. 依赖

- `streamlit`
- `markdown`(Python markdown→HTML)
- pdf.js(前端 CDN,无需 pip)

均为新增,仅本工具使用;不影响赛题 baseline 运行依赖。
