"""Constrained model fallback for desktop intent recognition."""
import json
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
        'name':'classify_desktop_intent',
        'description':'只识别用户是否要求执行桌面操作，不执行操作。',
        'parameters':{
            'type':'object',
            'properties':{
                'action':{
                    'type':'string',
                    'enum':['none','clarify','cancel','undo','volume_set','volume_delta',
                            'brightness_set','brightness_delta','mute','media','app'],
                },
                'value':{
                    'description':'百分比、增减量、布尔值、媒体动作或白名单应用名。',
                },
                'confidence':{'type':'number','minimum':0,'maximum':1},
            },
            'required':['action','confidence'],
            'additionalProperties':False,
        },
    },
}

_SYSTEM_PROMPT = """你是桌面操作意图分类器，只能调用 classify_desktop_intent，不要回答用户。
规则：
1. 只分类用户明确要求执行的操作；询问、解释、假设、教程归为 none。
2. 否定、取消操作归为 cancel，不能把否定句当成肯定操作。
3. 音量或亮度的“稍微、一点”默认增减 5；绝对百分比使用 *_set，相对变化使用 *_delta。
4. 缺少必要目标、包含多个冲突操作或无法确定时归为 clarify。
5. media 的 value 只能是 play、pause、next、previous；app 的 value 必须是给定白名单名称。
6. 不得声称已经执行，不能输出 shell 命令。"""


class ModelIntentRecognizer:
    def __init__(self,client=None,confidence_threshold=.80):
        self.client=client or LLMClient()
        self.confidence_threshold=confidence_threshold

    def classify(self,text,last_target=None):
        apps='、'.join(APPS)
        user=(f'用户原话：{text}\n最近调节目标：{last_target or "无"}\n'
              f'允许的应用名：{apps}')
        choice={'type':'function','function':{'name':'classify_desktop_intent'}}
        try:
            response=self.client.chat(
                [{'role':'system','content':_SYSTEM_PROMPT},{'role':'user','content':user}],
                tools=[_INTENT_TOOL],temperature=0,max_tokens=160,tool_choice=choice,
            )
        except (LLMError,ServerNotRunningError):
            return Intent('clarify')
        return self._parse_response(response)

    def _parse_response(self,response):
        try:
            message=response['choices'][0]['message']
        except (KeyError,IndexError,TypeError):
            return Intent('clarify')
        args=None
        calls=message.get('tool_calls') or []
        for call in calls:
            function=call.get('function') or {}
            if function.get('name')=='classify_desktop_intent':
                args=function.get('arguments'); break
        if args is None:
            args=message.get('content')
        if isinstance(args,str):
            try: args=json.loads(args)
            except (ValueError,TypeError): return Intent('clarify')
        return self._validate(args)

    def _validate(self,args):
        if not isinstance(args,dict): return Intent('clarify')
        action=args.get('action')
        try: confidence=float(args.get('confidence',0))
        except (TypeError,ValueError): return Intent('clarify')
        if not 0<=confidence<=1 or confidence<self.confidence_threshold:
            return Intent('clarify')
        if action=='none': return Intent('chat')
        if action in ('clarify','cancel','undo'): return Intent(action)
        value=args.get('value')
        if action in ('volume_set','volume_delta','brightness_set','brightness_delta'):
            if isinstance(value,bool): return Intent('clarify')
            try: value=int(value)
            except (TypeError,ValueError): return Intent('clarify')
            if action.endswith('_set') and not 0<=value<=100: return Intent('clarify')
            if action.endswith('_delta') and (value==0 or not -100<=value<=100): return Intent('clarify')
            return Intent(action,value)
        if action=='mute' and isinstance(value,bool): return Intent(action,value)
        if action=='media' and value in ('play','pause','next','previous'): return Intent(action,value)
        if action=='app' and isinstance(value,str):
            value=value.lower()
            if value in APPS: return Intent(action,value)
        return Intent('clarify')
