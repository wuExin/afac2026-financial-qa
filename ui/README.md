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
