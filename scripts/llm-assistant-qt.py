#!/usr/bin/env python3
"""
LLM Assistant - AI 助手气泡窗口

从系统托盘启动的浮动聊天气泡，支持:
- 文字输入（流式输出）
- 语音输入（调用 voice-control-run）
- 快捷操作按钮（音量/亮度，直接执行不经过 LLM）
- 系统托盘常驻，应用内快捷键呼出/隐藏
"""

import sys
import os
import json
import subprocess
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QLineEdit, QPushButton, QLabel, QSystemTrayIcon,
    QMenu, QScrollArea, QFrame, QSizePolicy, QMessageBox,
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer, QPoint, QRect
from PyQt6.QtGui import (
    QIcon, QFont, QColor, QPalette, QShortcut, QKeySequence,
    QTextCharFormat, QTextCursor,
)

from llm_core.client import LLMClient, ServerNotRunningError, LLMError
from llm_core.agent import AgentLoop
from llm_core.tools import execute_fixed_command, set_confirmation_handler
from llm_core.config import get_config

# ============================================================
# 配置
# ============================================================
config = get_config()
VOICE_SCRIPT = Path(config.get("paths.voice_script", "~/.local/bin/voice-control-run")).expanduser()
BUBBLE_WIDTH = 400
BUBBLE_HEIGHT = 520

# 样式主题
STYLE = """
QMainWindow {
    background-color: rgba(30, 30, 46, 240);
    border: 1px solid rgba(137, 180, 250, 60);
    border-radius: 16px;
}

QLabel#title {
    color: #cdd6f4;
    font-size: 15px;
    font-weight: bold;
    padding: 8px;
}

QTextEdit#output {
    background-color: rgba(24, 24, 37, 200);
    color: #cdd6f4;
    border: 1px solid rgba(69, 71, 90, 100);
    border-radius: 10px;
    padding: 10px;
    font-size: 13px;
    selection-background-color: #585b70;
}

QLineEdit#input {
    background-color: rgba(49, 50, 68, 200);
    color: #cdd6f4;
    border: 1px solid rgba(137, 180, 250, 80);
    border-radius: 10px;
    padding: 8px 12px;
    font-size: 13px;
}

QLineEdit#input:focus {
    border: 1px solid #89b4fa;
}

QPushButton#send_btn {
    background-color: #89b4fa;
    color: #1e1e2e;
    border: none;
    border-radius: 10px;
    padding: 8px 16px;
    font-weight: bold;
    font-size: 13px;
}

QPushButton#send_btn:hover {
    background-color: #b4d0fb;
}

QPushButton#send_btn:disabled {
    background-color: #585b70;
    color: #9399b2;
}

QPushButton#voice_btn {
    background-color: #a6e3a1;
    color: #1e1e2e;
    border: none;
    border-radius: 10px;
    padding: 8px;
    font-size: 13px;
}

QPushButton#voice_btn:hover {
    background-color: #c6f0c1;
}

QPushButton#voice_btn[listening="true"] {
    background-color: #f38ba8;
    color: #1e1e2e;
}

QPushButton.quick_btn {
    background-color: rgba(69, 71, 90, 160);
    color: #cdd6f4;
    border: 1px solid rgba(137, 180, 250, 40);
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 14px;
    min-width: 36px;
}

QPushButton.quick_btn:hover {
    background-color: rgba(88, 91, 112, 200);
    border: 1px solid rgba(137, 180, 250, 100);
}

QLabel#status {
    color: #89b4fa;
    font-size: 11px;
    padding: 2px 4px;
}
"""


# ============================================================
# LLM 工作线程（支持流式输出）
# ============================================================
class LLMWorker(QThread):
    """后台线程处理 LLM 请求，支持流式输出"""
    chunk_ready = pyqtSignal(str)           # 增量文本
    thinking = pyqtSignal(str)              # 推理内容
    tool_call = pyqtSignal(str, str)        # 工具调用 (name, preview)
    tool_result = pyqtSignal(str, str)      # 工具结果 (name, preview)
    error = pyqtSignal(str)                 # 错误
    finished = pyqtSignal()                 # 完成

    def __init__(self):
        super().__init__()
        self._query = ""
        self._stopped = False

    def set_query(self, query: str):
        self._query = query
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        if not self._query:
            self.finished.emit()
            return

        try:
            client = LLMClient()
            if not client.check_health():
                self.error.emit("❌ llama-server 未运行\n请先执行: llama-serve qwen36 &")
                self.finished.emit()
                return

            agent = AgentLoop(client)
            content_started = False

            for event in agent.run_stream(self._query):
                if self._stopped:
                    break

                t = event.get("type", "")

                if t == "thinking":
                    self.thinking.emit(event["content"])

                elif t == "text":
                    if not content_started:
                        content_started = True
                    self.chunk_ready.emit(event["content"])

                elif t == "tool_call":
                    name = event["name"]
                    args = event.get("args", {})
                    if name == "execute_command":
                        preview = args.get("command", "")[:80]
                    elif name in ("read_file", "write_file"):
                        preview = args.get("path", "")[:60]
                    elif name == "list_directory":
                        preview = args.get("path", ".")[:60]
                    else:
                        preview = ""
                    self.tool_call.emit(name, preview)

                elif t == "tool_result":
                    name = event.get("name", "")
                    result = event.get("result", "")[:100]
                    self.tool_result.emit(name, result)

                elif t == "error":
                    self.error.emit(event["content"])

                elif t == "done":
                    break

        except ServerNotRunningError as e:
            self.error.emit(f"❌ {e}")
        except LLMError as e:
            self.error.emit(f"❌ {e}")
        except Exception as e:
            self.error.emit(f"❌ 错误: {e}")

        self.finished.emit()


# ============================================================
# 语音识别线程
# ============================================================
class VoiceWorker(QThread):
    """后台线程调用语音识别脚本"""
    result_ready = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._proc: subprocess.Popen | None = None
        self._stop_requested = False

    def stop(self):
        """Request a cooperative stop and terminate only the owned child process."""
        self._stop_requested = True
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()

    def run(self):
        if not VOICE_SCRIPT.exists():
            self.error.emit(f"语音识别脚本不存在: {VOICE_SCRIPT}")
            return

        try:
            proc = subprocess.Popen(
                [str(VOICE_SCRIPT), "--mode", "llm", "--once"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self._proc = proc
            if self._stop_requested:
                proc.terminate()
            stdout, stderr = proc.communicate(timeout=30)
            if self._stop_requested:
                return
            text = stdout.strip() or stderr.strip()
            if text:
                self.result_ready.emit(text)
            else:
                self.error.emit("未识别到语音")
        except subprocess.TimeoutExpired:
            if self._proc is not None:
                self._proc.kill()
                self._proc.communicate()
            if not self._stop_requested:
                self.error.emit("语音识别超时")
        except FileNotFoundError:
            self.error.emit(f"脚本未找到: {VOICE_SCRIPT}")
        except Exception as e:
            if not self._stop_requested:
                self.error.emit(f"语音识别错误: {e}")
        finally:
            self._proc = None


# ============================================================
# 主窗口 - 气泡式聊天界面
# ============================================================
class LLMAssistantWindow(QMainWindow):
    """AI 助手气泡窗口"""

    # 命令确认请求信号（跨线程: LLMWorker → GUI 主线程）
    _confirm_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("AI 助手")
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(BUBBLE_WIDTH, BUBBLE_HEIGHT)

        # 状态
        self._is_processing = False
        self._response_started = False
        self._voice_worker: VoiceWorker | None = None

        # LLM 工作线程
        self._worker = LLMWorker()
        self._worker.chunk_ready.connect(self._on_chunk)
        self._worker.thinking.connect(self._on_thinking)
        self._worker.tool_call.connect(self._on_tool_call)
        self._worker.tool_result.connect(self._on_tool_result)
        self._worker.error.connect(self._on_error)
        self._worker.finished.connect(self._on_finished)

        # 工具确认: shell、写文件和受限目录读取均在执行前弹窗
        self._confirm_signal.connect(self._show_confirm_dialog)
        self._confirm_pending = None
        set_confirmation_handler(self._confirm_command)

        self._init_ui()
        self._init_tray()
        self._init_shortcuts()

        # 定位到右下角
        self._position_bottom_right()

    def _init_ui(self):
        """初始化界面"""
        central = QWidget()
        central.setObjectName("central")
        self.setCentralWidget(central)

        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        # 标题栏（可拖拽移动）
        title_bar = QHBoxLayout()
        self._title_label = QLabel("🤖 AI 助手")
        self._title_label.setObjectName("title")
        title_bar.addWidget(self._title_label)
        title_bar.addStretch()

        close_btn = QPushButton("✕")
        close_btn.setFixedSize(28, 28)
        close_btn.setStyleSheet(
            "QPushButton { background: transparent; color: #cdd6f4; border: none; font-size: 16px; }"
            "QPushButton:hover { color: #f38ba8; }"
        )
        close_btn.clicked.connect(self.hide)
        title_bar.addWidget(close_btn)
        layout.addLayout(title_bar)

        # 输出区域
        self._output = QTextEdit()
        self._output.setObjectName("output")
        self._output.setReadOnly(True)
        self._output.setPlaceholderText("有什么可以帮你的？")
        layout.addWidget(self._output, stretch=1)

        # 状态标签
        self._status_label = QLabel("🟢 就绪")
        self._status_label.setObjectName("status")
        layout.addWidget(self._status_label)

        # 输入区域
        input_layout = QHBoxLayout()
        input_layout.setSpacing(6)

        self._input = QLineEdit()
        self._input.setObjectName("input")
        self._input.setPlaceholderText("输入问题或命令...")
        self._input.returnPressed.connect(self._send_query)
        input_layout.addWidget(self._input, stretch=1)

        self._send_btn = QPushButton("发送")
        self._send_btn.setObjectName("send_btn")
        self._send_btn.clicked.connect(self._send_query)
        input_layout.addWidget(self._send_btn)

        self._voice_btn = QPushButton("🎤")
        self._voice_btn.setObjectName("voice_btn")
        self._voice_btn.setFixedSize(40, 36)
        self._voice_btn.setToolTip("语音输入（点击开始/停止）")
        self._voice_btn.clicked.connect(self._toggle_voice)
        input_layout.addWidget(self._voice_btn)

        layout.addLayout(input_layout)

        # 快捷按钮行
        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(4)

        quick_actions = [
            ("🔊", "volume_up", "音量+"),
            ("🔉", "volume_down", "音量-"),
            ("☀️", "brightness_up", "亮度+"),
            ("🌙", "brightness_down", "亮度-"),
            ("🔇", "mute", "静音切换"),
        ]

        for icon, cmd, tooltip in quick_actions:
            btn = QPushButton(icon)
            btn.setProperty("class", "quick_btn")
            btn.setStyleSheet(STYLE.split("QPushButton.quick_btn")[1].split("}")[0] + "}")
            btn.setToolTip(tooltip)
            btn.clicked.connect(lambda checked, c=cmd: self._execute_quick(c))
            quick_layout.addWidget(btn)

        layout.addLayout(quick_layout)

        # 应用样式
        self.setStyleSheet(STYLE)

    def _init_tray(self):
        """初始化系统托盘"""
        self._tray = QSystemTrayIcon(self)
        self._tray.setIcon(QIcon.fromTheme("user-available"))

        menu = QMenu()
        menu.addAction("显示/隐藏", self._toggle_visible)
        menu.addSeparator()
        menu.addAction("退出", self._quit)
        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    def _init_shortcuts(self):
        """初始化应用内快捷键；系统级呼出由 KDE 全局快捷键配置负责。"""
        self._toggle_shortcut = QShortcut(QKeySequence("Ctrl+Shift+Space"), self)
        self._toggle_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        self._toggle_shortcut.activated.connect(self._toggle_visible)

    def _position_bottom_right(self):
        """将窗口定位到屏幕右下角"""
        screen = QApplication.primaryScreen()
        if screen:
            geom = screen.availableGeometry()
            x = geom.right() - BUBBLE_WIDTH - 20
            y = geom.bottom() - BUBBLE_HEIGHT - 20
            self.move(x, y)

    # ============================================================
    # 事件处理
    # ============================================================
    def _append_output_line(self, text: str, color: str) -> None:
        """Append literal text with formatting; never interpret model text as HTML."""
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not self._output.document().isEmpty():
            cursor.insertBlock()
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.insertText(text, fmt)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._toggle_visible()
        elif reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._toggle_visible()

    def _toggle_visible(self):
        if self.isVisible():
            self.hide()
        else:
            self._position_bottom_right()
            self.show()
            self.raise_()
            self.activateWindow()
            self._input.setFocus()

    def _send_query(self):
        """发送查询"""
        if self._is_processing:
            return

        text = self._input.text().strip()
        if not text:
            return

        self._append_output_line(f"👤 {text}", "#89b4fa")
        self._input.clear()
        self._response_started = False

        self._set_processing(True)
        self._worker.set_query(text)
        self._worker.start()

    def _toggle_voice(self):
        """切换语音识别"""
        if self._voice_worker and self._voice_worker.isRunning():
            self._voice_worker.stop()
            self._voice_worker.wait(2000)
            self._voice_btn.setProperty("listening", "false")
            self._voice_btn.setStyleSheet("")
            self._voice_btn.setStyleSheet(
                STYLE.split("QPushButton#voice_btn")[1].split("}")[0] + "}"
            )
            self._status_label.setText("🟢 就绪")
            return

        self._voice_worker = VoiceWorker()
        self._voice_worker.result_ready.connect(self._on_voice_result)
        self._voice_worker.error.connect(self._on_voice_error)
        self._voice_worker.finished.connect(self._on_voice_finished)
        self._voice_worker.start()

        self._voice_btn.setProperty("listening", "true")
        self._voice_btn.setStyleSheet("")
        self._voice_btn.setStyleSheet(
            "QPushButton#voice_btn { background-color: #f38ba8; color: #1e1e2e; border: none; border-radius: 10px; padding: 8px; font-size: 13px; }"
        )
        self._status_label.setText("🎤 正在听...")

    def _on_voice_result(self, text: str):
        """语音识别结果"""
        self._input.setText(text)
        self._status_label.setText("🟢 就绪")
        self._send_query()

    def _on_voice_error(self, error: str):
        self._status_label.setText(f"⚠️ {error}")
        self._append_output_line(f"⚠️ {error}", "#f38ba8")

    def _on_voice_finished(self):
        self._voice_btn.setProperty("listening", "false")
        self._voice_btn.setStyleSheet("")
        self._voice_btn.setStyleSheet(
            STYLE.split("QPushButton#voice_btn")[1].split("}")[0] + "}"
        )
        if self._status_label.text().startswith("🎤"):
            self._status_label.setText("🟢 就绪")
        self._voice_worker = None

    def _execute_quick(self, cmd: str):
        """执行快捷命令（不经过 LLM）"""
        result = execute_fixed_command(cmd)
        self._append_output_line(result, "#a6e3a1")

    # ============================================================
    # 命令确认（警告级命令执行前询问用户）
    # ============================================================
    def _confirm_command(self, tool_name: str, args: dict, reason: str) -> bool:
        """确认回调，在 LLMWorker 线程中被调用。"""
        # 若本身就在主线程（理论上不会），直接弹窗
        if QThread.currentThread() is self.thread():
            return self._ask_confirmation(tool_name, args, reason)
        event = threading.Event()
        self._confirm_pending = (tool_name, args, reason, event, [False])
        self._confirm_signal.emit()  # 队列到 GUI 主线程
        event.wait(timeout=60)
        return self._confirm_pending[4][0]

    def _show_confirm_dialog(self):
        """在 GUI 主线程弹出确认对话框。"""
        if not self._confirm_pending:
            return
        tool_name, args, reason, event, result = self._confirm_pending
        result[0] = self._ask_confirmation(tool_name, args, reason)
        event.set()

    def _ask_confirmation(self, tool_name: str, args: dict, reason: str) -> bool:
        """同步弹出确认框并返回用户选择。"""
        preview = ""
        if tool_name == "execute_command":
            preview = args.get("command", "")
        elif tool_name in ("read_file", "write_file", "list_directory"):
            preview = f"目标路径: {args.get('path', '')}"
        preview = (preview or reason or "")[:200]
        btn = QMessageBox.warning(
            self,
            "⚠️ 确认执行",
            f"{reason}\n\n{preview}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return btn == QMessageBox.StandardButton.Yes

    # ============================================================
    # LLM 响应回调
    # ============================================================
    def _on_chunk(self, text: str):
        cursor = self._output.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor("#cdd6f4"))
        if not self._response_started:
            if not self._output.document().isEmpty():
                cursor.insertBlock()
            cursor.insertText("🤖 ", fmt)
            self._response_started = True
        cursor.insertText(text, fmt)
        self._output.setTextCursor(cursor)
        self._output.ensureCursorVisible()

    def _on_thinking(self, text: str):
        self._status_label.setText(f"💭 思考中...")

    def _on_tool_call(self, name: str, preview: str):
        self._response_started = False
        msg = f"🔧 {name}"
        if preview:
            msg += f": {preview}"
        self._append_output_line(msg, "#f9e2af")
        self._status_label.setText(f"🔧 执行: {name}")

    def _on_tool_result(self, name: str, preview: str):
        self._status_label.setText("🤖 思考中...")

    def _on_error(self, error: str):
        self._response_started = False
        self._append_output_line(error, "#f38ba8")

    def _on_finished(self):
        self._set_processing(False)
        self._response_started = False
        if self._status_label.text() not in ("🟢 就绪",):
            self._status_label.setText("🟢 就绪")

    def _set_processing(self, processing: bool):
        self._is_processing = processing
        self._send_btn.setEnabled(not processing)
        self._input.setEnabled(not processing)
        if processing:
            self._status_label.setText("⏳ 处理中...")
            self._send_btn.setText("...")
        else:
            self._send_btn.setText("发送")

    # ============================================================
    # 窗口行为
    # ============================================================
    def mousePressEvent(self, event):
        """拖拽窗口"""
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if hasattr(self, "_drag_pos") and event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def changeEvent(self, event):
        """窗口失焦时隐藏"""
        # 不自动隐藏，让用户手动关闭或使用快捷键
        super().changeEvent(event)

    def closeEvent(self, event):
        event.ignore()
        self.hide()

    def _quit(self):
        if self._voice_worker and self._voice_worker.isRunning():
            self._voice_worker.stop()
            self._voice_worker.wait(2000)
        if self._worker.isRunning():
            self._worker.stop()
            self._worker.wait(3000)
            if self._worker.isRunning():
                # 请求阻塞在 urllib 上无法靠标志位中断时，强制终止线程以正常退出
                self._worker.terminate()
                self._worker.wait(1000)
        self._tray.hide()
        QApplication.quit()


# ============================================================
# 主入口
# ============================================================
def main():
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("LLM Assistant")

    window = LLMAssistantWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
