"""smolagents 自定义工具集 — grep 版

用系统 grep 命令做关键词搜索，比 BM25 更快更精确。
"""

from smolagents import tool
from typing import Optional
import subprocess
import tempfile
import os
from pathlib import Path

_doc_base: Path = None  # 文档根目录，如 data/processed_pymupdf4llm


def init_tools(doc_base: Path):
    global _doc_base
    _doc_base = doc_base


@tool
def grep_document(query: str, doc_path: str) -> str:
    """在指定文档目录中搜索关键词，返回匹配行及其上下文。

    这是最常用的工具。当你需要查找某个条款、数据、公式时，先用这个工具在文档中定位。

    Args:
        query: 搜索关键词，如 "现金价值"、"等待期"、"营业收入"
        doc_path: 文档目录路径，从题目的 doc_paths 中获取。
                  例如 "data/processed_pymupdf4llm/insurance/1"

    Returns:
        匹配的行及上下文（每处匹配最多显示前后 3 行），最多返回 5000 字符。
    """
    p = Path(doc_path)
    if not p.exists():
        return f"错误：路径不存在: {doc_path}"

    try:
        result = subprocess.run(
            ["grep", "-rn", "-C", "3", query, str(p)],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        if not output:
            return f"在 {doc_path} 中未找到 '{query}'"
        if len(output) > 5000:
            output = output[:5000] + "\n... (结果过长，已截断)"
        return output
    except subprocess.TimeoutExpired:
        return f"搜索超时: {query}"
    except Exception as e:
        return f"搜索失败: {e}"


@tool
def read_page(page_path: str) -> str:
    """读取指定页面的完整内容。

    当 grep_document 找到相关位置后，用这个工具读取整页内容以获取完整上下文。

    Args:
        page_path: 页面文件路径，如 "data/processed_pymupdf4llm/insurance/1/page_0008.md"

    Returns:
        页面的完整文本内容（最多 8000 字符）。
    """
    p = Path(page_path)
    if not p.exists():
        return f"错误：文件不存在: {page_path}"
    try:
        content = p.read_text(encoding="utf-8")
        if len(content) > 8000:
            content = content[:8000] + "\n... (内容过长，已截断)"
        return content
    except Exception as e:
        return f"读取失败: {e}"


@tool
def run_python(code: str) -> str:
    """执行 Python 代码进行数值计算。

    所有涉及数值计算、排序、对比的操作必须用这个工具，不要自己心算。

    Args:
        code: Python 代码字符串。使用 print() 输出结果。

    Returns:
        代码的标准输出。
    """
    forbidden = ["import ", "__import__", "exec(", "eval(",
                 "subprocess", "os.", "sys.", "shutil", "pathlib", "open("]
    for kw in forbidden:
        if kw in code:
            return f"错误：禁止的操作 '{kw}'"

    if len(code) > 3000:
        return "错误：代码过长"

    try:
        result = subprocess.run(
            ["python3", "-c", code],
            capture_output=True, text=True, timeout=10,
            cwd=tempfile.gettempdir(),
        )
        if result.returncode != 0:
            return f"执行错误:\n{result.stderr}"
        return result.stdout.strip() or "执行成功（无输出）"
    except subprocess.TimeoutExpired:
        return "错误：超时（10秒）"
    except Exception as e:
        return f"执行失败: {e}"