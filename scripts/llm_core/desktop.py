"""Conservative local intent routing and checked desktop actions. No shell parsing."""
from dataclasses import dataclass
import re
import shutil
import subprocess
import time
from llm_core.security import get_auditor

@dataclass(frozen=True)
class Intent:
    action: str
    value: object = None

NUM = r'(?:\d{1,3}|[零〇一二两三四五六七八九十百]{1,5})'
APPS = {
    '浏览器': ('gtk-launch', 'google-chrome.desktop'),
    'chrome': ('google-chrome-stable',), '谷歌浏览器': ('google-chrome-stable',),
    '火狐': ('firefox',), 'fire狐': ('firefox',), 'firefox': ('firefox',),
    '文件管理器': ('dolphin',), '文件夹': ('dolphin',),
    '终端': ('konsole',), '计算器': ('kcalc',),
    'vscode': ('code',), '代码编辑器': ('code',),
}

def number(s):
    if s.isdigit():
        return int(s)
    digits = dict(zip('零〇一二两三四五六七八九', [0,0,1,2,2,3,4,5,6,7,8,9]))
    if s in ('一百','百'):
        return 100
    if '百' in s:
        return None
    if '十' in s:
        if s.count('十') != 1:
            return None
        a,b=s.split('十')
        if (a and a not in digits) or (b and b not in digits):
            return None
        return (digits.get(a,1)*10)+digits.get(b,0)
    return digits.get(s)

def parse_intent(text, last_target=None):
    s = re.sub(r'[\s，。！？、,.!?]', '', text).lower()
    s = re.sub(r'^(?:小希|小西|小溪|小曦)', '', s)
    s = re.sub(r'^(?:请|麻烦你|帮我|给我)', '', s)
    s = re.sub(r'[吧呀啊]$', '', s)
    if s in ('取消','算了','停止','不要执行'):
        return Intent('cancel')
    if s in ('撤销','撤销上次操作','撤销上次调节','撤销刚才操作','恢复刚才的设置'):
        return Intent('undo')
    # Never treat a negated instruction as an affirmative desktop action.
    if re.match(r'^(?:不要|别|不用|不必)', s):
        return Intent('cancel')
    if s in ('静音','设为静音','开启静音','把声音关掉','关闭声音'):
        return Intent('mute', True)
    if s in ('取消静音','解除静音','关闭静音','恢复声音','打开声音'):
        return Intent('mute', False)
    media = {'暂停':'pause','暂停播放':'pause','继续播放':'play','恢复播放':'play',
             '播放音乐':'play','下一首':'next','上一首':'previous'}
    if s in media:
        return Intent('media', media[s])
    m = re.fullmatch(r'(?:打开|启动)(.+)',s)
    if m and m[1] in APPS:
        return Intent('app', m[1])
    # Require a complete, single utterance, so explanations/compound requests cannot match.
    m = re.fullmatch(r'(?:把)?(音量|声音|亮度|屏幕亮度)(?:调到|设为|设置为|调成|调整到)(?:百分之)?('+NUM+r')(?:%|百分比)?',s)
    if m:
        n=number(m[2]); target='volume' if m[1] in ('音量','声音') else 'brightness'
        return Intent(target+'_set',n) if n is not None else None
    if re.fullmatch(r'(?:把)?(?:亮度|屏幕|屏幕亮度)?(?:调到|设为)?(?:最亮|最高|最大)',s):
        return Intent('brightness_set',100)
    if re.fullmatch(r'(?:把)?(?:亮度|屏幕|屏幕亮度)?(?:调到|设为)?(?:最暗|最低|最小)',s):
        return Intent('brightness_set',5)
    s = re.sub(r'^把','',s)
    m = re.fullmatch(r'(音量|声音|亮度|屏幕亮度|屏幕)?(?:再)?(?:调|调节|调整|降|减|增|提)?(高|大|响|亮|加|低|小|轻|暗|少)(?:一点|点)?(?:百分之)?('+NUM+r')?(?:%)?',s)
    if m:
        noun,direction,amount=m.groups()
        target = ('volume' if noun in ('音量','声音') else 'brightness') if noun else (('brightness' if direction in ('亮','暗') else last_target))
        if target is None and direction in ('响','轻'):
            target='volume'
        if target not in ('volume','brightness'):
            return Intent('clarify')
        n=number(amount) if amount else 5
        if n is None:
            return None
        return Intent(target+'_delta', n if direction in ('高','大','响','亮','加') else -n)
    return None

class DesktopController:
    def __init__(self):
        self.last_target=None
        self.last_at=0
        self.undo_state=None

    def recent_target(self):
        return self.last_target if time.monotonic()-self.last_at < 120 else None

    def parse(self,text):
        return parse_intent(text,self.recent_target())

    def _run(self,args):
        p=subprocess.run(args,capture_output=True,text=True,timeout=5)
        if p.returncode:
            raise RuntimeError((p.stderr or p.stdout or '系统命令失败').strip()[:180])
        return p.stdout.strip()

    def _read(self,target):
        if target=='volume':
            out=self._run(['pactl','get-sink-volume','@DEFAULT_SINK@'])
            matches=re.findall(r'(\d+)%',out)
            if not matches:
                raise RuntimeError('无法读取当前音量')
            # Preserve individual channels when undoing stereo balance.
            return tuple(int(v) for v in matches)
        if target=='mute':
            out=self._run(['pactl','get-sink-mute','@DEFAULT_SINK@'])
            if out.endswith('yes'): return True
            if out.endswith('no'): return False
            raise RuntimeError('无法读取静音状态')
        raw=int(self._run(['brightnessctl','get']))
        maximum=int(self._run(['brightnessctl','max']))
        if maximum<=0: raise RuntimeError('无法读取屏幕亮度')
        return (raw,maximum)

    def _write(self,target,value,raw=False):
        if target=='volume':
            values=value if isinstance(value,tuple) else (value,)
            self._run(['pactl','set-sink-volume','@DEFAULT_SINK@',*[f'{v}%' for v in values]])
        elif target=='mute':
            self._run(['pactl','set-sink-mute','@DEFAULT_SINK@','1' if value else '0'])
        else:
            self._run(['brightnessctl','set',str(value[0]) if raw else f'{value}%'])

    def _media(self,action):
        import dbus
        bus=dbus.SessionBus()
        players=sorted(n for n in bus.list_names() if n.startswith('org.mpris.MediaPlayer2.'))
        if not players: raise RuntimeError('没有可控制的播放器，请先打开音乐或视频')
        if len(players)>1:
            playing=[n for n in players if str(dbus.Interface(bus.get_object(n,'/org/mpris/MediaPlayer2'),'org.freedesktop.DBus.Properties').Get('org.mpris.MediaPlayer2.Player','PlaybackStatus'))=='Playing']
            if len(playing)!=1: raise RuntimeError('有多个播放器，请先只保留一个正在播放的应用')
            players=playing
        obj=dbus.Interface(bus.get_object(players[0],'/org/mpris/MediaPlayer2'),'org.mpris.MediaPlayer2.Player')
        getattr(obj,{'play':'Play','pause':'Pause','next':'Next','previous':'Previous'}[action])(timeout=5)
        return {'play':'已继续播放','pause':'已暂停播放','next':'已切换下一首','previous':'已切换上一首'}[action]

    def execute(self,intent):
        try:
            result=self._execute(intent)
            get_auditor().log('desktop_control',{'action':intent.action,'value':intent.value},result)
            return result
        except Exception as e:
            message=f'操作失败：{e}'
            get_auditor().log('desktop_control',{'action':intent.action,'value':intent.value},message,success=False)
            return message

    def _execute(self,i):
        if i.action=='clarify': return '我不确定具体操作，请明确说明要调节音量、亮度或控制哪个应用。'
        if i.action=='cancel': return '已取消，没有执行操作。'
        if i.action=='undo':
            if not self.undo_state: return '没有可以撤销的音量或亮度调节。'
            target,before,after=self.undo_state
            if self._read(target)!=after:
                return '系统设置已被其他操作改变，未覆盖当前设置。'
            self._write(target,before,raw=True)
            self.undo_state=None
            return '已恢复上次调节前的设置。'
        if i.action=='app':
            args=APPS[i.value]
            if not shutil.which(args[0]): raise RuntimeError(f'没有找到应用：{i.value}')
            subprocess.Popen(args,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
            return f'已请求打开{i.value}。'
        if i.action=='media': return self._media(i.value)
        target=i.action.split('_')[0]
        if target not in ('volume','brightness','mute'): raise ValueError('不支持的动作')
        if target!='mute' and (type(i.value) is not int or not -100<=i.value<=100):
            raise ValueError('百分比需要在 0 到 100 之间')
        before=self._read(target)
        if target=='mute':
            value=bool(i.value)
        else:
            current=max(before) if target=='volume' else round(before[0]*100/before[1])
            value=current+i.value if i.action.endswith('_delta') else i.value
            value=max(0 if target=='volume' else 5,min(100,value))
        self._write(target,value)
        after=self._read(target)
        self.undo_state=(target,before,after)
        if target in ('volume','brightness'):
            self.last_target=target; self.last_at=time.monotonic()
        if target=='mute': return '已静音。' if after else '已取消静音。'
        actual=max(after) if target=='volume' else round(after[0]*100/after[1])
        return f'{"音量" if target=="volume" else "亮度"}已设为 {actual}%。'
