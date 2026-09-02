"""
工具定义与执行 - 统一的工具注册和执行

所有工具在此定义，所有组件共享同一份实现。
"""

import subprocess
from pathlib import Path
from typing import Any, Callable, Optional

from llm_core.config import get_config
from llm_core.security import get_validator, get_auditor


# ============================================================
# 确认回调机制
# ============================================================
# 前端可通过 set_confirmation_handler() 注册确认函数，用于在执行
# "警告级"命令前征得用户同意。回调签名:
#   handler(tool_name: str, args: dict, reason: str) -> bool
# 返回 True 表示同意执行，False 表示拒绝。
# 未注册回调时，警告级命令默认被拒绝（fail-closed）。
_confirm_handler: Optional[Callable[[str, dict, str], bool]] = None


def set_confirmation_handler(handler: Optional[Callable[[str, dict, str], bool]]) -> None:
    """注册命令执行前的确认回调（GUI/CLI 等交互式前端使用）。"""
    global _confirm_handler
    _confirm_handler = handler


def get_confirmation_handler() -> Optional[Callable[[str, dict, str], bool]]:
    return _confirm_handler


# ============================================================
# 路径安全策略
# ============================================================
# write_file 禁止写入的系统目录前缀
_WRITE_BLOCKED_PREFIXES: tuple[str, ...] = (
    "/etc", "/usr", "/bin", "/sbin", "/lib", "/lib64",
    "/boot", "/root", "/var", "/opt", "/sys", "/proc", "/dev",
    "/run", "/snap",
)

# 读写都禁止触及的敏感目录（路径任意一层命中即拒绝）
_SENSITIVE_DIRS: frozenset[str] = frozenset({
    ".ssh", ".gnupg", ".aws", ".kube", ".docker", ".pki", ".password-store",
})

# read_file 额外禁止读取的敏感文件
_READ_BLOCKED_FILES: tuple[str, ...] = ("/etc/shadow", "/etc/gshadow")
_READ_BLOCKED_PREFIXES: tuple[str, ...] = ("/proc", "/sys", "/dev")
_READ_BLOCKED_NAMES: frozenset[str] = frozenset({
    ".env", ".netrc", ".npmrc", ".pypirc", "credentials", "credentials.json",
})


def _path_policy_violation(path: Path, *, write: bool) -> Optional[str]:
    """检查路径是否触碰敏感位置，命中则返回拒绝原因，否则返回 None。"""
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path

    s = str(resolved)
    if write:
        for prefix in _WRITE_BLOCKED_PREFIXES:
            if s == prefix or s.startswith(prefix + "/"):
                return f"禁止写入系统目录: {prefix}"
    else:
        for blocked in _READ_BLOCKED_FILES:
            if s == blocked:
                return f"禁止读取敏感文件: {blocked}"
        for prefix in _READ_BLOCKED_PREFIXES:
            if s == prefix or s.startswith(prefix + "/"):
                return f"禁止读取虚拟/设备目录: {prefix}"
        if resolved.name.lower() in _READ_BLOCKED_NAMES:
            return f"禁止读取敏感文件名: {resolved.name}"

    for part in resolved.parts:
        if part in _SENSITIVE_DIRS:
            return f"禁止访问敏感目录: {part}"
    return None


def _is_within_allowed_read_root(path: Path) -> bool:
    """Whether a path is inside one of the explicitly configured read roots."""
    roots = get_config().get("security.allowed_read_roots", [])
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path.absolute()
    for raw_root in roots if isinstance(roots, list) else []:
        try:
            root = Path(str(raw_root)).expanduser().resolve()
            resolved.relative_to(root)
            return True
        except (OSError, ValueError):
            continue
    return False


def _confirm_or_reject(tool_name: str, args: dict, reason: str, auditor) -> Optional[str]:
    """Run the frontend confirmation gate; return a rejection message or None."""
    handler = get_confirmation_handler()
    if handler is None:
        msg = f"🚫 {reason}\n当前前端不支持确认，已拒绝执行。"
        auditor.log(tool_name, args, msg, success=False)
        return msg
    if not handler(tool_name, args, reason):
        msg = f"🚫 用户拒绝执行: {reason}"
        auditor.log(tool_name, args, msg, success=False)
        return msg
    return None


# ============================================================
# 工具定义（OpenAI function calling 格式）
# ============================================================

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "execute_command",
            "description": "执行 fish shell 命令并返回输出。可以查询系统信息、操作文件、运行程序等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 fish shell 命令",
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，支持 ~ 展开",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "写入内容到文件",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径，支持 ~ 展开",
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "列出目录内容",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径，支持 ~ 展开，默认为当前目录",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_up",
            "description": "调高系统音量",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "volume_down",
            "description": "调低系统音量",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brightness_up",
            "description": "调高屏幕亮度",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "brightness_down",
            "description": "调低屏幕亮度",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

TOOL_NAMES: list[str] = [t["function"]["name"] for t in TOOLS]


# ============================================================
# 工具执行函数
# ============================================================

def _execute_command(args: dict) -> str:
    """执行 shell 命令（带安全验证）"""
    config = get_config()
    validator = get_validator()
    auditor = get_auditor()

    command = args.get("command", "")
    if not command.strip():
        return "错误: 空命令"

    # 安全验证
    validation = validator.validate(command)
    if not validation.allowed:
        auditor.log("execute_command", args, validation.reason, success=False)
        return validation.reason

    confirm_all = config.get("security.confirm_all_commands", True)
    if validation.requires_confirmation or confirm_all:
        reason = validation.reason or f"⚠️ 即将执行 shell 命令:\n   {command[:200]}"
        rejection = _confirm_or_reject("execute_command", args, reason, auditor)
        if rejection:
            return rejection
        result = f"{reason}\n已确认执行..."
    else:
        result = ""

    try:
        proc = subprocess.run(
            ["fish", "-c", command],
            capture_output=True,
            text=True,
            timeout=config.command_timeout,
            cwd=str(Path.home()),
        )
        output = proc.stdout
        if proc.stderr:
            output += f"\n[stderr]\n{proc.stderr}"
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"

        final = (result + "\n" + output).strip() if result else (output or "(无输出)")

        auditor.log("execute_command", args, final, success=True)
        return final[:5000] if len(final) > 5000 else final

    except subprocess.TimeoutExpired:
        msg = f"错误: 命令执行超时 ({config.command_timeout}s)"
        auditor.log("execute_command", args, msg, success=False)
        return msg
    except FileNotFoundError:
        msg = "错误: fish shell 未找到"
        auditor.log("execute_command", args, msg, success=False)
        return msg


def _read_file(args: dict) -> str:
    """读取文件内容（限普通文件、限量读取、敏感路径拒绝）"""
    config = get_config()
    auditor = get_auditor()

    path = Path(args["path"]).expanduser()
    if not path.exists():
        auditor.log("read_file", args, f"文件不存在: {path}", success=False)
        return f"文件不存在: {path}"
    if path.is_dir():
        auditor.log("read_file", args, f"路径是目录: {path}", success=False)
        return f"路径是目录: {path}"

    violation = _path_policy_violation(path, write=False)
    if violation:
        msg = f"🚫 {violation}: {path}"
        auditor.log("read_file", args, msg, success=False)
        return msg

    if config.get("security.confirm_reads_outside_roots", True) and not _is_within_allowed_read_root(path):
        reason = f"⚠️ 即将读取允许目录之外的文件: {path.resolve()}"
        rejection = _confirm_or_reject("read_file", args, reason, auditor)
        if rejection:
            return rejection

    # 只读普通文件，避免 FIFO/设备文件阻塞或无界读取
    try:
        if not path.is_file():
            msg = f"错误: 不是普通文件 {path}"
            auditor.log("read_file", args, msg, success=False)
            return msg
    except OSError as e:
        msg = f"错误: {e}"
        auditor.log("read_file", args, msg, success=False)
        return msg

    try:
        # 先读大小限制的字节数，避免大文件整块进内存
        max_chars = config.read_max_chars
        with open(path, "rb") as f:
            data = f.read(max_chars)
        content = data.decode("utf-8", errors="replace")
        truncated = len(data) >= max_chars
        preview = content + ("\n...(已截断)" if truncated else "")
        auditor.log("read_file", args, f"成功读取 {len(content)} 字符", success=True)
        return preview
    except PermissionError:
        msg = f"错误: 无权限读取 {path}"
        auditor.log("read_file", args, msg, success=False)
        return msg
    except Exception as e:
        msg = f"错误: {e}"
        auditor.log("read_file", args, msg, success=False)
        return msg


def _write_file(args: dict) -> str:
    """写入文件内容（禁止写系统目录/敏感目录）"""
    auditor = get_auditor()

    path = Path(args["path"]).expanduser()
    content = args.get("content", "")

    violation = _path_policy_violation(path, write=True)
    if violation:
        msg = f"🚫 {violation}: {path}"
        auditor.log("write_file", args, msg, success=False)
        return msg


    if get_config().get("security.confirm_writes", True):
        reason = f"⚠️ 即将写入文件: {path.resolve()}"
        rejection = _confirm_or_reject("write_file", args, reason, auditor)
        if rejection:
            return rejection

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        msg = f"已写入 {path} ({len(content)} 字符)"
        auditor.log("write_file", args, msg, success=True)
        return msg
    except PermissionError:
        msg = f"错误: 无权限写入 {path}"
        auditor.log("write_file", args, msg, success=False)
        return msg
    except Exception as e:
        msg = f"错误: {e}"
        auditor.log("write_file", args, msg, success=False)
        return msg


def _list_directory(args: dict) -> str:
    """列出目录内容"""
    config = get_config()
    auditor = get_auditor()

    path = Path(args.get("path", ".")).expanduser()
    if not path.exists():
        auditor.log("list_directory", args, f"目录不存在: {path}", success=False)
        return f"目录不存在: {path}"
    if not path.is_dir():
        auditor.log("list_directory", args, f"不是目录: {path}", success=False)
        return f"不是目录: {path}"

    violation = _path_policy_violation(path, write=False)
    if violation:
        msg = f"🚫 {violation}: {path}"
        auditor.log("list_directory", args, msg, success=False)
        return msg

    if config.get("security.confirm_reads_outside_roots", True) and not _is_within_allowed_read_root(path):
        reason = f"⚠️ 即将列出允许目录之外的目录: {path.resolve()}"
        rejection = _confirm_or_reject("list_directory", args, reason, auditor)
        if rejection:
            return rejection

    try:
        items = []
        for item in sorted(path.iterdir()):
            prefix = "📁 " if item.is_dir() else "📄 "
            items.append(f"{prefix}{item.name}")
        max_items = config.get("tools.list_max_items", 50)
        result = "\n".join(items[:max_items])
        if len(items) > max_items:
            result += f"\n... 还有 {len(items) - max_items} 项"
        auditor.log("list_directory", args, f"列出 {len(items)} 项", success=True)
        return result or "(空目录)"
    except PermissionError:
        msg = f"错误: 无权限访问 {path}"
        auditor.log("list_directory", args, msg, success=False)
        return msg


def _run_checked(name: str, args: dict, auditor, cmd_list: list, ok_msg: str, fail_msg: str) -> str:
    """执行系统命令并检查 returncode，失败时如实报告。"""
    try:
        proc = subprocess.run(cmd_list, capture_output=True, timeout=5)
    except Exception as e:
        auditor.log(name, args, str(e), success=False)
        return f"{fail_msg}: {e}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[:200]
        auditor.log(name, args, f"{fail_msg}: {detail}", success=False)
        return f"{fail_msg}: {detail}"
    auditor.log(name, args, "成功", success=True)
    return ok_msg


def _volume_up(args: dict) -> str:
    """调高音量"""
    auditor = get_auditor()
    config = get_config()
    step = config.get('tools.volume_step', '5%')
    return _run_checked(
        "volume_up", args, auditor,
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"+{step}"],
        "音量已调高 🔊", "音量调节失败",
    )


def _volume_down(args: dict) -> str:
    """调低音量"""
    auditor = get_auditor()
    config = get_config()
    step = config.get('tools.volume_step', '5%')
    return _run_checked(
        "volume_down", args, auditor,
        ["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"-{step}"],
        "音量已调低 🔉", "音量调节失败",
    )


def _brightness_up(args: dict) -> str:
    """调高亮度"""
    auditor = get_auditor()
    config = get_config()
    step = config.get('tools.brightness_step', '5%')
    return _run_checked(
        "brightness_up", args, auditor,
        ["brightnessctl", "set", f"+{step}"],
        "亮度已调高 ☀️", "亮度调节失败",
    )


def _brightness_down(args: dict) -> str:
    """调低亮度"""
    auditor = get_auditor()
    config = get_config()
    step = config.get('tools.brightness_step', '5%')
    return _run_checked(
        "brightness_down", args, auditor,
        ["brightnessctl", "set", f"{step}-"],
        "亮度已调低 🌙", "亮度调节失败",
    )


# 工具执行映射
_TOOL_HANDLERS: dict[str, Any] = {
    "execute_command": _execute_command,
    "read_file": _read_file,
    "write_file": _write_file,
    "list_directory": _list_directory,
    "volume_up": _volume_up,
    "volume_down": _volume_down,
    "brightness_up": _brightness_up,
    "brightness_down": _brightness_down,
}


def execute_tool(name: str, args: dict) -> str:
    """
    执行指定的工具。

    Args:
        name: 工具名称
        args: 工具参数

    Returns:
        工具执行结果字符串
    """
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return f"未知工具: {name}"
    try:
        return handler(args)
    except Exception as e:
        return f"工具执行异常 [{name}]: {e}"


def execute_fixed_command(name: str) -> str:
    """
    执行固定的系统命令（不经过LLM，直接调用）。
    供快捷按钮使用。

    Args:
        name: 命令名 (volume_up, volume_down, brightness_up, brightness_down, mute)

    Returns:
        执行结果字符串
    """
    commands: dict[str, tuple[str, str]] = {
        "volume_up": ("pactl set-sink-volume @DEFAULT_SINK@ +5%", "音量+"),
        "volume_down": ("pactl set-sink-volume @DEFAULT_SINK@ -5%", "音量-"),
        "brightness_up": ("brightnessctl set +5%", "亮度+"),
        "brightness_down": ("brightnessctl set 5%-", "亮度-"),
        "mute": ("pactl set-sink-mute @DEFAULT_SINK@ toggle", "静音切换"),
    }

    if name not in commands:
        return f"未知命令: {name}"

    cmd, label = commands[name]
    try:
        proc = subprocess.run(["fish", "-c", cmd], capture_output=True, timeout=5)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:200]
            return f"❌ {label} 失败: {detail}"
        return f"✅ {label}"
    except Exception as e:
        return f"❌ {label} 失败: {e}"
