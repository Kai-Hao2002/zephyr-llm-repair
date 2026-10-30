import os, sys, time
sys.path.insert(0, os.path.expanduser("~/zephyr-llm-repair"))
from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/zephyr-llm-repair/.env"))
os.environ["ZEPHYR_GEMINI_MODEL"] = "gemini-4-argon"
from core.llm_provider import get_chat_model
for temp in (0, 0.1, 0.8):
    llm = get_chat_model(role="pro", temperature=temp, timeout=120)
    t = time.time()
    try:
        r = llm.invoke("In one short sentence: what does `k_sleep` do in Zephyr?")
        print("temp", temp, "OK", round(time.time()-t,1), "s | content type", type(r.content).__name__, "| .text:", repr(str(r.text)[:90]), "| usage", r.usage_metadata)
    except Exception as e:
        print("temp", temp, "ERR", type(e).__name__, str(e)[:400])
