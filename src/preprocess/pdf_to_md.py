#!/usr/bin/env python3
"""
PDF 批量转 Markdown 脚本（基于 pymupdf4llm）

用法示例：
    python -m src.preprocess.pdf_to_md \
        --input-dir design-draft/data/raw_dataset/raw \
        --output-dir data/processed_pymupdf4llm \
        --workers 4

输入目录结构（与 raw_dataset 一致）：
    input-dir/
    ├── insurance/
    │   ├── 1.pdf
    │   ├── 2.pdf
    │   └── ...
    ├── regulatory/
    └── ...

输出目录结构（与已有解析数据对齐）：
    output-dir/
    ├── insurance/
    │   ├── 1/
    │   │   ├── page_0001.md
    │   │   ├── page_0002.md
    │   │   └── ...
    │   ├── 2/
    │   └── ...
    ├── regulatory/
    └── ...
"""

import argparse
import json
import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed


def process_pdf(pdf_path: Path, output_dir: Path) -> dict:
    """处理单个 PDF，按页输出 Markdown。"""
    import pymupdf4llm

    domain = pdf_path.parent.name
    doc_id = pdf_path.stem

    out_dir = output_dir / domain / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        chunks = pymupdf4llm.to_markdown(str(pdf_path), page_chunks=True)
        for i, chunk in enumerate(chunks):
            page_num = i + 1
            md_path = out_dir / f"page_{page_num:04d}.md"
            md_path.write_text(chunk["text"], encoding="utf-8")

        return {
            "pdf": str(pdf_path),
            "pages": len(chunks),
            "output": str(out_dir),
            "status": "ok",
        }
    except Exception as e:
        return {
            "pdf": str(pdf_path),
            "pages": 0,
            "output": str(out_dir),
            "status": "error",
            "error": str(e),
        }


def main():
    parser = argparse.ArgumentParser(description="PDF 批量转 Markdown")
    parser.add_argument(
        "--input-dir",
        type=str,
        required=True,
        help="输入 PDF 根目录（含各领域的子目录）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        required=True,
        help="输出 Markdown 根目录",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="并发处理线程数（默认 4）",
    )
    parser.add_argument(
        "--json",
        type=str,
        default=None,
        help="可选：将处理结果写入 JSON 文件",
    )
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.exists():
        print(f"错误: 输入目录不存在: {input_dir}", file=sys.stderr)
        return

    pdfs = sorted(input_dir.rglob("*.pdf"))
    print(f"发现 {len(pdfs)} 个 PDF 文件")

    if not pdfs:
        print("没有找到 PDF 文件，请检查输入目录。", file=sys.stderr)
        return

    results = []
    ok_count = 0
    err_count = 0

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_pdf = {
            executor.submit(process_pdf, pdf, output_dir): pdf
            for pdf in pdfs
        }
        for future in as_completed(future_to_pdf):
            result = future.result()
            results.append(result)
            if result["status"] == "ok":
                ok_count += 1
                print(
                    f"[OK] {result['pdf']} → {result['pages']} 页 → {result['output']}"
                )
            else:
                err_count += 1
                print(
                    f"[ERR] {result['pdf']}: {result.get('error', 'unknown')}",
                    file=sys.stderr,
                )

    print(
        f"\n完成: {ok_count}/{len(results)} 成功, {err_count}/{len(results)} 失败"
    )

    if args.json:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"结果已保存: {json_path}")


if __name__ == "__main__":
    main()
