"""
LLM 客户端封装 — OpenAI 兼容接口调用设计

主渠道：由 MODEL_NAME/API_BASE_URL/API Key 配置决定。
回退渠道：可选。通过 FALLBACK_MODEL_NAME 或 config.model.fallback_name 显式配置。

触发回退的条件：
- HTTP 429 rate_limited
- HTTP 5xx 服务端错误
- 网络连接错误

正式提交必须切回 Qwen 系列模型；日常实验可使用其它 OpenAI 兼容模型。
"""
import os
import time
from dataclasses import dataclass, field
from typing import Optional

from openai import OpenAI, APIStatusError, APITimeoutError, APIConnectionError

try:
    from anthropic import Anthropic
    from anthropic import APIStatusError as AnthropicAPIStatusError
    from anthropic import APITimeoutError as AnthropicAPITimeoutError
    from anthropic import APIConnectionError as AnthropicAPIConnectionError
    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False


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


def _is_anthropic_endpoint(base_url: str) -> bool:
    return "/anthropic" in base_url


class LLMClient:
    """阿里云百炼 / 智谱 GLM 双协议客户端（OpenAI 兼容 + Anthropic 兼容）"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        model: str = "qwen-plus",
        temperature: float = 0.0,
        fallback_model: str = "",
    ):
        is_qwen_endpoint = "dashscope.aliyuncs.com" in base_url or model.lower().startswith("qwen")
        is_anthropic = _is_anthropic_endpoint(base_url)
        if not api_key:
            if is_qwen_endpoint:
                api_key = os.getenv("DASHSCOPE_API_KEY")
            else:
                api_key = (
                    os.getenv("LLM_API_KEY")
                    or os.getenv("GLM_API_KEY")
                    or os.getenv("DASHSCOPE_API_KEY")
                )
        if not api_key and is_qwen_endpoint:
            raise ValueError("缺少 DashScope API Key，请设置 DASHSCOPE_API_KEY 后再使用 Qwen/DashScope")
        if not api_key:
            raise ValueError("缺少 API Key，请设置 LLM_API_KEY、DASHSCOPE_API_KEY 或 GLM_API_KEY")

        if is_anthropic and not _HAS_ANTHROPIC:
            raise ValueError("端点为 Anthropic 协议但未安装 anthropic SDK，请 pip install anthropic")

        # 主渠道
        if is_anthropic:
            self.router_client = Anthropic(api_key=api_key, base_url=base_url)
        else:
            self.router_client = OpenAI(api_key=api_key, base_url=base_url)
        self.router_model = model
        self.router_is_anthropic = is_anthropic

        # 回退渠道。为空时禁用，避免 GLM 测试时误回退到 Qwen 或反之。
        if is_anthropic:
            self.fallback_client = Anthropic(api_key=api_key, base_url=base_url)
        else:
            self.fallback_client = OpenAI(api_key=api_key, base_url=base_url)
        self.fallback_model = fallback_model
        self.fallback_is_anthropic = is_anthropic

        self.temperature = temperature
        self.total_usage = TokenUsage()

    @staticmethod
    def _extra_body_for_model(model: str) -> Optional[dict]:
        """为不同模型传专属参数（OpenAI 协议路径专用）。"""
        m = model.lower()
        if m.startswith("qwen"):
            return {"enable_thinking": False}
        if m.startswith("glm"):
            return {"thinking": {"type": "disabled"}}
        return None

    @staticmethod
    def _should_fallback(error: Exception) -> bool:
        """判断是否需要回退到 fallback"""
        if isinstance(error, APIStatusError):
            status = error.status_code
            return status == 429 or status >= 500
        if isinstance(error, (APITimeoutError, APIConnectionError)):
            return True
        if _HAS_ANTHROPIC:
            if isinstance(error, AnthropicAPIStatusError):
                status = error.status_code
                return status == 429 or status >= 500
            if isinstance(error, (AnthropicAPITimeoutError, AnthropicAPIConnectionError)):
                return True
        return False

    def _call_anthropic(
        self,
        client,
        model: str,
        messages: list[dict],
        max_tokens: Optional[int],
        temperature: Optional[float],
    ) -> LLMResponse:
        """Anthropic 协议调用（智谱 Coding Plan 等）。"""
        system_parts = []
        user_messages = []
        for m in messages:
            if m.get("role") == "system":
                system_parts.append(m.get("content", ""))
            else:
                user_messages.append(m)

        kwargs = dict(
            model=model,
            messages=user_messages,
            max_tokens=max_tokens or 4096,
            temperature=temperature if temperature is not None else self.temperature,
        )
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)

        resp = client.messages.create(**kwargs)

        text_parts = []
        tool_calls = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append(block)

        prompt_tokens = resp.usage.input_tokens
        completion_tokens = resp.usage.output_tokens
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        self.total_usage.prompt_tokens += usage.prompt_tokens
        self.total_usage.completion_tokens += usage.completion_tokens
        self.total_usage.total_tokens += usage.total_tokens

        return LLMResponse(
            content="".join(text_parts),
            usage=usage,
            finish_reason=resp.stop_reason or "",
            model=resp.model,
            tool_calls=tool_calls or None,
        )

    def _call_openai(
        self,
        client,
        model: str,
        messages: list[dict],
        max_tokens: Optional[int],
        temperature: Optional[float],
        tools: Optional[list],
        tool_choice: Optional[str],
    ) -> LLMResponse:
        """OpenAI 协议调用（Qwen / 智谱 paas/v4 等）。"""
        kwargs = dict(
            model=model,
            messages=messages,
            temperature=temperature if temperature is not None else self.temperature,
        )
        extra_body = self._extra_body_for_model(model)
        if extra_body:
            kwargs["extra_body"] = extra_body
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if tools is not None:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"

        resp = client.chat.completions.create(**kwargs)

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

    def _call_router(
        self,
        messages: list[dict],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """调用主渠道"""
        if self.router_is_anthropic:
            return self._call_anthropic(
                self.router_client, self.router_model, messages, max_tokens, temperature
            )
        return self._call_openai(
            self.router_client, self.router_model, messages,
            max_tokens, temperature, tools, tool_choice,
        )

    def _call_fallback(
        self,
        messages: list[dict],
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        tools: Optional[list] = None,
        tool_choice: Optional[str] = None,
    ) -> LLMResponse:
        """调用回退渠道"""
        if self.fallback_is_anthropic:
            return self._call_anthropic(
                self.fallback_client, self.fallback_model, messages, max_tokens, temperature
            )
        return self._call_openai(
            self.fallback_client, self.fallback_model, messages,
            max_tokens, temperature, tools, tool_choice,
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
            if self.fallback_model and self._should_fallback(e):
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
