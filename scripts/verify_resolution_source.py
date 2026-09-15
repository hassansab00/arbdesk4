#!/usr/bin/env python3
"""
Resolution-source verification (docs/settlement_verification.md), as a job
you can run instead of a procedure you have to follow.

This sandbox has no egress to weather.gov, so the spot-check that gates
`settings.settlement_verified` could never be done from here. A GitHub
Actions runner *does* have normal internet access, so this script does the
whole thing there and prints a result block ready to paste into
docs/settlement_verification.md.

It does two jobs:

1. **Tests the real parser.** It calls
   `settlement.fetch_resolution_source_reading()` - the actual function
   that will decide settlements - rather than a copy. If that parser does
   not hit (and it never has been checked against a live page), the script
   does not guess: it dumps enough of the real page structure to fix the
   parser, and fails.

2. **Compares all three surfaces** for the same city-day:
   our IEM archive (`weather_observations`), `api.weather.gov`, and
   `weather.gov/wrh/timeseries` - the one that actually settles.

It NEVER writes anything. It cannot flip `settlement_verified`; that stays
a deliberate human decision after reading the output.

Usage:
    python scripts/verify_resolution_source.py [city_key] [YYYY-MM-DD]

Both arguments are optional. With neither, it picks a city that has an
ICAO code and a resolved past market, and yesterday's date.
"""
import datetime as dt
import json
import os
import re
import sys

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import rest  # noqa: E402
from common import rest_all
import settlement  # noqa: E402

UA = {"User-Agent": "AD4-resolution-verification (github.com/hassansab00/arbdesk4)"}


def pick_city(city_key=None):
    cities = rest("cities", {"select": "city_key,display_name,icao,unit,status", "limit": "500"})
    with_icao = [c for c in cities if c.get("icao")]
    if not with_icao:
        raise SystemExit("No city in `cities` has an ICAO code - nothing to verify against.")
    if city_key:
        for c in with_icao:
            if c["city_key"] == city_key:
                return c
        raise SystemExit(f"city_key {city_key!r} not found, or it has no ICAO. "
                         f"Available: {', '.join(sorted(c['city_key'] for c in with_icao))}")
    # Prefer a city that already has a past market - that is the case the
    # settlement path actually exercises.
    today = dt.date.today().isoformat()
    markets = rest("markets", [("select", "city_key,resolution_date"),
                               ("resolution_date", f"lt.{today}"),
                               ("order", "resolution_date.desc"), ("limit", "200")])
    resolved = [m["city_key"] for m in markets]
    for c in with_icao:
        if c["city_key"] in resolved:
            return c
    return with_icao[0]


def iem_max(city_key, for_date):
    """Our own archive's max for that UTC day."""
    rows = rest_all("weather_observations", [
        ("select", "valid_at,temp_c,source"),
        ("city_key", f"eq.{city_key}"),
        ("valid_at", f"gte.{for_date}T00:00:00Z"),
        ("valid_at", f"lt.{for_date}T23:59:59Z"),
    ], order="valid_at.asc,source.asc", page_size=1000)
    temps = [r["temp_c"] for r in rows if r.get("temp_c") is not None]
    return (max(temps) if temps else None), len(rows)


def api_weather_gov_max(icao, for_date):
    """The OTHER NWS surface - closest in spirit to our IEM archive."""
    url = f"https://api.weather.gov/stations/{icao}/observations"
    params = {"start": f"{for_date}T00:00:00Z", "end": f"{for_date}T23:59:59Z"}
    r = requests.get(url, params=params, headers=UA, timeout=45)
    if r.status_code == 404:
        return None, 0, f"404 - {icao} is not an api.weather.gov station (non-US station?)"
    r.raise_for_status()
    feats = r.json().get("features", [])
    temps = []
    for f in feats:
        t = (f.get("properties") or {}).get("temperature") or {}
        v, unit = t.get("value"), t.get("unitCode", "")
        if v is None:
            continue
        temps.append(v if "degC" in unit else (v - 32) * 5.0 / 9.0)
    return (max(temps) if temps else None), len(feats), None


def diagnose_page(icao, for_date):
    """
    The parser did not hit. Do NOT guess a number - report what the page
    actually looks like, so the parser can be fixed against reality.
    """
    url = settlement.resolution_source_url(icao)
    r = requests.get(url, headers=UA, timeout=45)
    html = r.text
    print(f"\n{'='*70}\nPARSER DID NOT MATCH - page diagnostics\n{'='*70}")
    print(f"url            : {url}")
    print(f"http status    : {r.status_code}")
    print(f"content-type   : {r.headers.get('content-type')}")
    print(f"page bytes     : {len(html)}")

    print("\n-- <script src=...> (the data may be fetched separately) --")
    for src in re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html)[:25]:
        print(f"   {src}")

    print("\n-- `var X = ` / `const X = ` assignments in inline script --")
    for name in sorted(set(re.findall(r'\b(?:var|const|let)\s+([A-Za-z_$][\w$]*)\s*=', html)))[:40]:
        print(f"   {name}")

    print("\n-- candidate embedded JSON blobs (first 300 chars each) --")
    for m in list(re.finditer(r'(?:var|const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(\[|\{)', html))[:8]:
        start = m.start(2)
        print(f"\n   [{m.group(1)}] {html[start:start+300]!r}")

    print("\n-- does the date appear in the page at all? --")
    for form in (for_date, for_date.replace("-", "/"),
                 dt.date.fromisoformat(for_date).strftime("%d %b %Y"),
                 dt.date.fromisoformat(for_date).strftime("%b %d")):
        print(f"   {form!r}: {'YES' if form in html else 'no'}")

    print(f"\n-- first 1500 chars of body --\n{html[:1500]}")
    print(f"\n{'='*70}")
    print("NEXT: update fetch_resolution_source_reading() in scripts/settlement.py")
    print("to match the structure above, then re-run this workflow. Do not")
    print("hand-write a temperature into docs/settlement_verification.md.")
    print("="*70)


def main():
    city_key = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] not in ("-", "auto") else None
    for_date = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] not in ("-", "auto") else \
        (dt.date.today() - dt.timedelta(days=1)).isoformat()

    city = pick_city(city_key)
    icao = city["icao"]
    print(f"city       : {city['city_key']} ({city.get('display_name') or ''})")
    print(f"icao       : {icao}")
    print(f"date       : {for_date}")
    print(f"source url : {settlement.resolution_source_url(icao)}\n")

    # ---- 1. our archive -------------------------------------------------
    try:
        iem, n_iem = iem_max(city["city_key"], for_date)
        print(f"IEM archive (weather_observations): max {iem}  ({n_iem} observations)")
    except Exception as e:
        iem, n_iem = None, 0
        print(f"IEM archive: FAILED - {e}")

    # ---- 2. api.weather.gov ---------------------------------------------
    try:
        apiwx, n_api, note = api_weather_gov_max(icao, for_date)
        print(f"api.weather.gov                  : max {apiwx}  ({n_api} observations)"
              + (f"  [{note}]" if note else ""))
    except Exception as e:
        apiwx, n_api = None, 0
        print(f"api.weather.gov                  : FAILED - {e}")

    # ---- 3. the actual resolution source, through the REAL parser -------
    parser_ok, resolution_max, parser_err = False, None, None
    try:
        resolution_max = settlement.fetch_resolution_source_reading(icao, for_date)
        parser_ok = True
        print(f"weather.gov/wrh/timeseries       : max {resolution_max}   <- THE RESOLUTION SOURCE")
    except Exception as e:
        parser_err = str(e)
        print(f"weather.gov/wrh/timeseries       : PARSER FAILED - {e}")

    if not parser_ok:
        try:
            diagnose_page(icao, for_date)
        except Exception as e:
            print(f"(diagnostics also failed: {e})")
        print("\nRESULT: NOT VERIFIED. Leave settings.settlement_verified = false.")
        sys.exit(1)

    # ---- comparison ------------------------------------------------------
    vals = {"IEM": iem, "api.weather.gov": apiwx, "resolution source": resolution_max}
    present = {k: v for k, v in vals.items() if v is not None}
    spread = (max(present.values()) - min(present.values())) if len(present) > 1 else 0.0

    print(f"\n{'='*70}")
    print("PASTE THIS INTO docs/settlement_verification.md")
    print("="*70)
    print(f"""
```
City:                {city['city_key']}
ICAO:                {icao}
Date checked:        {for_date}
Checked at:          {dt.datetime.now(dt.timezone.utc).isoformat()}
IEM max:             {iem}
api.weather.gov max: {apiwx}
weather.gov/wrh/timeseries max (the resolution source): {resolution_max}
Max spread:          {spread:.2f} C
Match?               {"YES - within 0.5C" if spread <= 0.5 else "NO - investigate before flipping settlement_verified"}
```
""".rstrip())
    print("="*70)

    if spread <= 0.5:
        print("\nAll available surfaces agree within 0.5 C, and the parser hit a live page.")
        print("That is the condition docs/settlement_verification.md asks for.")
        print("Flip the gate ONLY after you have read the numbers above yourself:")
        print("""
  update settings
  set value = jsonb_set(value, '{value}', 'true')
  where key = 'settlement_verified';
""")
    else:
        print(f"\nSurfaces disagree by {spread:.2f} C. Do NOT flip settlement_verified.")
        print("A disagreement here is exactly the thing this check exists to catch:")
        print("settling off the wrong surface pays the wrong person.")
        sys.exit(1)


if __name__ == "__main__":
    main()
