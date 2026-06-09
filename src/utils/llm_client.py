"""
LLM 客户端封装 — 双渠道调用设计

主渠道：qwen-plus（性价比最优，文档分析能力强）
回退渠道：qwen-turbo（同一平台内的轻量模型，作为内部回退）

触发回退的条件：
- HTTP 429 rate_limited
- HTTP 5xx 服务端错误
- 网络连接错误

设计说明：双渠道结构是一种冗余/容错设计模式。当主模型因限流、服务端异常等原因不可用时，
自动切换到备用模型继续服务。这种设计在跨服务商场景下价值更大（不同服务商的审查机制、
限流策略、可用性互相独立），但本次比赛限定使用阿里云百炼，所以回退只是同一平台内不同
模型之间的切换。保留这个结构是为了展示"回退设计"这一工程思维，方便读者学习。
"""
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError


@dataclass
class TokenUsage:
    """单次调用的 Token 统计"""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0


@dataclass
class LLMResponse:
    """模型返回结果"""
    content: str
    usage: TokenUsage
    finish_reason: str = ""
    model: str = ""
    tool_calls: list = None


class LLMClient:
    """阿里云百炼双渠道客户端"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-plus",
        temperature: float = 0.0,
    ):
        api_key = api_key or os.getenv("DASHSCOPE_API_KEY")
        if not api_key:
            raise ValueError("缺少百炼 API Key，请设置 DASHSCOPE_API_KEY 环境变量")

        # 主渠道
        self.router_client = OpenAI(api_key=api_key, base_url=base_url)
        self.router_model = model

        # 回退渠道（同一平台内的轻量模型）
        self.fallback_client = OpenAI(api_key=api_key, base_url=base_url)
        self.fallback_model = "qwen-turbo"

        self.temperature = temperature
        self.total_usage = TokenUsage()

    @staticmethod
    def _should_fallback(error: Exception) -> bool:
        """判断是否需要回退到 turbo"""
        if isinstance(error, APIStatusError):
            status = error.status_code
            # 429: 限流；5xx: 服务端错误
            return status == 429 or status >= 500
        if isinstance(error, (APITimeoutError, APIConnectionError)):
            return True
        return False

    def _call_router(
        self,
        messages: list[dict],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """调用主渠道（qwen-plus）"""
        kwargs = dict(
            model=self.router_model,
            messages=messages,
            temperature=temperature if temperature is not None else self.temperature,
            extra_body={"enable_thinking": False},
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        resp = self.router_client.chat.completions.create(**kwargs)

        usage = TokenUsage(
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            total_tokens=resp.usage.total_tokens,
        )
        self.total_usage.prompt_tokens += usage.prompt_tokens
        self.total_usage.completion_tokens += usage.completion_tokens
        self.total_usage.total_tokens += usage.total_tokens

        msg = resp.choices[0].message
        return LLMResponse(
            content=msg.content or "",
            usage=usage,
            finish_reason=resp.choices[0].finish_reason or "",
            model=resp.model,
            tool_calls=getattr(msg, "tool_calls", None),
        )

    def _call_fallback(
        self,
        messages: list[dict],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """调用回退渠道（qwen-turbo）"""
        kwargs = dict(
            model=self.fallback_model,
            messages=messages,
            temperature=temperature if temperature is not None else self.temperature,
        )
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        resp = self.fallback_client.chat.completions.create(**kwargs)

        usage = TokenUsage(
            prompt_tokens=resp.usage.prompt_tokens,
            completion_tokens=resp.usage.completion_tokens,
            total_tokens=resp.usage.total_tokens,
        )
        self.total_usage.prompt_tokens += usage.prompt_tokens
        self.total_usage.completion_tokens += usage.completion_tokens
        self.total_usage.total_tokens += usage.total_tokens

        msg = resp.choices[0].message
        return LLMResponse(
            content=msg.content or "",
            usage=usage,
            finish_reason=resp.choices[0].finish_reason or "",
            model=resp.model,
            tool_calls=getattr(msg, "tool_calls", None),
        )

    def chat(
        self,
        messages: list[dict],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """发送 chat 请求，自动回退"""
        t0 = time.time()

        try:
            response = self._call_router(messages, max_tokens, temperature, tools, tool_choice)
            response.usage.latency_ms = (time.time() - t0) * 1000
            return response
        except Exception as e:
            if self._should_fallback(e):
                response = self._call_fallback(messages, max_tokens, temperature, tools, tool_choice)
                response.usage.latency_ms = (time.time() - t0) * 1000
                return response
            raise

    def reset_usage(self):
        """重置累计 Token 统计"""
        self.total_usage = TokenUsage()

    @property
    def usage_summary(self) -> str:
        u = self.total_usage
        return (
            f"prompt={u.prompt_tokens}, "
            f"completion={u.completion_tokens}, "
            f"total={u.total_tokens}"
        )
