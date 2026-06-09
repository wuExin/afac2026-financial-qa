"""
百炼 API 最小连通性测试：只调一问，验证 base_url、key、模型是否都正确。
"""
import os
from openai import OpenAI

api_key = os.getenv("DASHSCOPE_API_KEY")
if not api_key:
    print("错误：未设置 DASHSCOPE_API_KEY 环境变量")
    exit(1)

client = OpenAI(
    api_key=api_key,
    base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
)

try:
    resp = client.chat.completions.create(
        model="qwen-plus",
        messages=[{"role": "user", "content": "请用一句话介绍你自己"}],
        temperature=0.0,
        max_tokens=100,
    )
    print(f"模型: {resp.model}")
    print(f"回答: {resp.choices[0].message.content}")
    print(f"prompt_tokens: {resp.usage.prompt_tokens}")
    print(f"completion_tokens: {resp.usage.completion_tokens}")
    print(f"total_tokens: {resp.usage.total_tokens}")
    print("\nAPI 连通性测试通过！")
except Exception as e:
    print(f"API 调用失败: {e}")
    exit(1)
