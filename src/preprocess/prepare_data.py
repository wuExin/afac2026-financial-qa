#!/usr/bin/env python3
"""
Data preparation script.

PDF files are expected to already be placed under data/pdf/. This script only
converts those PDFs into Markdown files under data/processed_pymupdf4llm/.
"""

import argparse
import sys
from concurrent.futures import ProcessPoolExecutor, TimeoutError as FutureTimeout
from pathlib import Path


DEFAULT_PDF_DIR = Path("data/pdf")
DEFAULT_OUTPUT_DIR = Path("data/processed_pymupdf4llm")


def convert_all_pdfs(pdf_dir: Path, output_dir: Path, workers: int = 4) -> None:
    """Convert all PDFs under pdf_dir into Markdown page files."""
    from src.preprocess.pdf_to_md import process_pdf

    pdfs = sorted(pdf_dir.rglob("*.pdf"))
    if not pdfs:
        print(f"No PDF files found under {pdf_dir}; skipping conversion.")
        return

    print(f"Converting {len(pdfs)} PDF files from {pdf_dir} (workers={workers})...")

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
                    print(f"[OK] {result['pdf']} -> {result['pages']} pages")
                else:
                    err_count += 1
                    print(
                        f"[ERR] {result['pdf']}: {result.get('error', 'unknown')}",
                        file=sys.stderr,
                    )
            except FutureTimeout:
                timeout_count += 1
                print(f"[TIMEOUT] {pdf}: skipped after 60 seconds", file=sys.stderr)

    print(
        f"\nConversion complete: {ok_count}/{len(pdfs)} ok, "
        f"{err_count}/{len(pdfs)} failed, {timeout_count}/{len(pdfs)} timed out"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare PDF Markdown data")
    parser.add_argument(
        "--pdf-dir",
        type=str,
        default=str(DEFAULT_PDF_DIR),
        help="PDF input directory (default: data/pdf)",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=str(DEFAULT_OUTPUT_DIR),
        help="Markdown output directory (default: data/processed_pymupdf4llm)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel PDF conversion workers (default: 4)",
    )
    parser.add_argument(
        "--skip-convert",
        action="store_true",
        help="Skip PDF conversion",
    )
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir)
    output_dir = Path(args.output_dir)

    if not args.skip_convert:
        convert_all_pdfs(pdf_dir, output_dir, workers=args.workers)

    print("\nData preparation complete.")
    print(f"  PDF: {pdf_dir}")
    print(f"  Markdown: {output_dir}")


if __name__ == "__main__":
    main()
