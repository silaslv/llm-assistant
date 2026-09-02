#!/usr/bin/env python3
"""
LLM Assistant Backend - 为 KDE Plasma Widget 提供后端服务

支持两种通信方式:
1. Unix domain socket (CLI 工具使用) - /tmp/llm-assistant.sock
2. DBus session bus (Plasma QML Widget 使用) - org.kde.plasma.llm-assistant

两种方式共享同一个 LLMAssistant 实例，互不影响。
"""

import sys
import os
import json
import socket
import subprocess
import threading
import select
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from llm_core.client import LLMClient, ServerNotRunningError
from llm_core.agent import AgentLoop
from llm_core.tools import execute_fixed_command
from llm_core.config import get_config

import dbus
import dbus.service
import dbus.mainloop.glib
from gi.repository import GLib

# ============================================================
# 配置
# ============================================================
SOCKET_PATH = "/tmp/llm-assistant.sock"
VOICE_SCRIPT = Path.home() / ".local/bin/voice-control-run"
DBUS_BUS_NAME = "org.kde.plasma.llm-assistant"
DBUS_OBJECT_PATH = "/llm_assistant"
DBUS_INTERFACE = "org.kde.plasma.llm_assistant"


# ============================================================
# LLM Assistant 后端
# ============================================================
class LLMAssistant:
    """LLM 助手后端"""

    def __init__(self):
        self._client = LLMClient()
        self._agent = AgentLoop(self._client)
        self._is_listening = False
        self._voice_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def is_listening(self) -> bool:
        with self._lock:
            return self._is_listening

    def process_query(self, query: str, is_command_mode: bool = False) -> str:
        """处理用户查询"""
        if not self._client.check_health():
            return "❌ llama-server 未运行\n请先执行: llama-serve qwen36 &"

        try:
            return self._agent.run(query)
        except ServerNotRunningError:
            return "❌ llama-server 未运行\n请先执行: llama-serve qwen36 &"
        except Exception as e:
            return f"❌ 错误: {e}"

    def execute_command(self, name: str) -> str:
        """执行固定命令"""
        return execute_fixed_command(name)

    def start_listening(self) -> str:
        """开始语音识别"""
        if not VOICE_SCRIPT.exists():
            return f"❌ 语音识别脚本不存在: {VOICE_SCRIPT}"

        with self._lock:
            if self._is_listening:
                return "⚠️ 语音识别已在运行"
            self._is_listening = True

        def _listen():
            try:
                subprocess.run(
                    [str(VOICE_SCRIPT), "--mode", "llm", "--once"],
                    timeout=30,
                )
            except subprocess.TimeoutExpired:
                pass
            except Exception:
                pass
            finally:
                with self._lock:
                    self._is_listening = False

        self._voice_thread = threading.Thread(target=_listen, daemon=True)
        self._voice_thread.start()
        return "🎤 语音识别已启动"

    def stop_listening(self) -> str:
        """停止语音识别"""
        with self._lock:
            self._is_listening = False
        try:
            subprocess.run(["pkill", "-f", "voice-control-run"], capture_output=True, timeout=5)
        except Exception:
            pass
        return "语音识别已停止"


# ============================================================
# DBus 服务 - 供 Plasma QML Widget 调用
# ============================================================
class LLMAssistantDBusService(dbus.service.Object):
    """DBus 服务对象，注册在 org.kde.plasma.llm-assistant"""

    def __init__(self, bus_name, assistant: LLMAssistant):
        super().__init__(bus_name, DBUS_OBJECT_PATH)
        self._assistant = assistant

    @dbus.service.method(DBUS_INTERFACE, in_signature="sb", out_signature="s",
                         async_callbacks=("reply_handler", "error_handler"))
    def processQuery(self, query: str, is_command: bool, reply_handler, error_handler):
        """异步处理查询，避免长时间生成阻塞 DBus 主循环。"""
        threading.Thread(
            target=self._run_process_query,
            args=(query, is_command, reply_handler, error_handler),
            daemon=True,
        ).start()

    def _run_process_query(self, query, is_command, reply_handler, error_handler):
        try:
            result = self._assistant.process_query(query, is_command)
            GLib.idle_add(self._reply_from_main, reply_handler, result)
        except Exception as e:
            GLib.idle_add(self._reply_from_main, error_handler, str(e))

    @staticmethod
    def _reply_from_main(handler, value):
        """在 GLib 主循环线程中发送 DBus 回复，确保线程安全。"""
        try:
            handler(value)
        except Exception:
            pass
        return False  # 只运行一次，移除 idle source

    @dbus.service.method(DBUS_INTERFACE, in_signature="s", out_signature="s")
    def executeCommand(self, name: str) -> str:
        return self._assistant.execute_command(name)

    @dbus.service.method(DBUS_INTERFACE, in_signature="", out_signature="s")
    def startListening(self) -> str:
        return self._assistant.start_listening()

    @dbus.service.method(DBUS_INTERFACE, in_signature="", out_signature="s")
    def stopListening(self) -> str:
        return self._assistant.stop_listening()

    @dbus.service.method(DBUS_INTERFACE, in_signature="", out_signature="s")
    def getState(self) -> str:
        return "listening" if self._assistant.is_listening else "idle"

    @dbus.service.method(DBUS_INTERFACE, in_signature="", out_signature="s")
    def health(self) -> str:
        client = LLMClient()
        return json.dumps({
            "server_running": client.check_health(),
            "backend": "ok"
        })


# ============================================================
# Unix Socket IPC 服务器 - 供 CLI 工具使用
# ============================================================
class UnixSocketServer:
    """Unix domain socket 服务器，JSON-line 协议"""

    def __init__(self, socket_path: str, assistant: LLMAssistant):
        self._path = socket_path
        self._assistant = assistant
        self._running = False
        self._server: socket.socket | None = None

    def start(self):
        """启动服务器"""
        if os.path.exists(self._path):
            try:
                os.unlink(self._path)
            except OSError:
                pass

        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server.bind(self._path)
        self._server.listen(5)
        self._server.setblocking(False)
        self._running = True

        print(f"🔌 Unix Socket 已启动: {self._path}")

        try:
            self._run_loop()
        except Exception:
            pass
        finally:
            self.stop()

    def stop(self):
        """停止服务器"""
        self._running = False
        if self._server:
            self._server.close()
        if os.path.exists(self._path):
            try:
                os.unlink(self._path)
            except OSError:
                pass

    def _run_loop(self):
        """主事件循环"""
        inputs = [self._server]
        client_buffers: dict[socket.socket, bytearray] = {}

        while self._running:
            readable, _, _ = select.select(inputs, [], [], 0.5)

            for sock in readable:
                if sock is self._server:
                    conn, addr = self._server.accept()
                    conn.setblocking(False)
                    inputs.append(conn)
                    client_buffers[conn] = bytearray()
                else:
                    try:
                        data = sock.recv(4096)
                        if data:
                            client_buffers[sock].extend(data)
                            self._process_buffer(sock, client_buffers[sock])
                        else:
                            self._cleanup_client(sock, inputs, client_buffers)
                    except (ConnectionResetError, BrokenPipeError):
                        self._cleanup_client(sock, inputs, client_buffers)
                    except BlockingIOError:
                        pass

    def _process_buffer(self, sock: socket.socket, buffer: bytearray):
        """处理客户端缓冲区中的 JSON 行"""
        while b"\n" in buffer:
            line_bytes, buffer[:] = buffer.split(b"\n", 1)
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = {"error": "无效的 JSON 请求"}
                self._send_response(sock, response)
                continue

            response = self._handle_request(request)
            self._send_response(sock, response)

    def _handle_request(self, request: dict) -> dict:
        """处理单个请求"""
        method = request.get("method", "")
        params = request.get("params", [])
        req_id = request.get("id", "")

        response = {"id": req_id}

        try:
            if method == "processQuery":
                query = params[0] if len(params) > 0 else ""
                is_command = params[1] if len(params) > 1 else False
                response["result"] = self._assistant.process_query(query, is_command)
            elif method == "executeCommand":
                name = params[0] if len(params) > 0 else ""
                response["result"] = self._assistant.execute_command(name)
            elif method == "startListening":
                response["result"] = self._assistant.start_listening()
            elif method == "stopListening":
                response["result"] = self._assistant.stop_listening()
            elif method == "health":
                client = LLMClient()
                response["result"] = {
                    "server_running": client.check_health(),
                    "backend": "ok",
                }
            else:
                response["error"] = f"未知方法: {method}"
        except Exception as e:
            response["error"] = str(e)

        return response

    def _send_response(self, sock: socket.socket, response: dict):
        """发送 JSON 响应"""
        try:
            data = json.dumps(response, ensure_ascii=False) + "\n"
            sock.sendall(data.encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _cleanup_client(self, sock: socket.socket, inputs: list, buffers: dict):
        """清理断开的客户端"""
        if sock in inputs:
            inputs.remove(sock)
        buffers.pop(sock, None)
        try:
            sock.close()
        except OSError:
            pass


# ============================================================
# 主入口
# ============================================================
def _backend_alive(socket_path: str) -> bool:
    """检测 socket 路径上是否已有 backend 在监听。"""
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(socket_path)
        s.close()
        return True
    except OSError:
        return False


def main():
    # 单实例保护: 已有 backend 在运行时直接退出，避免抢占 socket 路径
    if os.path.exists(SOCKET_PATH):
        if _backend_alive(SOCKET_PATH):
            print(f"❌ 已有 LLM Assistant Backend 在运行（{SOCKET_PATH}），退出。", file=sys.stderr)
            sys.exit(1)
        # socket 文件存在但没有进程监听 → 清理陈旧文件
        try:
            os.unlink(SOCKET_PATH)
        except OSError:
            pass

    assistant = LLMAssistant()

    # 1. Unix Socket 在后台线程运行 (CLI 工具使用)
    socket_server = UnixSocketServer(SOCKET_PATH, assistant)
    socket_thread = threading.Thread(target=socket_server.start, daemon=True)
    socket_thread.start()

    # 2. DBus 服务在主线程运行 (Plasma QML Widget 使用)
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    bus = dbus.SessionBus()
    bus_name = dbus.service.BusName(DBUS_BUS_NAME, bus)
    dbus_service = LLMAssistantDBusService(bus_name, assistant)

    print("🤖 LLM Assistant Backend 已启动")
    print(f"   Socket: {SOCKET_PATH}")
    print(f"   DBus:   {DBUS_BUS_NAME}")
    print(f"   等待连接...")

    loop = GLib.MainLoop()
    try:
        loop.run()
    except KeyboardInterrupt:
        print("\n停止服务")
    finally:
        socket_server.stop()
        loop.quit()


if __name__ == "__main__":
    main()
