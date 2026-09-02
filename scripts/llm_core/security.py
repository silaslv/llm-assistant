"""
安全模块 - 命令验证 + 审计日志

CommandValidator: 检测危险命令模式，阻止恶意操作
AuditLogger: 记录所有工具调用到 JSONL 审计日志
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from llm_core.config import get_config


# 危险命令模式（正则表达式）
DANGEROUS_PATTERNS: list[tuple[str, str]] = [
    # (pattern, description)
    (r"\brm\s+(-[rRf]+\s+)*/", "删除根目录"),
    (r"\brm\s+(-[rRf]+\s+)*~", "删除用户目录"),
    (r"\brm\s+-rf\b", "递归强制删除"),
    (r"\bsudo\b", "提权操作"),
    (r"\bchmod\s+777\b", "开放所有权限"),
    (r"\bchmod\s+-R\s+777\b", "递归开放所有权限"),
    (r"\bmkfs\.", "格式化文件系统"),
    (r"\bdd\s+if=", "磁盘直接写入"),
    (r":\(\)\s*\{", "fork炸弹"),
    (r"\bcurl\b.*\|\s*(?:ba)?sh\b", "curl管道到shell"),
    (r"\bwget\b.*\|\s*(?:ba)?sh\b", "wget管道到shell"),
    (r">\s*/dev/sd[a-z]", "覆盖磁盘设备"),
    (r"\bmv\s+.*\s+/etc/", "移动文件到/etc"),
    (r"\bchown\s+-R\s+.*\s+/", "递归修改根目录所有者"),
    (r"\bfind\b.*-exec\b.*rm\b", "find执行删除"),
    (r"\bshutdown\b", "系统关机"),
    (r"\breboot\b", "系统重启"),
    (r"\b:\(\s*\)", "fork炸弹变体"),
]

# 需要二次确认的危险模式（较温和）
WARNING_PATTERNS: list[tuple[str, str]] = [
    (r"\bkill\s+-9\b", "强制终止进程"),
    (r"\bpkill\b", "批量终止进程"),
    (r"\bchmod\b", "修改权限"),
    (r"\bchown\b", "修改所有者"),
    (r"\bpasswd\b", "修改密码"),
    (r"\buseradd\b", "添加用户"),
    (r"\buserdel\b", "删除用户"),
    (r"\brm\b", "删除文件"),
    (r"\bmv\b.*\s+/", "移动文件到系统目录"),
    (r"\biptables\b", "修改防火墙"),
    (r"\bsystemctl\s+(?:stop|disable|mask)\b", "停止/禁用系统服务"),
]


class CommandValidationResult:
    """命令验证结果"""
    def __init__(self, allowed: bool, reason: str = "", requires_confirmation: bool = False):
        self.allowed = allowed
        self.reason = reason
        self.requires_confirmation = requires_confirmation


class CommandValidator:
    """命令验证器 - 检测并阻止危险命令"""

    def __init__(self, require_confirmation: bool = True):
        self.require_confirmation = require_confirmation

    def validate(self, command: str) -> CommandValidationResult:
        """
        验证命令是否安全。
        返回 CommandValidationResult 表示是否允许、原因、是否需要二次确认。
        """
        if not command or not command.strip():
            return CommandValidationResult(False, "空命令")

        # 检查危险模式
        for pattern, description in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return CommandValidationResult(
                    False,
                    f"🚫 危险操作被阻止: {description}\n"
                    f"   匹配模式: {pattern}\n"
                    f"   命令: {command[:200]}"
                )

        # 检查警告模式
        if self.require_confirmation:
            for pattern, description in WARNING_PATTERNS:
                if re.search(pattern, command, re.IGNORECASE):
                    return CommandValidationResult(
                        True,
                        f"⚠️ 需要确认: {description}\n"
                        f"   命令: {command[:200]}",
                        requires_confirmation=True
                    )

        return CommandValidationResult(True)


class AuditLogger:
    """审计日志 - 记录所有工具调用"""

    def __init__(self, log_path: str | None = None):
        if log_path is None:
            log_path = os.path.expanduser("~/.config/llm-assistant/audit.log")
        self._path = Path(log_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._path.touch(mode=0o600, exist_ok=True)
            self._path.chmod(0o600)
        except OSError:
            pass

    @staticmethod
    def _safe_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return an audit-safe copy without file contents or common secrets."""
        safe: dict[str, Any] = {}
        sensitive_keys = {"content", "password", "passwd", "token", "secret", "api_key", "authorization"}
        for key, value in arguments.items():
            if key.lower() in sensitive_keys:
                if key == "content" and isinstance(value, str):
                    safe[key] = f"<redacted:{len(value)} chars>"
                else:
                    safe[key] = "<redacted>"
            else:
                safe[key] = value
        return safe

    def log(self, tool_name: str, arguments: dict, result: str, success: bool = True) -> None:
        """记录一条工具调用审计日志"""
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "tool": tool_name,
            "arguments": self._safe_arguments(tool_name, arguments),
            "result_preview": result[:200] if result else "",
            "success": success,
        }
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # 静默失败，不影响主流程


# 模块级单例
_validator: CommandValidator | None = None
_auditor: AuditLogger | None = None


def get_validator() -> CommandValidator:
    global _validator
    if _validator is None:
        require_confirmation = get_config().get("security.require_confirmation", True)
        _validator = CommandValidator(require_confirmation=require_confirmation)
    return _validator


def get_auditor() -> AuditLogger:
    global _auditor
    if _auditor is None:
        _auditor = AuditLogger()
    return _auditor
