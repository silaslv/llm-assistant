"""Constrained model routing for conversation and local operations."""
import re

from llm_core.client import LLMClient, LLMError, ServerNotRunningError
from llm_core.desktop import APPS, Intent


_COMMAND_HINT = re.compile(
    r'音量|声音|静音|亮度|屏幕|播放|暂停|上一首|下一首|浏览器|火狐|文件管理|'
    r'终端|计算器|vscode|撤销|调高|调低|调大|调小|增加|减少|提高|降低|'
    r'响|吵|大声|小声|亮一点|暗一点|打开|启动|关掉'
)


def looks_like_desktop_command(text):
    """Broad, side-effect-free gate; semantic classification happens in the model."""
    compact=re.sub(r'\s+','',str(text))
    return len(compact)<=60 and bool(_COMMAND_HINT.search(compact.lower()))


_INTENT_TOOL = {
    'type':'function',
    'function':{
        'name':'classify_request',
        'description':'识别请求应直接聊天、执行桌面操作，还是调用本机工具；本工具不执行操作。',
        'parameters':{
            'type':'object',
            'properties':{
                'action':{
                    'type':'string',
                    'enum':['none','clarify','cancel','undo','volume_set','volume_delta',
                            'brightness_set','brightness_delta','mute','media','app',
                            'execute_command','read_file','write_file','list_directory'],
                },
                'value':{
                    'description':'字符串形式的百分比、增减量、布尔值、媒体动作或应用名；未使用时为空。',
                    'type':'string',
                },
                'command':{'type':'string','description':'execute_command 的命令，否则为空字符串。'},
                'path':{'type':'string','description':'文件或目录路径，否则为空字符串。'},
                'content':{'type':'string','description':'write_file 的内容，否则为空字符串。'},
                'confidence':{'type':'number','minimum':0,'maximum':1},
            },
            'required':['action','value','command','path','content','confidence'],
            'additionalProperties':False,
        },
    },
}

_KIND_SCHEMA = {
    'type':'object',
    'properties':{
        'kind':{'type':'string','enum':['chat','local_action']},
        'confidence':{'type':'number'},
    },
    'required':['kind','confidence'],
    'additionalProperties':False,
}

_KIND_PROMPT = """判断用户是要求助手现在操作/检查这台电脑，还是只聊天、问知识或问做法。
示例：
“检查当前哪些程序最占内存” => local_action
“帮我看看现在谁最耗内存” => local_action
“声音调小一点” => local_action
“这个视频声音有点小” => local_action
“打开浏览器” => local_action
“怎么检查哪些程序最占内存” => chat
“用什么命令查看内存” => chat
“什么是进程内存” => chat
“nihao” => chat
注意：怎么、如何、用什么命令是在问方法，不是要求现在执行。
用户：{text}
JSON："""

_ACTION_PROMPT = """用户已经明确要求现在操作或检查这台 Linux 电脑。选择动作并生成参数。
检查系统时间、磁盘、内存、进程、网络等使用 execute_command，command 必须是真实可运行的安全 shell 命令。
文件读取用 read_file；写文件用 write_file；列目录用 list_directory。音量、亮度、媒体和打开应用使用对应专用动作。
音量或亮度的“一点”默认增减 5；绝对百分比使用 *_set，相对变化使用 *_delta。
media 的 value 只能是 play、pause、next、previous；app 的 value 必须来自允许的应用名。
未使用的字符串字段填空；value 中的数值和布尔值也写成字符串。
示例：“检查哪些程序最占内存” => action=execute_command, command=ps -eo pid,comm,rss,%mem --sort=-rss | head -n 11
示例：“声音调小一点” => action=volume_delta, value=-5
示例：“列出 /tmp” => action=list_directory, path=/tmp
用户：{text}
最近调节目标：{last_target}
允许的应用名：{apps}
JSON："""


class ModelIntentRecognizer:
    def __init__(self,client=None,confidence_threshold=.80):
        self.client=client or LLMClient()
        self.confidence_threshold=confidence_threshold

    def classify(self,text,last_target=None):
        apps='、'.join(APPS)
        try:
            kind=self.client.complete_json(
                _KIND_PROMPT.format(text=text),_KIND_SCHEMA,max_tokens=64,
            )
            confidence=float(kind.get('confidence',0))
            if kind.get('kind')=='chat' and confidence>=self.confidence_threshold:
                return Intent('chat')
            if kind.get('kind')!='local_action' or confidence<self.confidence_threshold:
                return Intent('clarify')
            response=self.client.complete_json(
                _ACTION_PROMPT.format(
                    text=text,last_target=last_target or '无',apps=apps
                ),
                _INTENT_TOOL['function']['parameters'],max_tokens=256,
            )
        except (LLMError,ServerNotRunningError,TypeError,ValueError):
            return Intent('clarify')
        return self._validate(response)

    def _validate(self,args):
        if not isinstance(args,dict): return Intent('clarify')
        action=args.get('action')
        try: confidence=float(args.get('confidence',0))
        except (TypeError,ValueError): return Intent('clarify')
        if not 0<=confidence<=1 or confidence<self.confidence_threshold:
            return Intent('clarify')
        if action=='none': return Intent('chat')
        if action in ('clarify','cancel','undo'): return Intent(action)
        if action=='execute_command':
            command=args.get('command')
            if isinstance(command,str) and 0<len(command.strip())<=1000:
                return Intent('tool',(action,{'command':command.strip()}))
            return Intent('clarify')
        if action in ('read_file','list_directory'):
            path=args.get('path')
            if isinstance(path,str) and 0<len(path.strip())<=1000:
                return Intent('tool',(action,{'path':path.strip()}))
            return Intent('clarify')
        if action=='write_file':
            path=args.get('path'); content=args.get('content')
            if isinstance(path,str) and path.strip() and isinstance(content,str):
                return Intent('tool',(action,{'path':path.strip(),'content':content}))
            return Intent('clarify')
        value=args.get('value')
        if action in ('volume_set','volume_delta','brightness_set','brightness_delta'):
            if isinstance(value,bool): return Intent('clarify')
            try: value=int(value)
            except (TypeError,ValueError): return Intent('clarify')
            if action.endswith('_set') and not 0<=value<=100: return Intent('clarify')
            if action.endswith('_delta') and (value==0 or not -100<=value<=100): return Intent('clarify')
            return Intent(action,value)
        if action=='mute' and value in ('true','false'): return Intent(action,value=='true')
        if action=='media' and value in ('play','pause','next','previous'): return Intent(action,value)
        if action=='app' and isinstance(value,str):
            value=value.lower()
            if value in APPS: return Intent(action,value)
        return Intent('clarify')
