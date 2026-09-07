import json
import math
import random
import urllib.request
from datetime import UTC, datetime, timedelta

BASE = "http://localhost:8000"


def call(path, body=None, token=None, method="POST"):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read() or b"null")


tok = call(
    "/auth/signup", {"email": "operator@example.com", "password": "a-good-password"}
)["access_token"]
print("signed up operator@example.com")

specs = [
    ("Pump-3", "flow", "L/min", 50, 10.0, 80.0),
    ("Furnace-1", "temperature", "°C", 25, 5.0, 40.0),
    ("Line-Press", "pressure", "kPa", 100, None, None),
]
now = datetime.now(UTC)
random.seed(7)
for name, dtype, unit, baseline, lo, hi in specs:
    dev = call(
        "/devices",
        {
            "name": name,
            "type": dtype,
            "unit": unit,
            "min_threshold": lo,
            "max_threshold": hi,
        },
        tok,
    )
    rows = []
    for m in range(180, 0, -1):
        t = now - timedelta(minutes=m)
        val = (
            baseline
            + baseline * 0.15 * math.sin(2 * math.pi * m / 60)
            + random.gauss(0, baseline * 0.04)
        )
        rand = random.random()
        if rand < 0.02:
            val *= 2.0  # occasional spike -> alert
        elif rand > 0.02 and rand < 0.04:
            val -= 50
        rows.append({"value": round(val, 2), "time": t.isoformat()})
    n = call(f"/devices/{dev['id']}/readings/bulk", rows, tok)["count"]
    print(f"  {name}: {n} readings")

# now run the frontend and login as operator@example.com to view seed data
