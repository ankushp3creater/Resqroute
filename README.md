# ResQRoute — evidence-bound, climate-aware route risk assessment

**One-line pitch:** ResQRoute compares real road alternatives against official district-level weather evidence using
deterministic rules, then lets a bounded Gemini agent investigate through validated read-only tools and explain —
with every claim traceable to evidence. *Unknown is never reported as safe.*

## Honest current state
| Component | State |
|---|---|
| Geocoding (Nominatim) + routing (OSRM) | Real public services, no key (demo-grade; respect usage policies) |
| Haryana boundaries (22/23 districts) | Real file, validated at startup; Hansi missing → flagged everywhere |
| District matching, risk engine, comparison | Deterministic, unit-tested |
| **IMD weather** | **Not connected** – endpoints/access unverified. Weather = *unavailable*, risk = *unknown*. Extension point ready |
| Gemini agent | Implemented; needs `GEMINI_API_KEY`; **live Gemini calls not yet verified** |

## Run (Windows 11 PowerShell, Python 3.13, Node)
```powershell
# 1. backend
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend\requirements-dev.txt
copy .env.example .env          # then edit .env (add GEMINI_API_KEY when you have it)
python -m backend.tests.run_lite  # stdlib test runner (works without pytest)
pytest                            # full suite incl. HTTP tests
uvicorn backend.main:app --port 8000 --reload

# 2. frontend (second terminal)
cd frontend
npm install
npm run dev                       # http://localhost:5173
npm run build                     # production build -> frontend/dist (then served by uvicorn at :8000)
```
Set `GEOCODER_USER_AGENT` in `.env` to include your contact email (Nominatim policy).
Check the model name in `GEMINI_MODEL` against your Gemini account.

## Connecting IMD later (no other code changes)
See `backend/services/weather/imd/client.py`: set `IMD_API_BASE_URL`, fill `severity_maps.py` and
`district_crosswalk.py` from **official** IMD docs only, write a parser, register an `ImdFeed`.

## Architecture
```
Browser (Vite+Leaflet) → FastAPI api/ → services/{routing,geography,weather,risk,ai}
                                         deterministic: validation, matching, risk, comparison
                                         LLM only: services/ai (explains; cannot change risk levels)
```
Agent: Gemini function-calling, 4 read-only tools, max 6 tool steps, 1 self-correction retry, output validated
(route IDs, evidence refs, unsupported safety claims, no weather recommendation unless the backend says the
comparison basis is `weather`).

## Contract additions beyond specs.md (documented)
`Idempotency-Key` header; error codes `GEOCODER_*`, `ROUTING_TIMEOUT`, `ASSESSMENT_FAILED`, `AI_ANALYSIS_IN_PROGRESS`;
assessment fields `comparison`, `route_summaries`, `evidence_index`, `limitation_index`, `trace`;
AI fields `analysis_basis`, `geographic_comparison`, `geographic_preference_route_id`, `reason` (alias).
Past-departure check has a 5-minute grace period.
