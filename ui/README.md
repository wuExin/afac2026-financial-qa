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

## 文档内查找

三种视图(仅 PDF / 仅 Markdown / 双栏对比)顶部均有查找栏:

- 输入关键词即时高亮全部命中(大小写不敏感)。
- `Enter` / `↓` 下一个,`Shift+Enter` / `↑` 上一个,自动滚动定位;`Esc` / ✕ 清除。
- 双栏视图一个查找框同时查 Markdown 与 PDF 两侧,计数分项显示(如 `MD 5 / PDF 3`)。
- PDF 查找基于 pdf.js 文字层;命中限于单个文本节点内,跨节点短语可能漏匹配。
- 注:PDF > 8MB 的并排降级视图,左侧 Markdown 不带查找栏。
