"""
LLM 客户端 - 封装 llama-server HTTP API

支持同步请求和 SSE 流式输出。
"""

import json
import urllib.request
import urllib.error
from urllib.parse import urlparse
from typing import Any, Generator

from llm_core.config import get_config


class LLMError(Exception):
    """LLM 客户端错误"""
    pass


class ServerNotRunningError(LLMError):
    """llama-server 未运行"""
    pass


class LLMClient:
    """
    llama-server HTTP API 客户端。

    用法:
        client = LLMClient()
        if not client.check_health():
            raise ServerNotRunningError("llama-server 未运行")

        # 同步模式
        response = client.chat(messages, tools=TOOLS)

        # 流式模式
        for chunk in client.chat_stream(messages, tools=TOOLS):
            print(chunk, end="")
    """

    def __init__(self, base_url: str | None = None):
        config = get_config()
        self._base_url = (base_url or config.llm_url).rstrip("/")
        self._model = config.llm_model
        self._max_tokens = config.llm_max_tokens
        self._temperature = config.llm_temperature
        self._timeout = config.llm_timeout
        hostname = (urlparse(self._base_url).hostname or "").lower()
        self._loopback = hostname in {"localhost", "127.0.0.1", "::1"}
        self._opener = (
            urllib.request.build_opener(urllib.request.ProxyHandler({}))
            if self._loopback
            else None
        )

    def _open(self, request: urllib.request.Request, timeout: int):
        """Open a request, bypassing environment proxies for loopback servers."""
        if self._opener is not None:
            return self._opener.open(request, timeout=timeout)
        return urllib.request.urlopen(request, timeout=timeout)

    @property
    def base_url(self) -> str:
        return self._base_url

    def check_health(self, timeout: int = 5) -> bool:
        """检查 llama-server 是否运行。"""
        try:
            req = urllib.request.Request(
                f"{self._base_url}/v1/models",
                headers={"Content-Type": "application/json"},
            )
            with self._open(req, timeout=timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, TimeoutError):
            return False

    def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """
        发送聊天请求（同步模式）。

        Args:
            messages: 消息列表
            tools: 工具定义列表
            temperature: 温度参数（覆盖默认值）
            max_tokens: 最大 token 数（覆盖默认值）

        Returns:
            API 响应的完整 JSON 字典

        Raises:
            ServerNotRunningError: 服务未运行
            LLMError: 其他错误
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools

        try:
            req = urllib.request.Request(
                f"{self._base_url}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with self._open(req, timeout=self._timeout) as resp:
                return json.loads(resp.read())

        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionRefusedError) or "Connection refused" in str(e.reason):
                raise ServerNotRunningError(
                    f"❌ llama-server 未运行\n请先执行: llama-serve qwen36 &"
                ) from e
            raise LLMError(f"连接错误: {e}") from e
        except (json.JSONDecodeError, KeyError) as e:
            raise LLMError(f"响应解析错误: {e}") from e
        except Exception as e:
            raise LLMError(f"请求失败: {e}") from e

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Generator[dict, None, None]:
        """
        发送聊天请求（SSE 流式模式）。

        Yields:
            每个 SSE 事件的解析字典，格式:
            {"choices": [{"delta": {"content": "..."}}]}
            或
            {"choices": [{"delta": {"tool_calls": [...]}}]}

        Raises:
            ServerNotRunningError: 服务未运行
            LLMError: 其他错误
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
            "temperature": temperature if temperature is not None else self._temperature,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools

        try:
            req = urllib.request.Request(
                f"{self._base_url}/v1/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with self._open(req, timeout=self._timeout) as resp:
                for line in resp:
                    line = line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                        if data == "[DONE]":
                            break
                        try:
                            yield json.loads(data)
                        except json.JSONDecodeError:
                            continue

        except urllib.error.URLError as e:
            if isinstance(e.reason, ConnectionRefusedError) or "Connection refused" in str(e.reason):
                raise ServerNotRunningError(
                    f"❌ llama-server 未运行\n请先执行: llama-serve qwen36 &"
                ) from e
            raise LLMError(f"连接错误: {e}") from e
        except Exception as e:
            raise LLMError(f"流式请求失败: {e}") from e


def get_client(base_url: str | None = None) -> LLMClient:
    """获取 LLM 客户端实例。"""
    return LLMClient(base_url)
