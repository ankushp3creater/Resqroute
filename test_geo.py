import urllib.request as u, urllib.error as e
req = u.Request(
    "https://nominatim.openstreetmap.org/search?q=Ambala&format=jsonv2&limit=1&countrycodes=in",
    headers={"User-Agent": "ResQRoute-Hackathon/0.1 (test)"},
)
try:
    r = u.urlopen(req, timeout=15)
    print("STATUS", r.status)
    print(r.read(300))
except e.HTTPError as x:
    print("HTTP ERROR", x.code)
    print(x.read(300))
except Exception as x:
    print("OTHER ERROR", repr(x))