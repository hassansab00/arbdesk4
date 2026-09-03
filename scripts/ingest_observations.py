"""
AD4 Phase 1 - station observation ingest (IEM METAR).

This is the TRUTH the forecast is measured against, and the running-max feed
for intraday logic. Runs frequently; each row records valid_at (what it
describes) separately from observed_at (when we knew it).

Usage:
  python scripts/ingest_observations.py            # last 2 days (scheduled runs)
  python scripts/ingest_observations.py 365        # backfill N days
"""
import sys, io, csv, datetime as dt
import requests
from common import get_cities, upsert, log_run, retry

IEM = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"

def fetch_station(icao, start, end):
    p = {
        "station": icao, "data": "tmpf,dwpf,relh,drct,sknt,p01i,skyc1",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year,   "month2": end.month,   "day2": end.day,
        "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no",
        "missing": "empty", "trace": "empty", "direct": "no", "report_type": "3",
    }
    r = requests.get(IEM, params=p, timeout=300)
    r.raise_for_status()
    return r.text

def f_to_c(v):
    return None if v is None else round((v - 32.0) * 5.0 / 9.0, 2)

def num(x):
    try:
        v = float(x)
        return v if v == v else None
    except Exception:
        return None


# METAR sky-cover code -> oktas (eighths of sky covered). IEM's skyc1 column
# is a CODE, not a number, and weather_observations.cloud_cover is numeric on
# the real database - so writing the raw code straight through is what made
# every observations run die with:
#
#     invalid input syntax for type numeric: "FEW"
#
# Oktas is the standard numeric form of exactly this measurement, so the
# conversion loses nothing: FEW is 1-2 oktas, SCT 3-4, BKN 5-7, OVC 8. The
# midpoint of each band is used. VV (vertical visibility) means the sky is
# obscured, which is a full 8.
SKY_OKTAS = {
    "SKC": 0, "CLR": 0, "NSC": 0, "NCD": 0, "CAVOK": 0,
    "FEW": 2, "SCT": 4, "BKN": 6, "OVC": 8, "VV": 8,
}

def sky_oktas(code):
    """METAR sky-cover code -> 0-8, or None when absent/unrecognised."""
    c = (code or "").strip().upper()
    if not c:
        return None
    if c in SKY_OKTAS:
        return SKY_OKTAS[c]
    # some feeds send e.g. "BKN035" (cover + height) or "VV003"
    for prefix, oktas in SKY_OKTAS.items():
        if c.startswith(prefix):
            return oktas
    return None

def parse(text, city_key):
    rows, rdr = [], csv.DictReader(io.StringIO(text))
    for rec in rdr:
        ts = (rec.get("valid") or "").strip()
        if not ts:
            continue
        try:
            valid = dt.datetime.strptime(ts[:16], "%Y-%m-%d %H:%M").replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
        tmpf = num(rec.get("tmpf"))
        if tmpf is None:
            continue
        rows.append({
            "city_key": city_key,
            "station": (rec.get("station") or "").strip() or None,
            "valid_at": valid.isoformat(),
            "temp_f": tmpf,
            "temp_c": f_to_c(tmpf),
            "dewpoint_c": f_to_c(num(rec.get("dwpf"))),
            "humidity": num(rec.get("relh")),
            "wind_speed": num(rec.get("sknt")),
            "wind_dir_deg": num(rec.get("drct")),
            "precip": num(rec.get("p01i")),
            "cloud_cover": sky_oktas(rec.get("skyc1")),
            "source": "IEM",
        })
    return rows

def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    end = dt.datetime.now(dt.timezone.utc).date()
    start = end - dt.timedelta(days=days)

    cities = get_cities(require_coords=False, require_icao=True)
    print(f"stations: {len(cities)}  window: {start} -> {end} ({days}d)")

    total, failed = 0, []
    for i, c in enumerate(cities, 1):
        icao = c["icao"]
        txt = retry(lambda: fetch_station(icao, start, end), label=icao)
        if not txt:
            failed.append(icao); continue
        rows = parse(txt, c["city_key"])
        if not rows:
            failed.append(icao); continue
        n = upsert("weather_observations", rows, "city_key,valid_at,source")
        total += n
        print(f"  [{i}/{len(cities)}] {icao:6s} {n:6d} obs")

    print(f"\ntotal {total} observations, {len(failed)} stations failed")
    if failed:
        print("failed:", ", ".join(failed))
    log_run("ingest_observations", "ok" if not failed else "partial", total,
            {"days": days, "stations": len(cities), "failed": failed})

if __name__ == "__main__":
    main()
