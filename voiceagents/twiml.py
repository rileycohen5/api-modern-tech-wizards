import azure.functions as func
from .llm import ask_llm

# simple in-memory store per CallSid
CALL_MEMORY = {}

def handle_twiml(req: func.HttpRequest) -> func.HttpResponse:
    base_url = req.url.replace("/api/voiceagents/twiml", "")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Hey, this is your AI assistant. How can I help you today?</Say>
    <Gather input="speech" action="{base_url}/api/voiceagents/handle-input" method="POST" timeout="5">
        <Say>If you can, please briefly describe what you need help with.</Say>
    </Gather>
    <Say>I didn’t catch anything, so I’m going to hang up. Feel free to call back.</Say>
    <Hangup/>
</Response>
"""
    return func.HttpResponse(xml, mimetype="application/xml")


def handle_input(req: func.HttpRequest) -> func.HttpResponse:
    form = req.form
    user_text = form.get("SpeechResult", "")
    call_sid = form.get("CallSid", "unknown")

    # history
    history = CALL_MEMORY.get(call_sid, [])

    # get AI reply
    ai_reply = ask_llm(user_text, history)

    # store conversation
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": ai_reply})
    CALL_MEMORY[call_sid] = history

    # generate base URL again
    base_url = req.url.replace("/api/voiceagents/handle-input", "")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">{ai_reply}</Say>
    <Gather input="speech" action="{base_url}/api/voiceagents/handle-input" method="POST" timeout="5">
        <Say>What else can I help with?</Say>
    </Gather>
</Response>
"""
    return func.HttpResponse(xml, mimetype="application/xml")
