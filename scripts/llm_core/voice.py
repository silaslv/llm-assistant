"""Vosk microphone service. stdout is JSONL only; diagnostics go to stderr."""
import argparse
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time

WAKE_WORDS=('小希','小西','小溪','小曦')

def emit(kind,request_id=None,**data):
    print(json.dumps(dict(type=kind,id=request_id,**data),ensure_ascii=False),flush=True)

class Recognizer:
    def __init__(self,model='large',device=None):
        self.model_name=model
        self.device=device
        self.model=None

    def load(self):
        if self.model is None:
            from vosk import Model, SetLogLevel
            from llm_core.config import get_config
            SetLogLevel(-1)
            default=Path.home()/'ai/models'/('vosk-model-small-cn-0.22' if self.model_name=='small' else 'vosk-model-cn-0.22')
            path=Path(get_config().get('voice.model_path',str(default))).expanduser()
            if not path.is_dir(): raise RuntimeError(f'语音模型不存在：{path}')
            self.model=Model(str(path))

    def listen(self,request_id,commands=None,timeout=18):
        import pyaudio
        from vosk import KaldiRecognizer
        emit('loading',request_id)
        self.load()
        pa=pyaudio.PyAudio()
        stream=None
        audio=queue.Queue(maxsize=64)
        rec=KaldiRecognizer(self.model,16000)
        def callback(data,frames,time_info,status):
            try: audio.put_nowait(data)
            except queue.Full: pass
            return (None,pyaudio.paContinue)
        try:
            # Cancellation during a cold model load is honored before opening the mic.
            if commands and not commands.empty():
                cmd=commands.get_nowait()
                if cmd.get('type') in ('cancel','shutdown'):
                    emit('cancelled',request_id)
                    return cmd.get('type')
            stream=pa.open(format=pyaudio.paInt16,channels=1,rate=16000,input=True,
                input_device_index=self.device,frames_per_buffer=1600,stream_callback=callback)
            emit('listening',request_id)
            deadline=time.monotonic()+timeout
            last=''
            while time.monotonic()<deadline:
                if commands:
                    try:
                        cmd=commands.get_nowait()
                        if cmd.get('type') in ('cancel','shutdown'):
                            emit('cancelled',request_id)
                            return cmd['type']
                    except queue.Empty: pass
                try: data=audio.get(timeout=.05)
                except queue.Empty: continue
                if rec.AcceptWaveform(data):
                    text=json.loads(rec.Result()).get('text','').strip()
                    if text:
                        emit('final',request_id,text=text)
                        return text
                else:
                    partial=json.loads(rec.PartialResult()).get('partial','').strip()
                    if partial and partial!=last:
                        emit('partial',request_id,text=partial); last=partial
            text=json.loads(rec.FinalResult()).get('text','').strip()
            if text: emit('final',request_id,text=text); return text
            emit('error',request_id,message='没有听清，请靠近麦克风再试一次。')
        finally:
            if stream: stream.stop_stream(); stream.close()
            pa.terminate()

def service(args):
    commands=queue.Queue()
    def receive():
        for line in sys.stdin:
            try:
                cmd=json.loads(line)
                if isinstance(cmd,dict): commands.put(cmd)
            except ValueError: pass
        commands.put({'type':'shutdown'})
    threading.Thread(target=receive,daemon=True).start()
    recognizer=Recognizer(args.model,args.device)
    emit('ready')
    while True:
        cmd=commands.get()
        if cmd.get('type')=='shutdown': break
        if cmd.get('type')!='start': continue
        try:
            result=recognizer.listen(cmd.get('id'),commands,args.timeout)
            if result=='shutdown': break
        except Exception as e: emit('error',cmd.get('id'),message=str(e))
        finally: emit('idle',cmd.get('id'))

def main():
    ap=argparse.ArgumentParser(description='本地语音识别与桌面控制')
    ap.add_argument('--service',action='store_true')
    ap.add_argument('--once',action='store_true')
    ap.add_argument('--transcribe',action='store_true',help='只识别，不执行')
    ap.add_argument('--json-events',action='store_true',help='JSONL 事件（服务默认格式）')
    ap.add_argument('--mode',choices=['fixed','llm'],default='fixed')
    ap.add_argument('--model',choices=['small','large'],default='large')
    ap.add_argument('--device',type=int)
    ap.add_argument('--timeout',type=float,default=18)
    args=ap.parse_args()
    if args.service: service(args); return
    from llm_core.session import AssistantSession
    session=AssistantSession()
    recognizer=Recognizer(args.model,args.device)
    try:
        while True:
            text=recognizer.listen(1,timeout=args.timeout)
            if text and not args.transcribe:
                text=''.join(text.split())
                # Hands-free use always requires a wake word; one-shot is explicitly activated.
                if not args.once:
                    wake=next((w for w in WAKE_WORDS if text.startswith(w)),None)
                    if not wake: continue
                    text=text[len(wake):]
                intent=session.desktop.parse(text)
                if intent: emit('reply',text=session.desktop.execute(intent))
                elif args.mode=='llm':
                    # No confirmation handler in CLI: sensitive tools retain fail-closed policy.
                    for event in session.stream(text): emit('assistant',event=event)
                else: emit('reply',text='没有匹配到常用指令。')
            if args.once: break
    except KeyboardInterrupt: pass
    except Exception as e:
        emit('error',message=str(e)); sys.exit(1)

if __name__=='__main__': main()
