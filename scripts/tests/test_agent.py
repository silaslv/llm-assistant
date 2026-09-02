"""
测试: Agent 模块 + Client 模块
"""

import sys
import os
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm_core.client import LLMClient, ServerNotRunningError, LLMError
from llm_core.agent import AgentLoop, DEFAULT_SYSTEM_PROMPT


class TestLLMClient:
    """LLM 客户端测试"""

    @patch.object(LLMClient, "_open")
    def test_check_health_ok(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        client = LLMClient()
        assert client.check_health() is True

    @patch.object(LLMClient, "_open")
    def test_check_health_fail(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = LLMClient()
        assert client.check_health() is False

    @patch.object(LLMClient, "_open")
    def test_chat_returns_response(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{
                "message": {
                    "content": "你好！有什么可以帮你的？",
                    "role": "assistant",
                }
            }]
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        client = LLMClient()
        response = client.chat([{"role": "user", "content": "你好"}])

        assert response["choices"][0]["message"]["content"] == "你好！有什么可以帮你的？"

    @patch.object(LLMClient, "_open")
    def test_chat_with_tool_calls(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "execute_command",
                            "arguments": '{"command": "ls"}',
                        }
                    }]
                }
            }]
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        client = LLMClient()
        response = client.chat(
            [{"role": "user", "content": "列出文件"}],
            tools=[{"type": "function", "function": {"name": "execute_command"}}],
        )

        assert "tool_calls" in response["choices"][0]["message"]

    @patch.object(LLMClient, "_open")
    def test_server_not_running(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        client = LLMClient()
        try:
            client.chat([{"role": "user", "content": "hi"}])
            assert False, "应该抛出 ServerNotRunningError"
        except ServerNotRunningError:
            pass

    def test_loopback_bypasses_environment_proxy(self):
        assert LLMClient("http://127.0.0.1:18080")._opener is not None
        assert LLMClient("http://localhost:18080")._opener is not None
        assert LLMClient("https://example.com")._opener is None


class TestAgentLoop:
    """Agent 循环测试"""

    @patch.object(LLMClient, "chat")
    @patch.object(LLMClient, "check_health")
    def test_simple_question_no_tools(self, mock_health, mock_chat):
        mock_health.return_value = True
        mock_chat.return_value = {
            "choices": [{
                "message": {
                    "content": "Python 是一种编程语言。",
                    "role": "assistant",
                }
            }]
        }

        client = LLMClient()
        agent = AgentLoop(client)
        result = agent.run("什么是 Python？")

        assert "Python" in result

    @patch.object(LLMClient, "chat")
    @patch.object(LLMClient, "check_health")
    def test_with_one_tool_call(self, mock_health, mock_chat):
        mock_health.return_value = True

        # 第一次调用返回 tool_call
        # 第二次调用返回最终回答
        mock_chat.side_effect = [
            {
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "tool_calls": [{
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "list_directory",
                                "arguments": '{"path": "/tmp"}',
                            }
                        }]
                    }
                }]
            },
            {
                "choices": [{
                    "message": {
                        "content": "目录 /tmp 包含以下文件...",
                        "role": "assistant",
                    }
                }]
            },
        ]

        client = LLMClient()
        agent = AgentLoop(client)
        result = agent.run("列出 /tmp 目录")

        assert "目录" in result or "tmp" in result

    @patch.object(LLMClient, "chat")
    @patch.object(LLMClient, "check_health")
    def test_final_summary_after_max_rounds(self, mock_health, mock_chat):
        """达到最大轮数后应再做一次不带工具的收尾调用，给出最终回答"""
        mock_health.return_value = True
        tool_call_msg = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "list_directory", "arguments": '{"path": "/tmp"}'},
                    }]
                }
            }]
        }
        final_msg = {"choices": [{"message": {"content": "最终回答", "role": "assistant"}}]}

        def side_effect(messages, tools=None):
            if tools:
                return tool_call_msg
            return final_msg

        mock_chat.side_effect = side_effect
        client = LLMClient()
        agent = AgentLoop(client)
        result = agent.run("列目录")
        assert result == "最终回答"

    @patch.object(LLMClient, "chat")
    @patch.object(LLMClient, "check_health")
    def test_malformed_tool_args_does_not_crash(self, mock_health, mock_chat):
        """模型返回畸形 tool arguments 时不应崩溃"""
        mock_health.return_value = True
        mock_chat.side_effect = [
            {"choices": [{"message": {"role": "assistant", "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_directory", "arguments": "{bad json"},
            }]}}]},
            {"choices": [{"message": {"content": "目录内容如上", "role": "assistant"}}]},
        ]
        client = LLMClient()
        agent = AgentLoop(client)
        result = agent.run("列目录")
        assert "目录" in result

    def test_default_system_prompt(self):
        agent = AgentLoop()
        assert "assistant" in agent._system_prompt.lower()
        assert "execute_command" in agent._system_prompt


class TestFallbackCommand:
    """文本 fallback 提取测试（只应提取"整体就是一条命令"的正文）"""

    def test_fenced_block_only(self):
        from llm_core.agent import _extract_fallback_command
        assert _extract_fallback_command("```bash\ndate\n```") == "date"

    def test_execute_command_wrapper_only(self):
        from llm_core.agent import _extract_fallback_command
        assert _extract_fallback_command('execute_command("ls -la")') == "ls -la"
        assert _extract_fallback_command("execute_command('date')") == "date"

    def test_narrative_should_not_extract(self):
        from llm_core.agent import _extract_fallback_command
        content = "你可以用 execute_command('ls') 来查看文件列表。"
        assert _extract_fallback_command(content) is None

    def test_prose_with_code_block_not_extracted(self):
        from llm_core.agent import _extract_fallback_command
        content = "先看下当前目录：\n```bash\nls\n```\n以上就是结果。"
        assert _extract_fallback_command(content) is None

    def test_empty_or_whitespace(self):
        from llm_core.agent import _extract_fallback_command
        assert _extract_fallback_command("") is None
        assert _extract_fallback_command("   ") is None


if __name__ == "__main__":
    test1 = TestLLMClient()
    for name in dir(test1):
        if name.startswith("test_"):
            try:
                getattr(test1, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    test2 = TestAgentLoop()
    for name in dir(test2):
        if name.startswith("test_"):
            try:
                getattr(test2, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    test3 = TestFallbackCommand()
    for name in dir(test3):
        if name.startswith("test_"):
            try:
                getattr(test3, name)()
                print(f"  ✅ {name}")
            except Exception as e:
                print(f"  ❌ {name}: {e}")

    print("\nAgent + Client 模块测试完成！")
