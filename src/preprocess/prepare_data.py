#!/usr/bin/env python3
"""
数据准备脚本：解压赛题 ZIP 包到 data/raw_dataset/。

PDF 的 Markdown 解析产物（data/merged_md/）由赛题方通过 MinerU 预先提供，
本仓库不再做 PDF→MD 转换。

读者操作流程：
1. 从赛题页面下载 public_dataset_a.zip，放到项目根目录 data/ 下
2. 运行本脚本：python -m src.preprocess.prepare_data
3. 脚本解压 ZIP 到 data/raw_dataset/（含 questions/ 和 raw/）
"""

import argparse
import sys
import zipfile
from pathlib import Path


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


def main():
    parser = argparse.ArgumentParser(description="数据准备脚本")
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
    args = parser.parse_args()

    zip_path = Path(args.zip)
    extract_to = Path(args.extract_to)

    unzip_dataset(zip_path, extract_to)

    print("\n数据准备完成。")
    print(f"  原始数据: {extract_to}")
    print("  Markdown: data/merged_md（赛题方预先提供）")


if __name__ == "__main__":
    main()
