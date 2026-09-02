"""
测试: 工具模块 - execute_tool, execute_fixed_command
"""

import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm_core.tools import (
    execute_tool,
    execute_fixed_command,
    TOOLS,
    TOOL_NAMES,
    set_confirmation_handler,
)


class TestToolDefinitions:
    """工具定义测试"""

    def test_all_tools_have_required_fields(self):
        for tool in TOOLS:
            assert tool["type"] == "function"
            func = tool["function"]
            assert "name" in func
            assert "description" in func
            assert "parameters" in func

    def test_tool_names_unique(self):
        names = [t["function"]["name"] for t in TOOLS]
        assert len(names) == len(set(names)), "工具名称不唯一"

    def test_tool_names_match_list(self):
        names = [t["function"]["name"] for t in TOOLS]
        assert names == TOOL_NAMES

    def test_eight_tools_registered(self):
        assert len(TOOLS) == 8
        assert "execute_command" in TOOL_NAMES
        assert "read_file" in TOOL_NAMES
        assert "write_file" in TOOL_NAMES
        assert "list_directory" in TOOL_NAMES
        assert "volume_up" in TOOL_NAMES
        assert "volume_down" in TOOL_NAMES
        assert "brightness_up" in TOOL_NAMES
        assert "brightness_down" in TOOL_NAMES


class TestExecuteTool:
    """工具执行测试"""

    def test_unknown_tool(self):
        result = execute_tool("nonexistent_tool", {})
        assert "未知" in result

    def test_read_file_not_found(self):
        result = execute_tool("read_file", {"path": "/tmp/nonexistent_xyz_file_12345"})
        assert "不存在" in result or "not found" in result.lower()

    def test_read_file_is_directory(self):
        result = execute_tool("read_file", {"path": "/tmp"})
        assert "目录" in result or "directory" in result.lower()

    def test_read_file_success(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("Hello World\nTest content")
            path = f.name

        try:
            result = execute_tool("read_file", {"path": path})
            assert "Hello World" in result
        finally:
            os.unlink(path)

    def test_write_file(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name

        try:
            set_confirmation_handler(lambda tool, args, reason: True)
            result = execute_tool("write_file", {"path": path, "content": "test content"})
            assert "已写入" in result or "written" in result.lower()

            with open(path) as f:
                assert f.read() == "test content"
        finally:
            set_confirmation_handler(None)
            os.unlink(path)

    def test_list_directory(self):
        result = execute_tool("list_directory", {"path": "/tmp"})
        assert len(result) > 0

    def test_list_directory_nonexistent(self):
        result = execute_tool("list_directory", {"path": "/tmp/nonexistent_xyz_dir_12345"})
        assert "不存在" in result or "not found" in result.lower()

    @patch("subprocess.run")
    def test_volume_up(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = execute_tool("volume_up", {})
        assert "音量" in result

    @patch("subprocess.run")
    def test_brightness_down(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        result = execute_tool("brightness_down", {})
        assert "亮度" in result


class TestExecuteFixedCommand:
    """固定命令测试"""

    def test_known_commands(self):
        for cmd in ["volume_up", "volume_down", "brightness_up", "brightness_down", "mute"]:
            result = execute_fixed_command(cmd)
            assert "✅" in result or "❌" in result, f"命令 {cmd} 返回异常: {result}"

    def test_unknown_command(self):
        result = execute_fixed_command("nonexistent")
        assert "未知" in result


class TestPathSafety:
    """路径安全策略测试"""

    def test_write_to_system_dir_blocked(self):
        result = execute_tool("write_file", {"path": "/etc/llm_test.txt", "content": "x"})
        assert "禁止" in result

    def test_write_to_sensitive_dir_blocked(self):
        result = execute_tool("write_file", {"path": "~/.ssh/llm_test.pub", "content": "x"})
        assert "禁止" in result

    def test_write_to_tmp_allowed(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            path = f.name
        try:
            set_confirmation_handler(lambda tool, args, reason: True)
            result = execute_tool("write_file", {"path": path, "content": "x"})
            assert "已写入" in result
        finally:
            set_confirmation_handler(None)
            os.unlink(path)

    def test_read_shadow_blocked(self):
        result = execute_tool("read_file", {"path": "/etc/shadow"})
        assert "禁止" in result

    def test_read_device_refused(self):
        result = execute_tool("read_file", {"path": "/dev/zero"})
        assert "禁止" in result

    def test_read_proc_refused(self):
        result = execute_tool("read_file", {"path": "/proc/self/environ"})
        assert "禁止" in result


class TestConfirmationGate:
    """警告级命令确认机制测试（使用 chmod 触发警告级，避免命中危险模式）"""

    def test_warning_refused_without_handler(self):
        set_confirmation_handler(None)
        result = execute_tool("execute_command", {"command": "chmod 644 nonexistent_xyz_confirm"})
        assert "不支持确认" in result or "拒绝" in result

    def test_safe_command_also_requires_confirmation(self):
        set_confirmation_handler(None)
        result = execute_tool("execute_command", {"command": "printf ok"})
        assert "不支持确认" in result or "拒绝" in result

    def test_write_refused_without_handler(self):
        set_confirmation_handler(None)
        result = execute_tool("write_file", {"path": "/tmp/llm-no-confirm", "content": "x"})
        assert "不支持确认" in result or "拒绝" in result

    def test_read_outside_roots_refused_without_handler(self):
        set_confirmation_handler(None)
        result = execute_tool("read_file", {"path": "/etc/hosts"})
        assert "不支持确认" in result or "拒绝" in result

    @patch("subprocess.run")
    def test_warning_allowed_with_handler(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        set_confirmation_handler(lambda tool, args, reason: True)
        result = execute_tool("execute_command", {"command": "chmod 644 nonexistent_xyz_confirm"})
        assert "已确认执行" in result

    def test_warning_refused_by_handler(self):
        set_confirmation_handler(lambda tool, args, reason: False)
        result = execute_tool("execute_command", {"command": "chmod 644 nonexistent_xyz_confirm"})
        assert "拒绝执行" in result

    def test_dangerous_always_blocked(self):
        set_confirmation_handler(lambda tool, args, reason: True)
        result = execute_tool("execute_command", {"command": "rm -rf /"})
        assert "危险" in result


if __name__ == "__main__":
    test1 = TestToolDefinitions()
    for name in dir(test1):
        if name.startswith("test_"):
            try:
                getattr(test1, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    test2 = TestExecuteTool()
    for name in dir(test2):
        if name.startswith("test_"):
            try:
                getattr(test2, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    test3 = TestExecuteFixedCommand()
    for name in dir(test3):
        if name.startswith("test_"):
            try:
                getattr(test3, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    test4 = TestPathSafety()
    for name in dir(test4):
        if name.startswith("test_"):
            try:
                getattr(test4, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    test5 = TestConfirmationGate()
    for name in dir(test5):
        if name.startswith("test_"):
            try:
                getattr(test5, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    print("\n工具模块测试完成！")
