import os, time
from dotenv import dotenv_values
from google import genai
env = dotenv_values(os.path.expanduser("~/zephyr-llm-repair/.env"))
c = genai.Client(api_key=env["GEMINI_API_KEY"])
m = c.models.get(model="models/gemini-4-argon")
print({k: getattr(m, k, None) for k in ("name","display_name","description","version","thinking","temperature","max_temperature","top_p","top_k")})
for model in ("gemini-4-argon", "gemini-3.8-flash"):
    t = time.time()
    try:
        r = c.models.generate_content(model=model, contents="Reply OK.")
        u = r.usage_metadata
        print(model, "OK", round(time.time()-t,1), "s | resp model:", r.model_version, "| text:", repr(str(r.text)[:30]),
              "| prompt", u.prompt_token_count, "out", u.candidates_token_count, "thoughts", u.thoughts_token_count, "tier:", getattr(u,"traffic_type",None))
    except Exception as e:
        print(model, "ERR", type(e).__name__, str(e)[:500])
t = time.time()
try:
    r = c.models.embed_content(model="models/gemini-embedding-2", contents="zephyr kconfig")
    print("embedding-2 OK", round(time.time()-t,1), "s dim", len(r.embeddings[0].values))
except Exception as e:
    print("embedding ERR", type(e).__name__, str(e)[:500])
