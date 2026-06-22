"""
LLM API 最小连通性测试：从 config.yaml 读取 model/api_base，调一问验证 base_url、key、模型是否都正确。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import OpenAI

from src.utils.helpers import load_config


def main():
    cfg = load_config()
    model_cfg = cfg["model"]
    name = model_cfg["name"]
    base_url = model_cfg["api_base"]

    api_key = (
        os.getenv("LLM_API_KEY")
        or os.getenv("GLM_API_KEY")
        or os.getenv("DASHSCOPE_API_KEY")
    )
    if not api_key:
        print(f"错误：未设置 LLM_API_KEY / GLM_API_KEY / DASHSCOPE_API_KEY 环境变量")
        sys.exit(1)

    print(f"端点: {base_url}")
    print(f"模型: {name}")
    print(f"key:  {api_key[:8]}...{api_key[-4:]}")
    print()

    client = OpenAI(api_key=api_key, base_url=base_url)

    try:
        resp = client.chat.completions.create(
            model=name,
            messages=[{"role": "user", "content": "请用一句话介绍你自己"}],
            temperature=0.0,
            max_tokens=100,
            extra_body={"thinking": {"type": "disabled"}},
        )
        print(f"返回模型: {resp.model}")
        print(f"回答: {resp.choices[0].message.content}")
        msg = resp.choices[0].message
        if hasattr(msg, "reasoning_content") and msg.reasoning_content:
            print(f"reasoning: {msg.reasoning_content[:200]}")
        print(f"prompt_tokens: {resp.usage.prompt_tokens}")
        print(f"completion_tokens: {resp.usage.completion_tokens}")
        print(f"total_tokens: {resp.usage.total_tokens}")
        print("\nAPI 连通性测试通过！")
    except Exception as e:
        print(f"API 调用失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
