cd C:\Users\Ankush\Downloads\resqroute\resqroute
@'
from backend.config import Settings
from backend.http import HttpClient

s = Settings.from_env()
if not s.gemini_api_key:
    raise SystemExit("GEMINI_API_KEY is empty in .env")
status, data = HttpClient().request_json(
    "GET", s.gemini_base_url + "/models",
    params={"pageSize": 200}, headers={"x-goog-api-key": s.gemini_api_key}, timeout=20)
print("STATUS:", status)
for m in (data or {}).get("models", []):
    if "generateContent" in m.get("supportedGenerationMethods", []):
        print(m["name"].replace("models/", ""))
'@ | Set-Content -Encoding utf8 list_models.py