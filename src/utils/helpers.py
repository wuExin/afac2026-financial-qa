"""
工具函数模块。

职责：
1. Token 计数（基于 tiktoken）
2. 答案格式校验与规范化
3. 配置加载
4. 日志工具
5. 文件 I/O 辅助
"""

import os
import yaml
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional


def load_config(config_path: str = "config/config.yaml") -> Dict[str, Any]:
    """加载 YAML 配置文件"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_env(env_path: str = ".env"):
    """加载 .env 环境变量"""
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())


def load_json(file_path: Path) -> Any:
    """加载 JSON 文件"""
    with open(file_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(data: Any, file_path: Path, indent: int = 2):
    """保存 JSON 文件"""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=indent)


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """配置日志"""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("afac2026")


def count_tokens(text: str, model: str = "gpt-4") -> int:
    """使用 tiktoken 估算 Token 数"""
    import tiktoken
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding("cl100k_base")
    return len(encoding.encode(text))


def normalize_answer(answer: str, answer_format: str) -> str:
    """
    规范化答案格式：
    - mcq/tf: 取首个有效字母
    - multi: 字母去重排序，无分隔符
    """
    answer = answer.strip().upper()
    valid_letters = "".join(c for c in answer if c in "ABCD")

    if answer_format in ("mcq", "tf"):
        return valid_letters[0] if valid_letters else ""
    elif answer_format == "multi":
        return "".join(sorted(set(valid_letters)))
    return answer


def get_project_root() -> Path:
    """获取项目根目录"""
    return Path(__file__).resolve().parent.parent.parent
