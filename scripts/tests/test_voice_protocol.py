"""Exercise service cancellation without recording or changing desktop state."""
import json
import queue
import sys
from unittest.mock import Mock, patch
from llm_core.voice import Recognizer

class FakeAudio:
    paInt16=8
    paContinue=0
    def __init__(self,commands): self.commands=commands; self.opened=False; self.closed=False
    def PyAudio(self): return self
    def open(self,**kwargs): self.opened=True; self.commands.put({'type':'cancel'}); return self
    def stop_stream(self): pass
    def close(self): self.closed=True
    def terminate(self): self.closed=True

def test_cancel_after_load_never_opens_mic(capsys):
    commands=queue.Queue(); commands.put({'type':'cancel'})
    fake=FakeAudio(commands); r=Recognizer(); r.load=lambda:None
    with patch.dict(sys.modules,{'pyaudio':fake,'vosk':Mock()}):
        assert r.listen('cancelled-id',commands)=='cancel'
    assert not fake.opened
    events=[json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert events[-1]['type']=='cancelled'

def test_cancel_recording_releases_device(capsys):
    commands=queue.Queue(); fake=FakeAudio(commands); r=Recognizer(); r.load=lambda:None
    with patch.dict(sys.modules,{'pyaudio':fake,'vosk':Mock()}):
        assert r.listen('id',commands)=='cancel'
    assert fake.opened and fake.closed
    assert all(json.loads(x)['type']!='final' for x in capsys.readouterr().out.splitlines())
