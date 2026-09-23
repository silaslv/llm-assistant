"""One session for typed and spoken input, including short desktop follow-ups."""
from llm_core.agent import AgentLoop
from llm_core.desktop import DesktopController
from llm_core.intent import ModelIntentRecognizer, looks_like_desktop_command

class AssistantSession:
    def __init__(self, intent_recognizer=None):
        self.desktop=DesktopController()
        self.intent_recognizer=intent_recognizer or ModelIntentRecognizer()
        self.history=[]

    def stream(self,query):
        intent=self.desktop.parse(query)
        if intent is None and looks_like_desktop_command(query):
            classified=self.intent_recognizer.classify(query,self.desktop.recent_target())
            if classified is not None and classified.action!='chat': intent=classified
        answer=''
        if intent:
            answer=self.desktop.execute(intent)
            yield {'type':'text','content':answer}
        else:
            for event in AgentLoop().run_stream(query,history=self.history):
                if event.get('type')=='text': answer+=event.get('content','')
                yield event
        if answer:
            self.history.extend([{'role':'user','content':query},{'role':'assistant','content':answer[:6000]}])
            self.history=self.history[-12:]
        yield {'type':'done'}
