"""
LLM Core - 本地大模型桌面助手共享库

提供统一的配置管理、工具执行、LLM客户端、Agent循环和安全审计。
"""

from llm_core.config import Config, get_config
from llm_core.tools import TOOLS, execute_tool, TOOL_NAMES
from llm_core.client import LLMClient
from llm_core.agent import AgentLoop
from llm_core.security import CommandValidator, AuditLogger

__all__ = [
    "Config", "get_config",
    "TOOLS", "execute_tool", "TOOL_NAMES",
    "LLMClient",
    "AgentLoop",
    "CommandValidator", "AuditLogger",
]