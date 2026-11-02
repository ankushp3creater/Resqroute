import time
from backend.config import Settings
from backend.http import HttpClient

s = Settings.from_env()
body = {"contents": [{"role": "user", "parts": [{"text": "Reply with the single word: ok"}]}]}
t = time.time()
status, data = HttpClient().request_json(
    "POST", f"{s.gemini_base_url}/models/{s.gemini_model}:generateContent",
    headers={"x-goog-api-key": s.gemini_api_key}, json_body=body, timeout=170)
print("MODEL:", s.gemini_model, "| STATUS:", status, "| SECONDS:", round(time.time() - t, 1))