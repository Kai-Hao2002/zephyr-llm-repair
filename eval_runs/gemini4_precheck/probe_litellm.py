import os, json, time
from dotenv import dotenv_values
import litellm
env = dotenv_values(os.path.expanduser("~/zephyr-llm-repair/.env"))
key = env["GEMINI_API_KEY"]
print("litellm", litellm.__version__ if hasattr(litellm,'__version__') else '', "knows model:", "gemini/gemini-4-argon" in litellm.model_cost)
tools=[{"type":"function","function":{"name":"run_cmd","description":"Run a shell command","parameters":{"type":"object","properties":{"cmd":{"type":"string"}},"required":["cmd"]}}}]
msgs=[{"role":"user","content":"Use run_cmd to run `ls`, then tell me the result in a few words."}]
t=time.time()
r=litellm.completion(model="gemini/gemini-4-argon",api_key=key,messages=msgs,tools=tools,temperature=0.0,timeout=120)
m=r.choices[0].message
print("turn1", round(time.time()-t,1),"s tool_calls:",[ (c.function.name,c.function.arguments) for c in (m.tool_calls or [])], "usage", r.usage.model_dump() if r.usage else None)
if m.tool_calls:
    psf = getattr(m.tool_calls[0],'provider_specific_fields',None)
    print("thought sig present:", bool(psf and psf.get('thought_signature')))
    msgs.append(m.model_dump(exclude_none=True))
    msgs.append({"role":"tool","tool_call_id":m.tool_calls[0].id,"content":"main.c prj.conf"})
    t=time.time()
    r=litellm.completion(model="gemini/gemini-4-argon",api_key=key,messages=msgs,tools=tools,temperature=0.0,timeout=120)
    print("turn2", round(time.time()-t,1),"s:",repr(r.choices[0].message.content)[:120],"usage",r.usage.model_dump() if r.usage else None)
