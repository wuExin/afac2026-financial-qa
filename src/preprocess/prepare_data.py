#!/usr/bin/env python3
"""
数据准备自动化脚本

读者操作流程：
1. 从赛题页面下载 public_dataset_a.zip，放到项目根目录 data/ 下
2. 运行本脚本：python -m src.preprocess.prepare_data
3. 脚本自动完成：解压 ZIP → 提取 PDF → 用 pymupdf4llm 转为 Markdown

输出结构：
    data/
    ├── raw_dataset/           # 解压后的原始数据（含 questions/ 和 raw/）
    │   ├── questions/
    │   └── raw/
    │       ├── insurance/
    │       ├── regulatory/
    │       └── ...
    └── processed_pymupdf4llm/  # 解析后的 Markdown
        ├── insurance/
        ├── regulatory/
        └── ...
"""

import argparse
import zipfile
import sys
from html.parser import HTMLParser
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout


def unzip_dataset(zip_path: Path, extract_to: Path) -> None:
    """解压数据集 ZIP 文件，跳过文件名过长的文件。"""
    if not zip_path.exists():
        print(f"错误: 找不到数据集压缩包: {zip_path}", file=sys.stderr)
        print("请从赛题页面下载 public_dataset_a.zip 并放到 data/ 目录下。", file=sys.stderr)
        return

    print(f"正在解压: {zip_path} → {extract_to}")
    extract_to.mkdir(parents=True, exist_ok=True)

    skipped = 0
    with zipfile.ZipFile(zip_path, "r") as zf:
        for member in zf.namelist():
            # 去掉 ZIP 根目录前缀（如 public_dataset_upload/）
            relative = member
            if "/" in relative:
                parts = relative.split("/", 1)
                if len(parts) == 2:
                    relative = parts[1]
                else:
                    continue
            if not relative:
                continue

            target = extract_to / relative
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if member.endswith("/"):
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    with zf.open(member) as src, open(target, "wb") as dst:
                        dst.write(src.read())
            except OSError as e:
                if e.errno == 36:  # File name too long
                    skipped += 1
                    continue
                raise

    if skipped:
        print(f"解压完成，跳过 {skipped} 个文件名过长的文件（不影响 PDF 和题目）。")
    else:
        print("解压完成。")


def convert_all_pdfs(raw_dir: Path, output_dir: Path, workers: int = 4) -> None:
    """调用 pdf_to_md 批量转换所有 PDF，单任务 60 秒超时。"""
    from src.preprocess.pdf_to_md import process_pdf

    pdfs = sorted(raw_dir.rglob("*.pdf"))
    if not pdfs:
        print(f"未在 {raw_dir} 下找到 PDF 文件，跳过转换。")
        return

    print(f"开始转换 {len(pdfs)} 个 PDF 文件（workers={workers}，超时 60 秒/文件）...")

    ok_count = 0
    err_count = 0
    timeout_count = 0

    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(process_pdf, pdf, output_dir): pdf
            for pdf in pdfs
        }
        for future, pdf in futures.items():
            try:
                result = future.result(timeout=60)
                if result["status"] == "ok":
                    ok_count += 1
                    print(f"[OK] {result['pdf']} → {result['pages']} 页")
                else:
                    err_count += 1
                    print(f"[ERR] {result['pdf']}: {result.get('error', 'unknown')}", file=sys.stderr)
            except FutureTimeout:
                timeout_count += 1
                print(f"[TIMEOUT] {pdf}: 处理超时，跳过", file=sys.stderr)

    print(f"\n转换完成: {ok_count}/{len(pdfs)} 成功, {err_count}/{len(pdfs)} 失败, {timeout_count}/{len(pdfs)} 超时")


class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._parts = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def get_text(self) -> str:
        return "\n".join(self._parts)


def _html_to_text(content: str) -> str:
    parser = _HTMLTextExtractor()
    parser.feed(content)
    return parser.get_text()


def convert_text_documents(raw_dir: Path, output_dir: Path) -> None:
    """Copy TXT/HTML source documents into the processed Markdown layout."""
    files = sorted(
        list(raw_dir.rglob("*.txt"))
        + list(raw_dir.rglob("*.html"))
        + list(raw_dir.rglob("*.htm"))
    )
    if not files:
        return

    ok_count = 0
    err_count = 0
    for path in files:
        try:
            domain = path.relative_to(raw_dir).parts[0]
            doc_id = path.stem
            content = path.read_text(encoding="utf-8", errors="ignore")
            if path.suffix.lower() in {".html", ".htm"}:
                content = _html_to_text(content)

            out_dir = output_dir / domain / doc_id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "page_0001.md").write_text(content, encoding="utf-8")
            ok_count += 1
        except Exception as e:
            err_count += 1
            print(f"[ERR] {path}: {e}", file=sys.stderr)

    print(f"TXT/HTML 转换完成: {ok_count}/{len(files)} 成功, {err_count}/{len(files)} 失败")


def main():
    parser = argparse.ArgumentParser(description="数据准备自动化脚本")
    parser.add_argument(
        "--zip",
        type=str,
        default="data/public_dataset_a.zip",
        help="数据集压缩包路径（默认: data/public_dataset_a.zip）",
    )
    parser.add_argument(
        "--extract-to",
        type=str,
        default="data/raw_dataset",
        help="解压目标目录（默认: data/raw_dataset）",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="data/processed_pymupdf4llm",
        help="Markdown 输出目录（默认: data/processed_pymupdf4llm）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="PDF 转换并发数（默认 4）",
    )
    parser.add_argument(
        "--skip-unzip",
        action="store_true",
        help="跳过解压步骤（如果已经解压过）",
    )
    parser.add_argument(
        "--skip-convert",
        action="store_true",
        help="跳过 PDF 转换步骤",
    )
    args = parser.parse_args()

    zip_path = Path(args.zip)
    extract_to = Path(args.extract_to)
    output_dir = Path(args.output_dir)

    # 1. 解压
    if not args.skip_unzip:
        unzip_dataset(zip_path, extract_to)

    # 2. 转换 PDF
    if not args.skip_convert:
        raw_pdf_dir = extract_to / "raw"
        if raw_pdf_dir.exists():
            convert_all_pdfs(raw_pdf_dir, output_dir, workers=args.workers)
            convert_text_documents(raw_pdf_dir, output_dir)
        else:
            # 有些压缩包的目录结构可能不同，直接搜全部 PDF
            convert_all_pdfs(extract_to, output_dir, workers=args.workers)
            convert_text_documents(extract_to, output_dir)

    print("\n数据准备完成。")
    print(f"  原始数据: {extract_to}")
    print(f"  Markdown: {output_dir}")


if __name__ == "__main__":
    main()
