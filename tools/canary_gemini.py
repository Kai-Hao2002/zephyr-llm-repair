# tools/canary_gemini.py — one tiny generate call per key; exit 0 if any key answers 200, 3 if all 503/429.
# Used before a free-tier batch so that 503 "high demand" retries do not burn the daily quota.
import os
import sys
from dotenv import dotenv_values
from google import genai

model = sys.argv[1] if len(sys.argv) > 1 else "gemini-3.8-flash"
env = dotenv_values(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
for name in ("GEMINI_API_KEY", "GEMINI_API_KEY2"):
    if not env.get(name):
        continue
    try:
        client = genai.Client(api_key=env[name])  # keep a reference; an inline client gets closed before the call
        client.models.generate_content(model=model, contents="Reply OK.")
        print(f"{name}: ok")
        sys.exit(0)
    except Exception as e:
        print(f"{name}: {type(e).__name__} {str(e)[:80]}")
sys.exit(3)
