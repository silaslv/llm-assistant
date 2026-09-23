#!/usr/bin/env python3
"""Local desktop assistant: explicit voice activation, shared session, compact UI."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import subprocess
import time
from urllib.parse import urlparse
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent))
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QProcess, QTimer, QSize, QObject, QSettings, QEvent
from PyQt6.QtNetwork import QLocalServer, QLocalSocket, QAbstractSocket
from PyQt6.QtGui import QIcon, QPixmap, QPainter, QPen, QColor, QShortcut, QKeySequence
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QLineEdit, QFrame, QScrollArea, QSystemTrayIcon, QMenu,
    QMessageBox, QSizePolicy)
from llm_core.session import AssistantSession
from llm_core.tools import set_confirmation_handler
from llm_core.config import get_config
from llm_core.client import LLMClient

STYLE='''
QWidget { font-family: "Noto Sans CJK SC", "sans-serif"; font-size: 13px; color: #243b3b; }
QFrame#surface { background: #f7faf8; border: 1px solid #d8e5df; border-radius: 20px; }
QLabel#brand { font-size: 17px; font-weight: 700; color: #183e36; }
QLabel#eyebrow { color: #738780; font-size: 10px; }
QLabel#title { font-size: 22px; font-weight: 600; color: #183e36; }
QLabel#subtitle { color: #738780; font-size: 12px; }
QLabel#status { background: #e7efe9; color: #4e7465; border-radius: 9px; padding: 4px 9px; font-size: 11px; }
QLabel#status[state="listening"] { background: #d9eee4; color: #17664c; }
QLabel#status[state="error"] { background: #f8e9e4; color: #a45643; }
QPushButton { background: transparent; border: 0; border-radius: 9px; padding: 7px 10px; }
QPushButton:hover { background: #e6eee9; }
QPushButton:disabled { color: #a7b5af; }
QPushButton#mic { background: #226b55; border-radius: 32px; }
QPushButton#mic:hover { background: #18573f; }
QPushButton#mic[active="true"] { background: #bd654d; }
QPushButton#primary { background: #226b55; color: white; padding: 10px 14px; }
QPushButton#primary:hover { background: #18573f; }
QPushButton#primary:disabled { background: #afc3b8; }
QPushButton#chip { background: #edf3ef; border: 1px solid #e0e9e3; color: #547266; font-size: 11px; }
QPushButton#quiet { color: #71867b; font-size: 11px; }
QFrame#composer { background: white; border: 1px solid #d9e5dd; border-radius: 12px; }
QLineEdit { background: transparent; border: 0; padding: 8px; selection-background-color: #c9e5d7; }
QScrollArea { background: transparent; border: 0; }
QWidget#feed { background: transparent; }
QFrame[role="assistant"] { background: white; border: 1px solid #e2eae5; border-radius: 12px; }
QFrame[role="user"] { background: #e5efe9; border: 0; border-radius: 12px; }
QLabel#speaker { color: #7e9387; font-size: 10px; font-weight: 600; }
QLabel#message { font-size: 13px; line-height: 1.5; }
QLabel#detail { color: #7b8e83; font-size: 11px; }
QScrollBar:vertical { background: transparent; width: 5px; }
QScrollBar::handle:vertical { background: #c9d8cd; border-radius: 2px; min-height: 24px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QMenu { background: #f7faf8; border: 1px solid #d9e5dd; padding: 5px; }
QMenu::item:selected { background: #e5efe9; }
'''

def icon(kind,color='#557568',size=24):
    pix=QPixmap(size,size); pix.fill(Qt.GlobalColor.transparent)
    p=QPainter(pix); p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size/24,size/24); p.setPen(QPen(QColor(color),1.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
    if kind=='mic':
        p.drawRoundedRect(9,3,6,12,3,3); p.drawArc(6,8,12,11,180*16,180*16); p.drawLine(12,19,12,22); p.drawLine(8,22,16,22)
    elif kind=='close': p.drawLine(7,7,17,17); p.drawLine(7,17,17,7)
    elif kind=='expand': p.drawLine(7,10,12,15); p.drawLine(12,15,17,10)
    elif kind=='collapse': p.drawLine(7,15,12,10); p.drawLine(12,10,17,15)
    elif kind=='layout':
        p.drawRoundedRect(4,4,16,16,3,3); p.drawLine(13,4,13,20)
    elif kind=='stop': p.drawRoundedRect(7,7,10,10,2,2)
    elif kind=='spark':
        p.drawLine(12,3,12,21); p.drawLine(3,12,21,12); p.drawLine(6,6,18,18); p.drawLine(6,18,18,6)
    p.end(); return QIcon(pix)

def app_icon():
    result=QIcon(str(Path(__file__).resolve().parent/'assets'/'assistant.svg'))
    return result if not result.isNull() else icon('spark','#226b55',64)

class FeedbackLabel(QLabel):
    def __init__(self,text):
        super().__init__(text)
        self.full_text=text; self.compact=False
        self.setToolTip(text)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
    def setText(self,text):
        self.full_text=str(text); self.setToolTip(self.full_text); self._refresh()
    def setCompact(self,compact):
        self.compact=compact; self.setWordWrap(not compact); self._refresh()
    def _refresh(self):
        text=self.fontMetrics().elidedText(self.full_text,Qt.TextElideMode.ElideRight,max(1,self.width())) if self.compact else self.full_text
        super().setText(text)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'full_text'): self._refresh()

class DockButton(QPushButton):
    def mousePressEvent(self,event):
        self.dragging=False; self.native_drag=False
        self.press_global=event.globalPosition().toPoint()
        self.drag_offset=self.press_global-self.window().frameGeometry().topLeft()
        super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if event.buttons() & Qt.MouseButton.LeftButton:
            if not self.dragging and (event.globalPosition().toPoint()-self.press_global).manhattanLength()>=QApplication.startDragDistance():
                self.dragging=True; self.setDown(False)
                handle=self.window().windowHandle()
                self.native_drag=bool(handle and handle.startSystemMove())
            if self.dragging:
                if not self.native_drag: self.window().move(event.globalPosition().toPoint()-self.drag_offset)
                event.accept(); return
        super().mouseMoveEvent(event)
    def mouseReleaseEvent(self,event):
        if getattr(self,'dragging',False):
            self.dragging=False; self.setDown(False); self.window()._snap_fallback(); event.accept()
        else: super().mouseReleaseEvent(event)

class QueryWorker(QThread):
    received=pyqtSignal(dict)
    def __init__(self,session,query):
        super().__init__(); self.session=session; self.query=query; self.cancelled=False
    def run(self):
        try:
            for event in self.session.stream(self.query):
                if self.cancelled: break
                self.received.emit(event)
        except Exception as e: self.received.emit({'type':'error','content':str(e)})

class ModelWorker(QThread):
    status=pyqtSignal(str)
    def run(self):
        client=LLMClient()
        if client.check_health(timeout=2):
            self.status.emit('模型已连接，可以开始聊天。'); return
        url=urlparse(client.base_url)
        if url.hostname not in ('localhost','127.0.0.1','::1'):
            self.status.emit('配置的远程模型暂时不可用，请检查连接。'); return
        launcher=Path(__file__).resolve().parent/'llama-serve'
        if not launcher.exists(): self.status.emit('没有找到模型启动脚本。'); return
        try:
            logdir=Path.home()/'.cache/llm-assistant'; logdir.mkdir(parents=True,exist_ok=True)
            with (logdir/'model-start.log').open('ab') as log:
                proc=subprocess.Popen([str(launcher),os.environ.get('LLM_ASSISTANT_MODEL_ALIAS','lfm28'),
                    '--no-replace','--port',str(url.port or 18080)],stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
            self.status.emit('正在加载本地模型，常用电脑控制仍可使用。')
            for _ in range(90):
                if self.isInterruptionRequested(): return
                if client.check_health(timeout=1): self.status.emit('模型已连接，可以开始聊天。'); return
                if proc.poll() is not None:
                    self.status.emit('模型未就绪，请查看 ~/.cache/llm-assistant/model-start.log'); return
                self.msleep(1000)
            self.status.emit('模型仍未就绪，请查看 ~/.cache/llm-assistant/model-start.log')
        except Exception as e: self.status.emit(f'模型启动失败：{e}')

class VoiceService(QObject):
    received=pyqtSignal(dict)
    def __init__(self,parent=None):
        super().__init__(parent)
        self.proc=QProcess(self); self.buffer=b''; self.stderr=''; self.pending=None
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(self._diagnostic)
        self.proc.started.connect(self._started)
        self.proc.errorOccurred.connect(lambda _: self.received.emit({'type':'error','message':'无法启动语音服务，请检查 Python 与语音依赖。'}))
        self.proc.finished.connect(self._exited)
    def _diagnostic(self):
        self.stderr=(self.stderr+bytes(self.proc.readAllStandardError()).decode(errors='replace'))[-2000:]
    def _exited(self,code,status):
        self.buffer=b''
        self.received.emit({'type':'service_stopped','code':code})
    def _send(self,cmd): self.proc.write((json.dumps(cmd)+'\n').encode())
    def _started(self):
        if self.pending: self._send(self.pending); self.pending=None
    def start(self,request_id):
        cmd={'type':'start','id':request_id}
        if self.proc.state()==QProcess.ProcessState.NotRunning:
            self.pending=cmd; self.buffer=b''; self.stderr=''
            self.proc.setProgram(sys.executable)
            self.proc.setArguments([str(Path(__file__).resolve().parent/'voice-control.py'),'--service','--json-events','--model',get_config().get('voice.model','large')])
            self.proc.start()
        else: self._send(cmd)
    def cancel(self):
        self.pending=None
        if self.proc.state()!=QProcess.ProcessState.NotRunning: self._send({'type':'cancel'})
    def close_service(self):
        if self.proc.state()!=QProcess.ProcessState.NotRunning:
            self._send({'type':'shutdown'})
            if not self.proc.waitForFinished(800): self.proc.kill(); self.proc.waitForFinished(800)
    def _read(self):
        self.buffer+=bytes(self.proc.readAllStandardOutput())
        while b'\n' in self.buffer:
            line,self.buffer=self.buffer.split(b'\n',1)
            try:
                event=json.loads(line)
                if isinstance(event,dict): self.received.emit(event)
            except (ValueError,UnicodeDecodeError): pass

class LLMAssistantWindow(QMainWindow):
    confirm_requested=pyqtSignal(object)
    def __init__(self):
        super().__init__()
        self.setWindowTitle('小希 · 本地助手')
        self.setWindowIcon(app_icon())
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint|Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.session=AssistantSession(); self.worker=None; self.voice_id=None
        self.busy=False; self.expanded=False; self.reply_label=None; self.reply_text=''
        self.pending_confirmation=None; self.quit_requested=False; self.failed=False
        self.auto_dock=False
        self.message_history=[]; self.history_index=0; self.history_draft=''
        self.message_records=[]
        self.idle_timer=QTimer(self); self.idle_timer.setSingleShot(True); self.idle_timer.setInterval(8000)
        self.idle_timer.timeout.connect(self._collapse_if_idle)
        self.voice=VoiceService(self); self.voice.received.connect(self._voice_event)
        self.confirm_requested.connect(self._confirm)
        set_confirmation_handler(self._request_confirmation)
        self._init_ui(); self._init_tray()
        self.shortcut=QShortcut(QKeySequence('Ctrl+Space'),self); self.shortcut.activated.connect(self.toggle_voice)
        self.escape=QShortcut(QKeySequence('Escape'),self); self.escape.activated.connect(self.cancel_voice)
        self.mode_shortcuts=[]
        for number,mode in enumerate(('mini','standard','chat','sidebar','dock'),1):
            shortcut=QShortcut(QKeySequence('Alt+'+str(number)),self)
            shortcut.activated.connect(lambda m=mode:self.set_window_mode(m))
            self.mode_shortcuts.append(shortcut)
        self.window_settings=QSettings('LocalAssistant','Window')
        saved=self.window_settings.value('geometry')
        if saved is not None: self.restoreGeometry(saved)
        self.setStyleSheet(STYLE)
        self.set_window_mode(self.window_settings.value('window_mode','dock'))
        QApplication.instance().installEventFilter(self)

    def _button(self,text,callback,name=None):
        b=QPushButton(text); b.clicked.connect(callback)
        if name: b.setObjectName(name)
        return b

    def _init_ui(self):
        surface=QFrame(); surface.setObjectName('surface'); self.setCentralWidget(surface)
        layout=QVBoxLayout(surface); layout.setContentsMargins(22,18,22,16); layout.setSpacing(12)
        self.surface_layout=layout
        self.header_widget=QWidget()
        header=QHBoxLayout(self.header_widget); header.setContentsMargins(0,0,0,0)
        self.header_layout=header
        mark=QLabel(); mark.setPixmap(app_icon().pixmap(24,24)); header.addWidget(mark)
        title=QLabel('小希'); title.setObjectName('brand'); title.setToolTip('拖动标题或空白区域移动窗口'); title.setCursor(Qt.CursorShape.OpenHandCursor); header.addWidget(title)
        local=QLabel('LOCAL ASSISTANT'); local.setObjectName('eyebrow'); header.addWidget(local)
        self.brand_mark=mark; self.brand_eyebrow=local; self.brand_title=title
        header.addStretch()
        self.state=QLabel('待命'); self.state.setObjectName('status'); header.addWidget(self.state)
        self.size_btn=self._button('',lambda:None,'quiet')
        self.size_btn.setIcon(icon('layout',size=18))
        self.size_btn.setFixedSize(30,28)
        self.size_btn.setAccessibleName('窗口尺寸')
        self.size_btn.setStyleSheet('QPushButton { padding: 3px; } QPushButton::menu-indicator { image: none; width: 0px; }')
        self.size_btn.setToolTip('切换小悬浮窗、标准、对话、侧边栏')
        self.size_menu=QMenu('窗口尺寸',self)
        self.size_actions={}
        for mode,label in (('mini','小悬浮窗'),('standard','标准'),('chat','展开对话'),('sidebar','侧边栏'),('dock','贴边图标 · 自动收起')):
            action=self.size_menu.addAction(label)
            action.setCheckable(True)
            action.triggered.connect(lambda checked,m=mode:self.set_window_mode(m))
            self.size_actions[mode]=action
        self.size_btn.setMenu(self.size_menu); header.addWidget(self.size_btn)
        close=self._button('',self._hide_or_dock); close.setIcon(icon('close')); close.setFixedSize(28,28); close.setToolTip('隐藏到托盘'); header.addWidget(close)
        self.close_btn=close
        layout.addWidget(self.header_widget)
        self.dock_btn=DockButton()
        self.dock_btn.setIcon(app_icon()); self.dock_btn.setIconSize(QSize(28,28))
        self.dock_btn.setFixedSize(32,48); self.dock_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.dock_btn.setToolTip('点击展开小希 · 拖动换边 · 右键切换尺寸')
        self.dock_btn.setAccessibleName('展开小希助手')
        self.dock_btn.setStyleSheet('QPushButton { padding: 0; border: 0; border-radius: 10px; }')
        self.dock_btn.clicked.connect(self.reveal_dock)
        self.dock_btn.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.dock_btn.customContextMenuRequested.connect(lambda p:self.size_menu.exec(self.dock_btn.mapToGlobal(p)))
        layout.addWidget(self.dock_btn,0,Qt.AlignmentFlag.AlignCenter)
        self.hero_widget=QWidget()
        hero=QHBoxLayout(self.hero_widget); hero.setContentsMargins(0,0,0,0)
        words=QVBoxLayout(); words.setSpacing(4)
        self.hero_layout=hero
        self.headline=QLabel('说一句，我来帮你'); self.headline.setObjectName('title'); words.addWidget(self.headline)
        self.subtitle=QLabel('调节电脑，打开应用，或聊一聊。'); self.subtitle.setObjectName('subtitle'); words.addWidget(self.subtitle)
        hero.addLayout(words,1)
        self.mic=self._button('',self.toggle_voice,'mic'); self.mic.setFixedSize(64,64); self.mic.setIcon(icon('mic','white',28)); self.mic.setIconSize(QSize(28,28)); self.mic.setToolTip('点击说话 · 窗口内 Ctrl+Space'); hero.addWidget(self.mic)
        layout.addWidget(self.hero_widget)
        self.examples_widget=QWidget()
        examples=QHBoxLayout(self.examples_widget); examples.setContentsMargins(0,0,0,0); examples.setSpacing(7)
        for text in ('音量设为30%','打开浏览器','屏幕暗一点'):
            examples.addWidget(self._button(text,lambda _,t=text:self.send(t),'chip'))
        examples.addStretch(); layout.addWidget(self.examples_widget)
        self.scroll=QScrollArea(); self.scroll.setWidgetResizable(True)
        feed=QWidget(); feed.setObjectName('feed'); self.feed=QVBoxLayout(feed); self.feed.setContentsMargins(0,0,8,0); self.feed.setSpacing(10); self.feed.addStretch()
        self.scroll.setWidget(feed); self.scroll.hide(); layout.addWidget(self.scroll,1)
        composer=QFrame(); self.composer=composer; composer.setObjectName('composer'); row=QHBoxLayout(composer); row.setContentsMargins(5,5,5,5); row.setSpacing(4)
        self.composer_row=row
        self.input=QLineEdit(); self.input.setPlaceholderText('也可以输入指令…'); self.input.returnPressed.connect(lambda:self.send()); row.addWidget(self.input,1)
        self.send_btn=self._button('发送',lambda:self.send(),'primary'); row.addWidget(self.send_btn); layout.addWidget(composer)
        self.feedback=FeedbackLabel('点击麦克风开始 · Esc 取消录音'); self.feedback.setObjectName('detail'); self.feedback.setWordWrap(True); self.feedback.setMinimumHeight(32); layout.addWidget(self.feedback)
        self.footer_widget=QWidget()
        footer=QHBoxLayout(self.footer_widget); footer.setContentsMargins(0,0,0,0)
        self.expand_btn=self._button('展开对话',self.toggle_expand,'quiet'); self.expand_btn.setIcon(icon('expand',size=16)); footer.addWidget(self.expand_btn)
        self.undo_btn=self._button('撤销调节',lambda:self.send('撤销上次调节'),'quiet'); footer.addWidget(self.undo_btn)
        self.copy_btn=self._button('复制对话',self.copy_conversation,'quiet'); self.copy_btn.setToolTip('复制当前保留的完整对话，便于粘贴分享'); footer.addWidget(self.copy_btn)
        footer.addStretch()
        self.model_btn=self._button('启动模型',self.start_model,'quiet'); self.model_btn.setToolTip('音量、亮度和语音识别无需启动大模型'); footer.addWidget(self.model_btn)
        layout.addWidget(self.footer_widget)

    def _init_tray(self):
        self.tray=QSystemTrayIcon(app_icon(),self)
        self.tray.setToolTip('小希 · 本地助手')
        menu=QMenu(); menu.addAction('显示助手',self.show_window); menu.addAction('说一句',self.activate_voice); menu.addMenu(self.size_menu); menu.addSeparator(); menu.addAction('退出',self._quit)
        self.tray.setContextMenu(menu); self.tray.activated.connect(lambda reason:self.show_window() if reason==QSystemTrayIcon.ActivationReason.Trigger else None); self.tray.show()
    def show_window(self):
        if self.window_mode=='dock': self.set_window_mode('mini',preserve_dock=True)
        self.show(); self.raise_(); self.activateWindow(); self._arm_autohide()
    def activate_voice(self): self.show_window(); self.toggle_voice()
    def _status(self,text,state='idle'):
        self.state.setText(text); self.state.setProperty('state',state); self.state.style().unpolish(self.state); self.state.style().polish(self.state)
        if hasattr(self,'tray'): self.tray.setToolTip('小希 · '+text)
    def set_window_mode(self,mode,preserve_dock=False):
        if mode not in self.size_actions: mode='standard'
        if mode=='dock': self.auto_dock=True
        elif not preserve_dock: self.auto_dock=False
        if mode=='dock' and (self.busy or self.voice_id or self.pending_confirmation): mode='mini'
        self.window_mode=mode
        mini=mode=='mini'; dock=mode=='dock'; sidebar=mode=='sidebar'
        self.expanded=mode in ('chat','sidebar')
        self.scroll.setVisible(self.expanded)
        self.header_widget.setVisible(not dock)
        self.dock_btn.setVisible(dock)
        self.brand_mark.setVisible(not mini)
        self.brand_eyebrow.hide()
        self.state.setVisible(not mini)
        self.hero_widget.setVisible(mode=='standard')
        self.headline.setVisible(not mini)
        self.subtitle.setVisible(not mini)
        self.subtitle.setWordWrap(True)
        self.examples_widget.setVisible(mode=='standard')
        self.composer.setVisible(not mini and not dock)
        self.footer_widget.setVisible(not mini and not dock)
        self.feedback.setVisible(not dock)
        margins=(2,4,2,4) if dock else (8,6,8,6) if mini else (14,16,14,14) if sidebar else (20,18,20,16)
        self.surface_layout.setContentsMargins(*margins)
        self.surface_layout.setSpacing(2 if dock else 4 if mini else 10)
        self.header_layout.setSpacing(3 if mini else 6)
        self.brand_title.setStyleSheet('font-size: 13px;' if mini else '')
        self.size_btn.setFixedSize(24 if mini else 30,26 if mini else 28)
        self.close_btn.setFixedSize(22 if mini else 28,26 if mini else 28)
        self.hero_layout.removeWidget(self.mic)
        self.composer_row.removeWidget(self.mic)
        self.header_layout.removeWidget(self.mic)
        if mini: self.header_layout.insertWidget(self.header_layout.indexOf(self.size_btn),self.mic)
        elif self.expanded: self.composer_row.insertWidget(0,self.mic)
        else: self.hero_layout.addWidget(self.mic)
        self.mic.setVisible(not dock)
        mic_size=32 if mini else 36 if self.expanded else 60
        self.mic.setFixedSize(mic_size,mic_size)
        self.mic.setStyleSheet('QPushButton#mic { border-radius: '+str(mic_size//2)+'px; padding: 0px; }')
        self.mic.setIconSize(QSize(18 if mini else 22 if self.expanded else 28,18 if mini else 22 if self.expanded else 28))
        self.feedback.setCompact(mini or self.expanded)
        self.feedback.setMinimumHeight(16 if mini else 20 if self.expanded else 32)
        self.feedback.setMaximumHeight(16 if mini else 20 if self.expanded else 48)
        self.headline.setStyleSheet('font-size: 21px;')
        self.expand_btn.setText('收起对话' if self.expanded else '展开对话')
        self.expand_btn.setIcon(icon('collapse' if self.expanded else 'expand',size=16))
        self.input.setPlaceholderText('输入消息…' if sidebar else '也可以输入指令…')
        screen=self.screen(); geo=screen.availableGeometry() if screen else None
        width,height={'dock':(36,56),'mini':(176,72),'standard':(440,328),'chat':(520,680),'sidebar':(336,geo.height()-24 if geo else 760)}[mode]
        if geo: width=min(width,geo.width()); height=min(height,geo.height())
        self.setFixedSize(width,height)
        marker='侧边栏' if sidebar else '贴边图标' if dock else '贴边展开' if mini and self.auto_dock else ''
        self.setWindowTitle('小希 · 本地助手'+(' · '+marker if marker else ''))
        self._snap_fallback()
        for key,action in self.size_actions.items(): action.setChecked(key==('dock' if self.auto_dock else mode))
        if hasattr(self,'window_settings'):
            self.window_settings.setValue('window_mode','dock' if self.auto_dock else mode)
            self.window_settings.sync()
        self._arm_autohide()

    def reveal_dock(self):
        self.set_window_mode('mini',preserve_dock=True); self.show_window()

    def _hide_or_dock(self):
        if self.auto_dock: self.set_window_mode('dock')
        else: self.hide()

    def _arm_autohide(self):
        if self.auto_dock and self.window_mode=='mini': self.idle_timer.start()
        else: self.idle_timer.stop()

    def _collapse_if_idle(self):
        if not self.auto_dock or self.window_mode!='mini' or not self.isVisible(): return
        if self.busy or self.voice_id or self.pending_confirmation or QApplication.activePopupWidget() or QApplication.mouseButtons()!=Qt.MouseButton.NoButton:
            self.idle_timer.start(); return
        self.set_window_mode('dock')

    def eventFilter(self,watched,event):
        if watched is self.input and event.type()==QEvent.Type.KeyPress and event.modifiers()==Qt.KeyboardModifier.NoModifier:
            if event.key()==Qt.Key.Key_Up:
                self._navigate_history(-1); return True
            if event.key()==Qt.Key.Key_Down:
                self._navigate_history(1); return True
        if event.type() in (QEvent.Type.MouseButtonPress,QEvent.Type.MouseMove,QEvent.Type.KeyPress,QEvent.Type.Wheel,QEvent.Type.Enter):
            if isinstance(watched,QWidget) and watched.window() is self: self._arm_autohide()
        return super().eventFilter(watched,event)

    def _navigate_history(self,direction):
        if not self.message_history: return
        end=len(self.message_history)
        if direction<0:
            if self.history_index==end: self.history_draft=self.input.text()
            self.history_index=max(0,self.history_index-1)
        elif self.history_index<end:
            self.history_index+=1
        text=self.history_draft if self.history_index==end else self.message_history[self.history_index]
        self.input.setText(text)
        self.input.setCursorPosition(len(text))

    def _snap_fallback(self):
        # Wayland positioning is implemented by the companion KWin script.
        if QApplication.platformName().startswith('wayland'): return
        if self.window_mode not in ('dock','sidebar') and not (self.window_mode=='mini' and self.auto_dock): return
        screen=self.screen()
        if not screen: return
        area=screen.availableGeometry()
        x=area.left() if self.frameGeometry().center().x()<area.center().x() else area.right()-self.width()+1
        self.move(x,max(area.top(),min(self.y(),area.bottom()-self.height()+1)))

    def toggle_expand(self):
        self.set_window_mode('standard' if self.expanded else 'chat')

    def _scroll_to_bottom(self):
        """在布局更新后保持流式回复的最新内容可见。"""
        QTimer.singleShot(0, lambda: self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum()))

    def _message(self,role,text):
        box=QFrame(); box.setProperty('role',role); col=QVBoxLayout(box); col.setContentsMargins(14,11,14,12); col.setSpacing(5)
        speaker=QLabel('你' if role=='user' else '小希'); speaker.setObjectName('speaker'); col.addWidget(speaker)
        body=QLabel(text); body.setObjectName('message'); body.setWordWrap(True); body.setTextFormat(Qt.TextFormat.PlainText); body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse); body.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Minimum); col.addWidget(body)
        self.feed.insertWidget(self.feed.count()-1,box)
        self.message_records.append((role,body))
        while self.feed.count()>61:
            old=self.feed.takeAt(0); old.widget().deleteLater()
            if self.message_records: self.message_records.pop(0)
        self._scroll_to_bottom()
        return body

    def copy_conversation(self):
        lines=[]
        for role,label in self.message_records:
            text=label.text().strip()
            if text: lines.append(('你' if role=='user' else '小希')+'：'+text)
        if not lines:
            self.feedback.setText('当前还没有可复制的对话。')
            return
        QApplication.clipboard().setText('\n\n'.join(lines))
        self.feedback.setText(f'已复制 {len(lines)} 条对话，可直接粘贴分享。')
    def send(self,text=None):
        if self.busy: return
        text=text if isinstance(text,str) else self.input.text().strip()
        if not text: return
        if self.voice_id: self.cancel_voice()
        self.message_history.append(text)
        self.message_history=self.message_history[-5:]
        self.history_index=len(self.message_history); self.history_draft=''
        self.input.clear(); self._message('user',text)
        self.reply_label=None; self.reply_text=''; self.failed=False
        self.busy=True; self.send_btn.setEnabled(False); self.input.setEnabled(False); self.mic.setEnabled(False)
        self._status('处理中'); self.feedback.setText('正在处理你的请求…')
        self.worker=QueryWorker(self.session,text); self.worker.received.connect(self._query_event); self.worker.finished.connect(self._query_finished); self.worker.start()
    def _query_event(self,event):
        kind=event.get('type')
        if kind=='text':
            self.reply_text+=event.get('content','')
            if not self.reply_label: self.reply_label=self._message('assistant','')
            self.reply_label.setText(self.reply_text)
            self._scroll_to_bottom()
            self.feedback.setText(self.reply_text[:100])
            if self.reply_text.startswith('操作失败'): self.failed=True
        elif kind=='tool_call':
            self._status('执行中'); self.feedback.setText('正在执行操作…')
        elif kind=='thinking': self._status('理解中')
        elif kind=='error':
            self.failed=True; self.reply_text=event.get('content','请求失败')
            self._message('assistant',self.reply_text); self.feedback.setText(self.reply_text[:140]); self._status('未完成','error')
    def _query_finished(self):
        self.busy=False; self._arm_autohide(); self.send_btn.setEnabled(True); self.input.setEnabled(True); self.mic.setEnabled(True)
        self._status('未完成' if self.failed else '完成','error' if self.failed else 'idle')
        if not self.reply_text: self.feedback.setText('没有收到回复，请重试。')
        if self.quit_requested: self._finish_quit()
    def toggle_voice(self):
        if self.busy: return
        if self.voice_id: self.cancel_voice(); return
        self.voice_id=uuid.uuid4().hex; self.input.clear(); self.mic.setProperty('active',True); self.mic.setIcon(icon('stop','white',28)); self.mic.style().unpolish(self.mic); self.mic.style().polish(self.mic)
        self._status('准备中'); self.feedback.setText('首次使用正在加载语音模型…'); self.voice.start(self.voice_id)
    def cancel_voice(self):
        self.voice.cancel(); self.voice_id=None; self._reset_mic(); self._status('已取消'); self.feedback.setText('录音已取消，没有执行操作。')
    def _reset_mic(self):
        self._arm_autohide()
        self.mic.setProperty('active',False); self.mic.setIcon(icon('mic','white',28)); self.mic.style().unpolish(self.mic); self.mic.style().polish(self.mic)
    def _voice_event(self,event):
        kind=event.get('type')
        if kind=='service_stopped':
            if self.voice_id:
                self.voice_id=None; self._reset_mic(); self._status('未连接','error'); self.feedback.setText('语音服务已退出，请重试。')
            return
        if event.get('id') not in (None,self.voice_id) or (self.voice_id is None and kind!='ready'): return
        if kind=='listening': self._status('正在听','listening'); self.feedback.setText('请说话 · 常用指令自动执行 · 再点一次取消')
        elif kind=='partial': self.input.setText(event.get('text','')); self.feedback.setText('正在听：'+event.get('text',''))
        elif kind=='final':
            text=event.get('text','').strip(); self.voice_id=None; self._reset_mic()
            if text:
                if self.session.desktop.parse(text) is not None:
                    self.send(text)
                else:
                    if self.window_mode=='mini': self.set_window_mode('standard')
                    self.input.setText(text); self.input.setFocus()
                    self._status('待发送')
                    self.feedback.setText('已转为文字，请确认识别内容后发送。')
        elif kind=='error':
            self.voice_id=None; self._reset_mic(); self._status('未听清','error'); self.feedback.setText(event.get('message','识别失败')[:160])
    def _request_confirmation(self,name,args,reason):
        pending={'event':threading.Event(),'approved':False,'name':name,'args':args,'reason':reason}
        self.pending_confirmation=pending; self.confirm_requested.emit(pending)
        pending['event'].wait(60)
        return pending['approved']
    def _confirm(self,pending):
        if self.quit_requested: pending['event'].set(); return
        preview=pending['args'].get('command',pending['args'].get('path',''))
        self.show_window(); self._status('等待确认')
        dialog=QMessageBox(QMessageBox.Icon.Question,'确认操作',f"{pending['reason']}\n\n{str(preview)[:600]}",QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,self)
        dialog.setTextFormat(Qt.TextFormat.PlainText); dialog.setDefaultButton(QMessageBox.StandardButton.No)
        timer=QTimer(dialog); timer.setSingleShot(True); timer.timeout.connect(dialog.reject); timer.start(55000)
        pending['approved']=dialog.exec()==QMessageBox.StandardButton.Yes
        pending['event'].set(); self.pending_confirmation=None
    def start_model(self):
        if getattr(self,'model_worker',None) and self.model_worker.isRunning(): return
        self.model_btn.setEnabled(False)
        self.model_worker=ModelWorker()
        self.model_worker.status.connect(self._model_status)
        self.model_worker.finished.connect(lambda:self.model_btn.setEnabled(True))
        self.model_worker.start()
    def _model_status(self,message):
        if not self.busy and not self.voice_id: self.feedback.setText(message)
    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.window_mode=='mini':
            self.set_window_mode('standard'); self.input.setFocus(); event.accept()
        else: super().mouseDoubleClickEvent(event)
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            handle=self.windowHandle()
            if handle and handle.startSystemMove():
                self.drag_pos=None
                event.accept()
                return
            self.drag_pos=event.globalPosition().toPoint()-self.frameGeometry().topLeft()
        else:
            self.drag_pos=None
    def mouseMoveEvent(self,event):
        if getattr(self,'drag_pos',None) is not None and event.buttons() & Qt.MouseButton.LeftButton: self.move(event.globalPosition().toPoint()-self.drag_pos)
    def mouseReleaseEvent(self,event):
        self.drag_pos=None
        self._snap_fallback()
        super().mouseReleaseEvent(event)
    def showEvent(self,event):
        super().showEvent(event)
        handle=self.windowHandle()
        if handle and not getattr(self,'screen_hooked',False):
            handle.screenChanged.connect(self._screen_changed)
            self.screen_hooked=True
        self._arm_autohide()

    def _screen_changed(self,screen):
        if self.window_mode=='sidebar': self.set_window_mode('sidebar',preserve_dock=True)

    def hideEvent(self,event):
        if hasattr(self,'idle_timer'): self.idle_timer.stop()
        if hasattr(self,'window_settings'):
            self.window_settings.setValue('geometry',self.saveGeometry())
            self.window_settings.sync()
        super().hideEvent(event)
    def closeEvent(self,event): event.ignore(); self.hide()
    def _quit(self):
        self.quit_requested=True; self.voice_id=None; self.voice.close_service()
        if getattr(self,'model_worker',None) and self.model_worker.isRunning():
            self.model_worker.requestInterruption()
            self.model_worker.wait(3500)
        if self.pending_confirmation: self.pending_confirmation['event'].set()
        if self.worker and self.worker.isRunning():
            self.worker.cancelled=True; self.hide(); self._status('正在退出')
            # Do not terminate a Python thread while a tool or network request is running.
            return
        self._finish_quit()
    def _finish_quit(self):
        self.hide(); self.tray.hide(); QApplication.quit()

def main():
    parser=argparse.ArgumentParser(description='小希 · 本地助手')
    parser.add_argument('--voice',action='store_true')
    parser.add_argument('--mode',choices=('mini','standard','chat','sidebar','dock'))
    options,qt_args=parser.parse_known_args()
    app=QApplication([sys.argv[0],*qt_args]); app.setQuitOnLastWindowClosed(False)
    app.setApplicationName('llm-assistant'); app.setApplicationDisplayName('小希 · 本地助手')
    app.setDesktopFileName('llm-assistant'); app.setWindowIcon(app_icon())
    name=f'llm-assistant-{os.getuid()}'
    peer=QLocalSocket(); peer.connectToServer(name)
    if peer.waitForConnected(250):
        peer.write(json.dumps(dict(voice=options.voice,mode=options.mode)).encode()+bytes([10])); peer.flush(); peer.waitForBytesWritten(250)
        return
    server=QLocalServer(); server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    if not server.listen(name):
        if server.serverError()==QAbstractSocket.SocketError.AddressInUseError:
            QLocalServer.removeServer(name)
            if not server.listen(name): raise RuntimeError('无法创建助手本地通信接口')
        else: raise RuntimeError(server.errorString())
    window=LLMAssistantWindow()
    if options.mode: window.set_window_mode(options.mode)
    connections=[]
    def receive():
        socket=server.nextPendingConnection(); connections.append(socket)
        buffer=bytearray()
        def handle():
            buffer.extend(bytes(socket.readAll()))
            if len(buffer)>4096: socket.disconnectFromServer(); return
            raw=bytes(buffer)
            if raw in (b'voice',b'show'):
                request={'voice':raw==b'voice'}
            elif 10 in raw:
                try: request=json.loads(raw.splitlines()[0])
                except (ValueError,UnicodeDecodeError): socket.disconnectFromServer(); return
                if not isinstance(request,dict): socket.disconnectFromServer(); return
            else: return
            mode=request.get('mode')
            if isinstance(mode,str) and mode in window.size_actions: window.set_window_mode(mode)
            if mode=='dock': window.show()
            else: window.show_window()
            if request.get('voice') is True and not window.voice_id and not window.busy: window.activate_voice()
            socket.disconnectFromServer()
        socket.readyRead.connect(handle)
        socket.disconnected.connect(lambda: (connections.remove(socket),socket.deleteLater()))
        if socket.bytesAvailable(): handle()
    server.newConnection.connect(receive)
    window.show()
    if options.voice: QTimer.singleShot(0,window.activate_voice)
    sys.exit(app.exec())

if __name__=='__main__': main()
