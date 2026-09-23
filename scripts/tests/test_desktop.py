from unittest.mock import Mock, patch
import subprocess
import pytest
from llm_core.desktop import Intent, parse_intent, DesktopController
from llm_core.intent import ModelIntentRecognizer, looks_like_desktop_command
from llm_core.session import AssistantSession

@pytest.mark.parametrize('text,expected',[
    ('声音大一点',Intent('volume_delta',5)),
    ('音量减小',Intent('volume_delta',-5)),
    ('把声音降低百分之十',Intent('volume_delta',-10)),
    ('音量增大',Intent('volume_delta',5)),
    ('音量增加',Intent('volume_delta',5)),
    ('音量减少',Intent('volume_delta',-5)),
    ('音量调到百分之三十',Intent('volume_set',30)),
    ('音量设为30%',Intent('volume_set',30)),
    ('小希，把亮度调到最暗',Intent('brightness_set',5)),
    ('屏幕暗一点',Intent('brightness_delta',-5)),
    ('请把声音调小百分之十',Intent('volume_delta',-10)),
    ('静音',Intent('mute',True)),
    ('取消静音',Intent('mute',False)),
    ('不要静音',Intent('cancel')),
    ('别打开浏览器',Intent('cancel')),
    ('打开浏览器',Intent('app','浏览器')),
    ('下一首',Intent('media','next')),
    ('再小一点',Intent('clarify')),
    ('撤销上次调节',Intent('undo')),
    ('解释一下如何打开浏览器',None),
    ('音量调大然后打开浏览器',None),
    ('打开浏览器; rm -rf /',None),
])
def test_intents(text,expected):
    assert parse_intent(text)==expected

@pytest.fixture
def controller():
    with patch('llm_core.desktop.get_auditor'):
        c=DesktopController()
        c.state={'volume':(40,30),'brightness':(500,1000),'mute':False}
        c.writes=[]
        c._read=lambda target:c.state[target]
        def write(target,value,raw=False):
            c.writes.append((target,value,raw))
            if target=='volume': c.state[target]=value if isinstance(value,tuple) else (value,value)
            elif target=='brightness': c.state[target]=value if raw else (value*10,1000)
            else: c.state[target]=value
        c._write=write
        yield c

def test_followup_and_undo(controller):
    c=controller
    assert c.execute(c.parse('音量设为30%'))=='音量已设为 30%。'
    assert c.parse('再小一点')==Intent('volume_delta',-5)
    c.execute(c.parse('再小一点'))
    assert c.state['volume']==(25,25)
    c.execute(Intent('undo')); assert c.state['volume']==(30,30)

def test_undo_preserves_balance(controller):
    c=controller; c.execute(Intent('volume_set',20)); c.execute(Intent('undo'))
    assert c.state['volume']==(40,30)

def test_undo_does_not_override_external_change(controller):
    c=controller; c.execute(Intent('volume_set',20)); c.state['volume']=(70,70)
    assert '未覆盖' in c.execute(Intent('undo'))
    assert c.state['volume']==(70,70)

def test_repeated_mute_is_idempotent(controller):
    c=controller; c.execute(Intent('mute',True)); c.execute(Intent('mute',True))
    assert c.state['mute'] is True
    c.execute(Intent('mute',False)); assert c.state['mute'] is False

def test_bounds_and_no_black_screen(controller):
    c=controller; c.execute(Intent('brightness_set',0)); assert c.state['brightness']==(50,1000)
    c.execute(Intent('volume_delta',100)); assert c.state['volume']==(100,100)
    before=list(c.writes); assert '操作失败' in c.execute(Intent('volume_set',999)); assert c.writes==before

def test_timeout_no_success_no_undo(controller):
    c=controller; c._write=Mock(side_effect=subprocess.TimeoutExpired('pactl',5))
    assert c.execute(Intent('volume_set',30)).startswith('操作失败')
    assert c.undo_state is None

def test_context_expires(controller):
    c=controller; c.execute(Intent('volume_set',30)); c.last_at-=121
    assert c.parse('再小一点')==Intent('clarify')

def test_simple_commands_do_not_need_model(controller):
    session=AssistantSession(); session.desktop=controller
    with patch('llm_core.session.AgentLoop') as agent:
        events=list(session.stream('取消静音')); agent.assert_not_called()
    assert any(e.get('content')=='已取消静音。' for e in events)

def test_session_keeps_bounded_conversation():
    recognizer=Mock()
    recognizer.classify.return_value=Intent('chat')
    session=AssistantSession(intent_recognizer=recognizer)
    with patch('llm_core.session.AgentLoop') as agent:
        agent.return_value.run_stream.return_value=[{'type':'text','content':'你好'}]
        for i in range(9): list(session.stream('你好'+str(i)))
    assert len(session.history)==12
    assert session.history[-1]=={'role':'assistant','content':'你好'}

def test_model_intent_is_structured_and_validated():
    client=Mock()
    client.complete_json.side_effect=[
        {'kind':'local_action','confidence':.96},
        {'action':'volume_delta','value':'5','command':'','path':'',
         'content':'','confidence':.96},
    ]
    recognizer=ModelIntentRecognizer(client)
    assert recognizer.classify('这个视频声音有点小')==Intent('volume_delta',5)
    assert client.complete_json.call_count==2

def test_model_routes_local_inspection_to_tool_without_phrase_whitelist():
    client=Mock()
    client.complete_json.side_effect=[
        {'kind':'local_action','confidence':.97},
        {'action':'execute_command','value':'',
         'command':'ps -eo pid,comm,rss --sort=-rss | head',
         'path':'','content':'','confidence':.97},
    ]
    recognizer=ModelIntentRecognizer(client)
    assert recognizer.classify('帮我找出现在最吃资源的东西')==Intent(
        'tool',('execute_command',{'command':'ps -eo pid,comm,rss --sort=-rss | head'}))

def test_model_routes_how_to_question_to_chat():
    client=Mock()
    client.complete_json.return_value={'kind':'chat','confidence':.98}
    recognizer=ModelIntentRecognizer(client)
    assert recognizer.classify('怎么用命令查看内存最大的进程')==Intent('chat')
    client.complete_json.assert_called_once()

def test_session_forces_model_selected_tool():
    recognizer=Mock()
    routed=('execute_command',{'command':'ps -eo pid,comm,rss --sort=-rss | head'})
    recognizer.classify.return_value=Intent('tool',routed)
    session=AssistantSession(intent_recognizer=recognizer)
    with patch('llm_core.session.execute_tool',return_value='真实结果') as execute:
        events=list(session.stream('检查当前哪个程序最占内存'))
    execute.assert_called_once_with(*routed)
    assert any(e.get('content')=='真实结果' for e in events)

def test_invalid_model_intent_never_executes():
    recognizer=ModelIntentRecognizer(Mock())
    assert recognizer._validate({'action':'volume_set','value':999,'confidence':.99})==Intent('clarify')
    assert recognizer._validate({'action':'app','value':'任意命令','confidence':.99})==Intent('clarify')
    assert looks_like_desktop_command('这个视频太吵了')
