"""LLM 客户端

基于 smolagents 的 OpenAIServerModel，支持百炼和阶跃星辰双平台切换。
通过环境变量 DASHSCOPE_API_KEY / API_BASE_URL / MODEL_NAME 控制。

阶跃星辰兼容性处理：
    阶跃 API (step-router-v1) 对 stop 参数格式极其敏感。smolagents 传入的
    stop_sequences 与阶跃内置的特殊 stop token 混合后产生 dict 格式条目，
    导致 API 返回 400 错误。本模块 override generate 方法，对阶跃平台
    彻底移除 stop_sequences 参数。
"""

import sys
import os

_LOCAL_SITE = os.path.expanduser("~/.local/lib/python3.10/site-packages")
_SYSTEM_DIST = "/usr/lib/python3/dist-packages"
for _p in [_LOCAL_SITE, _SYSTEM_DIST]:
    if _p not in sys.path and os.path.isdir(_p):
        sys.path.insert(0, _p)

from smolagents import OpenAIServerModel


class StepFunCompatibleModel(OpenAIServerModel):
    """兼容阶跃星辰 API 的 Model wrapper

    对阶跃平台，不传 stop 参数，改用 max_tokens 限制输出。
    """

    def __init__(self, model_id: str, api_key: str, api_base: str, is_stepfun: bool = False, **kwargs):
        super().__init__(model_id=model_id, api_key=api_key, api_base=api_base, **kwargs)
        self.is_stepfun = is_stepfun

    def generate(
        self,
        messages,
        stop_sequences=None,
        response_format=None,
        tools_to_call_from=None,
        **kwargs,
    ):
        if self.is_stepfun:
            stop_sequences = None
            # 用 max_tokens 兜底，防止模型无限生成（正常情况不会触发）
            if "max_tokens" not in kwargs:
                kwargs["max_tokens"] = 4096
        return super().generate(
            messages=messages,
            stop_sequences=stop_sequences,
            response_format=response_format,
            tools_to_call_from=tools_to_call_from,
            **kwargs,
        )


def get_model(config: dict = None) -> StepFunCompatibleModel:
    """创建模型实例

    优先级：环境变量 > config.yaml > 默认值
    """
    if config is None:
        config = {}

    model_cfg = config.get("model", {})

    api_key = os.getenv("DASHSCOPE_API_KEY")
    base_url = os.getenv(
        "API_BASE_URL",
        model_cfg.get("api_base", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
    )
    model_name = os.getenv("MODEL_NAME", model_cfg.get("name", "qwen-plus"))
    temperature = model_cfg.get("temperature", 0.0)

    is_stepfun = "stepfun" in base_url

    return StepFunCompatibleModel(
        model_id=model_name,
        api_key=api_key,
        api_base=base_url,
        temperature=temperature,
        is_stepfun=is_stepfun,
    )