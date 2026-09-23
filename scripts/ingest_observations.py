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

# How much calendar time one request may cover. 48 stations over 2 days came
# back as 2,239 rows in 138 KB; a year in one request would be ~420,000 rows
# and ~25 MB, which is not a request, it is an outage waiting for a timeout.
MAX_DAYS_PER_REQUEST = 30


def fetch_station(station, start, end, since=None, until=None):
    """One request. `station` is an ICAO string OR a list of them.

    THE PER-IP RATE LIMIT WAS AN ARTEFACT OF ASKING 48 TIMES.

    This job sent one request per station, and moving it to n8n - where the
    whole board shares one egress IP with every other tenant - lost most of
    the board every run. Measured against the live service:

        12 concurrent   44 of 48 stations returned nothing
         4 concurrent   42 of 48
         1 at a time    34 of 48, in 26 s, because most were rejected
                        before they were served
        every failure body: "Too many requests from your IP address, slow down."

    Serialising did not help, which is the tell: the limiter counts REQUESTS
    per IP, not connections. So the fix is not to go slower, it is to stop
    making 48 requests. The ASOS service accepts REPEATED station= parameters,
    and `requests` serialises a list into exactly that. Measured from the same
    n8n egress that had been losing two thirds of the board:

        one request, all 48 stations   48 of 48 returned, 2,239 rows,
                                       138 KB, ~4 s, no rate limit at all

    sts/ets bound the window to an INSTANT rather than a day, which is what
    makes an hourly cadence cheap: the same 48 stations over the last 4 hours
    came back as 168 rows in 10 KB. They override the day parameters, which
    are still sent so that a caller reading year1/day2 off the query (and the
    test that pins the day2-is-exclusive fix) still sees them.
    """
    p = {
        # mslp, NOT alti or a raw station pressure. Sea-level pressure is the
        # only one comparable across a board that runs from Singapore at 5 m
        # to Mexico City at 2,240 m: station pressure there is ~770 hPa on the
        # calmest day of the year, so a single coefficient fitted across
        # cities would be reading altitude, not weather.
        "station": station, "data": "tmpf,dwpf,relh,drct,sknt,p01i,skyc1,mslp",
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": end.year,   "month2": end.month,   "day2": end.day,
        "tz": "Etc/UTC", "format": "onlycomma", "latlon": "no",
        "missing": "empty", "trace": "empty", "direct": "no",
        # ROUTINE AND SPECIAL REPORTS - every report the station files, which
        # is what the venue's WRH page shows (plan v2 P2.1). report_type=3
        # alone is the routine report only: one per hour, so a European
        # station filing half-hourly lost half its day and every US special
        # was dropped. Measured against IEM on 23 Sep, local days, max
        # against the WRH evidence the venue settles on:
        #   EGLC 17 Sep   3: 24 rows, 21C   3+4: 48 rows, 22C   WRH 22C
        #   EHAM 12 Sep   3: 24 rows, 21C   3+4: 48 rows, 22C   WRH 22C
        #   LGA  20 Sep   3: 24 rows, 71F   3+4: 37 rows, 72F   WRH 72F
        #   DAL  13 Sep   3: 24 rows, 99F   3+4: 24 rows, 99F   WRH 99F
        # 3+4 does not bring in the US five-minute feed (LGA: 13 extra rows,
        # all specials). report_type=1 returned nothing for EGLC and EHAM.
        "report_type": ["3", "4"],
    }
    if since is not None:
        p["sts"] = since.strftime("%Y-%m-%dT%H:%MZ")
        p["ets"] = (until or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%MZ")
    r = requests.get(IEM, params=p, timeout=300)
    r.raise_for_status()
    return r.text


def station_map(cities):
    """station id as IEM REPORTS it -> city_key.

    IEM ANSWERS A US STATION UNDER ITS THREE-LETTER ID. Asking for KAUS
    returns rows labelled AUS; asking for EGLC returns EGLC. That is invisible
    while each request carries one station, because the caller already knows
    which city it asked about - and it is fatal the moment one response
    carries all of them. A map keyed on cities.icao alone silently drops every
    US city: measured on this board, 11 of 48, including New York, Chicago,
    Los Angeles and Miami. The whole test suite passes while it happens,
    because the only fixture uses KAUS where production sends AUS.

    Both spellings are mapped. A collision is refused rather than resolved:
    two cities answering to one id would attribute somebody else's weather to
    a market, which is worse than a station nobody claims.
    """
    out, clash = {}, []
    for c in cities:
        icao = (c.get("icao") or "").strip().upper()
        if not icao:
            continue
        for key in (icao, icao[1:] if len(icao) == 4 and icao.startswith("K") else None):
            if not key:
                continue
            if out.get(key, c["city_key"]) != c["city_key"]:
                clash.append((key, out[key], c["city_key"]))
                continue
            out[key] = c["city_key"]
    if clash:
        raise ValueError("two cities answer to the same IEM station id: " +
                         "; ".join(f"{k}: {a} and {b}" for k, a, b in clash))
    return out


def day_chunks(start, end, max_days=MAX_DAYS_PER_REQUEST):
    """[start, end) split so no request covers more than max_days."""
    out, a = [], start
    while a < end:
        b = min(a + dt.timedelta(days=max_days), end)
        out.append((a, b))
        a = b
    return out or [(start, end)]

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

def parse(text, city_key=None, by_station=None):
    """Rows from one CSV response.

    city_key stays the second positional argument: scripts/live_weather.py and
    the older tests call parse(text, "nyc") and must go on working.

    by_station turns on the BATCHED form - one response carrying many
    stations, each row attributed by its own station column. A row whose
    station is not in the map is DROPPED, never guessed: an unrecognised id is
    either a city we did not ask for or the K-prefix trap in station_map(),
    and attributing it to whichever city was last seen would put one city's
    afternoon on another city's market.
    """
    rows, rdr = [], csv.DictReader(io.StringIO(text))
    unknown = set()
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
        station = (rec.get("station") or "").strip().upper()
        row_city = city_key
        if by_station is not None:
            row_city = by_station.get(station)
            if row_city is None:
                unknown.add(station)
                continue
        rows.append({
            "city_key": row_city,
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
            # PRESSURE WAS NEVER COLLECTED. weather_observations.pressure_hpa
            # has existed since ad4_00 and derived_city_day_features has
            # carried morning_pressure_hpa and pressure_change_24h_hpa the
            # whole time; ad4_28 even buckets the 24h change into "falling
            # hard / falling / steady / rising" for the reasoning panel.
            #
            # All of it read a column nothing wrote. Measured before this
            # line existed: 0 of 142,529 observations had a pressure, from
            # either source, and so 0 of 22,294 city-days had a morning
            # pressure. The schema said the desk understood pressure. The
            # data said it had never seen one.
            #
            # `mslp` is a station-reported field like the rest, so IEM serves
            # it for the whole archive - one backfill puts pressure on the
            # history the model trains against, rather than starting a
            # 120-day wait for the first usable day.
            "pressure_hpa": num(rec.get("mslp")),
            "source": "IEM",
        })
    if unknown:
        print(f"  ! {len(unknown)} unrecognised station id(s) dropped: "
              + ", ".join(sorted(unknown)[:10]), file=sys.stderr)
    return rows

def window(days, today=None):
    """The dates to ask IEM for, INCLUDING today.

    `day2` ON THE ASOS SERVICE IS EXCLUSIVE, and for three weeks this asked
    for `end = today`, which means [start, today) - every day but the one
    being traded.

    The damage was invisible because it self-heals overnight: tomorrow's run
    asks for a window that ends after today, so today's readings do arrive -
    just never while they matter. Every history you look at afterwards is
    complete, which is why this survived every check anyone made of it.

    Measured on 21 Sep before the fix, over 7 days of rows:

        IEM   7,830 rows, EVERY ONE written the day after it describes
        NWS  22,482 rows written the same day (+1,217 across midnight)

    Not one IEM row at a lag of zero, ever. A publishing delay upstream would
    have produced a spread; a window that stops at midnight produces exactly
    this. And the two runs on 21 Sep prove it was not latency: the 04:45 run
    came back with readings from 23:58 the previous night - 4.8 hours behind
    real time - while the 12:40 run, eight hours later, still stopped at that
    same wall. IEM had the data both times. It was never asked.

    What it cost: the 37 cities with no api.weather.gov feed ran on
    observations averaging 14.7 hours old and up to 25.7, at 10 readings a
    day against 291 for the 11 NWS cities. These are DAILY HIGH markets, so
    for most of the board the desk could not see the day's peak until the day
    was over.
    """
    today = today or dt.datetime.now(dt.timezone.utc).date()
    return today - dt.timedelta(days=days), today + dt.timedelta(days=1)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    start, end = window(days)

    cities = get_cities(require_coords=False, require_icao=True)
    by_station = station_map(cities)
    icaos = sorted({(c.get("icao") or "").strip().upper() for c in cities if c.get("icao")})
    chunks = day_chunks(start, end)
    print(f"stations: {len(icaos)}  window: {start} -> {end} exclusive ({days}d + today)"
          f"  requests: {len(chunks)}")

    total, empty_chunks, seen = 0, 0, {}
    for i, (a, b) in enumerate(chunks, 1):
        # ONE REQUEST FOR THE WHOLE BOARD. See fetch_station: the per-IP rate
        # limit that kept this job off n8n counts requests, so 48 of them is
        # the defect and 1 of them is the fix.
        txt = retry(lambda: fetch_station(icaos, a, b),
                    label=f"{len(icaos)} stations {a}..{b}")
        if not txt:
            empty_chunks += 1
            continue
        rows = parse(txt, by_station=by_station)
        if not rows:
            empty_chunks += 1
            continue
        for r in rows:
            seen[r["city_key"]] = seen.get(r["city_key"], 0) + 1
        n = upsert("weather_observations", rows, "city_key,valid_at,source")
        total += n
        print(f"  [{i}/{len(chunks)}] {a} -> {b}  {n:6d} obs across "
              f"{len({r['city_key'] for r in rows})} city/cities")

    # A CITY THAT CAME BACK WITH NOTHING IS NAMED. The old loop reported a
    # failed STATION, which a batched request cannot have - the request either
    # lands or it does not. What can still go wrong is a station the service
    # has no data for, and that has to be visible per city or it is invisible.
    silent = sorted(c["city_key"] for c in cities if c["city_key"] not in seen)
    print(f"\ntotal {total} observations across {len(seen)} of {len(cities)} city/cities")
    if silent:
        print(f"{len(silent)} city/cities returned nothing: " + ", ".join(silent[:12]))
    status = "ok" if not silent and not empty_chunks else "partial"
    log_run("ingest_observations", status, total,
            {"days": days, "stations": len(icaos), "requests": len(chunks),
             "cities_with_rows": len(seen), "silent_cities": silent,
             "empty_chunks": empty_chunks})

if __name__ == "__main__":
    main()
