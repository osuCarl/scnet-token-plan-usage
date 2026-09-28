"""Offline test for the scnet-usage plugin backend.

Loads dashboard/plugin_api.py exactly the way the web server does (importlib
via spec_from_file_location + sys.modules registration), mounts the router on
a fresh FastAPI app under the real prefix /api/plugins/scnet-usage, and drives
it with TestClient against the LIVE state.db (read-only).

Checks:
1. GET /usage returns cycle bounds, per-model totals, and a daily series
   whose day strings all fall inside the cycle and sum back to totals.credits.
2. Daily rows carry input/cached/output token buckets AND their credits
   splits, and per-day sum(input_cr+cached_cr+output_cr) ≈ credits (the
   same multiplier is applied bucket-wise, so they must match exactly up
   to rounding).
3. today_credits equals the daily row for today (when present).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
PLUGIN_API = HERE.parent / "dashboard" / "plugin_api.py"
sys.path.insert(0, str(HERE.parent))  # hermes_constants lives in the repo? no — installed tree

# hermes_constants comes from the installed Hermes tree; make sure it resolves
_HERMES = Path(r"C:\Users\j6056\AppData\Local\hermes\hermes-agent")
if str(_HERMES) not in sys.path:
    sys.path.insert(0, str(_HERMES))

spec = importlib.util.spec_from_file_location("hermes_dashboard_plugin_scnet_usage", PLUGIN_API)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)

app = FastAPI()
app.include_router(mod.router, prefix="/api/plugins/scnet-usage")
client = TestClient(app)

failures = []


def check(name: str, cond: bool, detail: str = "") -> None:
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        failures.append(name)


resp = client.get("/api/plugins/scnet-usage/usage")
check("GET /usage returns 200", resp.status_code == 200, f"status={resp.status_code} body={resp.text[:300]}")
if resp.status_code != 200:
    print(json.dumps(resp.json() if resp.headers.get("content-type", "").startswith("application/json") else resp.text, indent=2, ensure_ascii=False))
    sys.exit(1)

data = resp.json()
cycle = data["cycle"]
print(f"\ncycle: {cycle['start']} → {cycle['end']} (end INCLUSIVE — expiry day is the cycle's last day)")
print(f"totals.credits = {data['totals']['credits']}, today = {data['today_credits']}")
print(f"models: {[m['model'] for m in data['models']]}")
print(f"daily rows: {len(data['daily'])}, cycle_days: {len(data.get('cycle_days', []))}")

# 0. REGRESSION (2026-09-28 bug): the expiry/anchor day itself must still
# belong to the CURRENT cycle — the plugin used to flip to a new cycle at
# 00:00 of the expiry day. Today must satisfy start <= today <= end.
today_str = datetime.now().strftime("%Y-%m-%d")
check("today is inside the current cycle (expiry day = last day, not day 1 of a new cycle)",
      cycle["start"] <= today_str <= cycle["end"],
      f"today={today_str} cycle={cycle['start']}→{cycle['end']}")

# 1. daily days inside cycle (end inclusive)
daily = data["daily"]
start_s, end_s = cycle["start"], cycle["end"]
inside = all(start_s <= d["day"] <= end_s for d in daily)
check("all daily days inside current cycle (inclusive end)", inside,
      str([d["day"] for d in daily if not (start_s <= d["day"] <= end_s)]))

# 2. sum of daily credits ≈ totals.credits (same rows, same filter)
sum_credits = sum(d["credits"] for d in daily)
tol = max(1.0, data["totals"]["credits"] * 0.001)
check("sum(daily.credits) ≈ totals.credits",
      abs(sum_credits - data["totals"]["credits"]) <= tol,
      f"sum={sum_credits} totals={data['totals']['credits']}")

# 3. per-day bucket split consistency
bad_split = []
for d in daily:
    split = round(d["input_cr"] + d["cached_cr"] + d["output_cr"], 2)
    if abs(split - d["credits"]) > 0.05:
        bad_split.append((d["day"], split, d["credits"]))
check("input_cr+cached_cr+output_cr ≈ credits for every day", not bad_split, str(bad_split[:3]))

# 4. bucket fields present and ints
fields_ok = all(
    isinstance(d.get(k), int) for d in daily for k in ("input", "output", "cached", "calls")
)
check("token/call bucket fields are ints", fields_ok)

# 5. today_credits consistency
today = datetime.now().strftime("%Y-%m-%d")
today_row = next((d for d in daily if d["day"] == today), None)
if today_row:
    check("today_credits matches today's daily row",
          abs(data["today_credits"] - today_row["credits"]) < 0.01,
          f"today_credits={data['today_credits']} row={today_row['credits']}")
else:
    print(f"[SKIP] no usage today ({today}); today_credits={data['today_credits']}")

# 6. cycle_days is contiguous and covers cycle start
cd = data.get("cycle_days", [])
if cd:
    check("cycle_days[0] == cycle.start", cd[0] == start_s, f"{cd[0]} vs {start_s}")
    check("cycle_days[-1] == cycle.end (expiry day included)", cd[-1] == end_s,
          f"last={cd[-1]} end={end_s}")
    from datetime import date, timedelta
    d0 = date.fromisoformat(cd[0])
    contiguous = all(
        date.fromisoformat(cd[i]) == d0 + timedelta(days=i) for i in range(len(cd))
    )
    check("cycle_days contiguous daily steps", contiguous)

# 7. UNIT: _cycle_bounds flip semantics — the anchor/expiry day belongs to
# the OLD cycle; the cycle flips the day after. C-extension datetime can't
# be patched, so _cycle_bounds takes 'now' as an injectable parameter in
# tests; production callers keep the default (datetime.now()).
print("\n_cycle_bounds unit checks (injected 'now'):")

for today, expect_start, expect_end in [
    ("2026-09-28", "2026-08-28", "2026-09-28"),  # expiry day → old cycle, today is last day
    ("2026-09-29", "2026-09-28", "2026-10-28"),  # day after → new cycle
    ("2026-09-27", "2026-08-28", "2026-09-28"),  # day before expiry
    ("2026-08-28", "2026-07-28", "2026-08-28"),  # anchor day of previous cycle
    ("2026-09-30", "2026-09-28", "2026-10-28"),  # after short-month clamp zone
    ("2026-02-28", "2026-01-28", "2026-02-28"),  # Feb short-month expiry day
    ("2026-03-01", "2026-02-28", "2026-03-28"),  # day after Feb expiry (clamped cycle)
]:
    fake = datetime.strptime(today, "%Y-%m-%d")
    s_ts, e_ts, s_lab, e_lab = mod._cycle_bounds(28, now=fake)
    ok = (s_lab == expect_start and e_lab == expect_end)
    check(f"_cycle_bounds(28) on {today} → {expect_start}..{expect_end}", ok,
          f"got {s_lab}..{e_lab}")

# end_ts must be the exclusive next-day bound: expiry-day usage (any time
# of day) still counts into the OLD cycle.
s_ts, e_ts, s_lab, e_lab = mod._cycle_bounds(28, now=datetime.strptime("2026-09-28 16:49", "%Y-%m-%d %H:%M"))
expiry_end_of_day = datetime.strptime("2026-09-28 23:59:59", "%Y-%m-%d %H:%M:%S").timestamp()
check("end_ts covers expiry day 23:59:59 (exclusive next-day 00:00 bound)",
      s_ts <= expiry_end_of_day < e_ts,
      f"end_ts={e_ts} vs 09-28 23:59:59={expiry_end_of_day}")

# show a few sample rows
print("\nsample daily rows:")
for d in daily[:5] + (daily[-3:] if len(daily) > 5 else []):
    print("  " + json.dumps(d, ensure_ascii=False))

print()
if failures:
    print(f"FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("ALL CHECKS PASSED")
