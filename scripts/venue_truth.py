"""The truth the forecast corrections learn from (plan v2.4 P3.10 part 3.1).

P3.9's station correction and P2.9's station model learned from
derived_city_day_features.max_c, the station maximum as the desk read it. The
model is scored on the venue's winner, and the two disagree (measured 28 Sep):

  * C cities before Sep: that maximum names a LOWER bucket than the venue's
    winner on 10.3% of 5,936 city-days (docs/MODEL_VS_MARKET_2026-09-28.md, Q6);
  * F cities, 24 Aug-27 Sep: it differs from the venue's own reading on 132 of
    311 city-days, 0.21 C too high on average (141 are whole Celsius, which
    cannot place a 2 F bucket).

Trained on the venue's truth instead, the same recipe lost its low bias (+0.112
-> +0.010 buckets) and priced the ladder better at 08:00 (log loss +0.0079
[+0.0013, +0.0140] on 7,069 city-days).

The labels come from v_venue_truth (supabase/migrations/20260928120000): the
venue's verified reading when it lies in the confirmed winner's bucket (or no
winner is confirmed yet), else the winning bucket's midpoint reading. The
database holds those from 22 Aug; for earlier days (P3.9's windows reach back
~75 days) the committed record (data/training/market_history, P3.10) gives the
winning bucket the same way, on the same filters as the study: an active
city's resolved event, on the city's current station, with exactly one winner
(a "main" listing before an "arch-" one). A city-day with neither has no
label: an old station maximum is not mixed in.

settings.truth_labels {"source": "venue" | "station"} chooses what the learners
fit on; absent, it is "venue". Both learners log the other label set's
walk-forward score against the venue's truth beside their own, so the choice is
checked every night.
"""
import csv
import gzip
import os
import sys

RECORD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "training", "market_history")
SETTING = "truth_labels"
DEFAULT = "venue"
SOURCES = ("venue", "station")


def source(rest_all):
    """'venue' or 'station': the labels the learners fit on tonight. An
    unreadable or unknown setting is the default, said on stderr."""
    try:
        rows = rest_all("settings", [("select", "value"), ("key", f"eq.{SETTING}")], order="key.asc")
    except Exception as e:
        print(f"  note: settings.{SETTING} unreadable ({e}); fitting on {DEFAULT}", file=sys.stderr)
        return DEFAULT
    value = ((rows[0].get("value") if rows else None) or {}).get("source") or DEFAULT
    if value not in SOURCES:
        print(f"  note: settings.{SETTING} names {value!r}; fitting on {DEFAULT}", file=sys.stderr)
        return DEFAULT
    return value


def bucket_label_c(lo, hi, unit):
    """The midpoint of the whole readings a closed bucket [lo, hi) holds, in C
    ([k, k+1) C -> k; [2j, 2j+2) F -> 2j + 0.5 F), or None for an open one."""
    if lo is None or hi is None:
        return None
    reading = (float(lo) + float(hi) - 1) / 2
    return reading if unit == "C" else (reading - 32) * 5 / 9


def record_labels(icao, start=None, end=None, record=RECORD):
    """{(city_key, date): label_c} from the committed record's winning buckets.
    icao: {city_key: the city's current station}; other cities are left out."""
    def rows(name):
        with gzip.open(os.path.join(record, name), "rt", newline="") as f:
            yield from csv.DictReader(f)
    events, main_days = {}, set()
    for r in rows("events.csv.gz"):
        if r["listing"] == "main":
            main_days.add((r["city_key"], r["date"]))
    for r in rows("events.csv.gz"):
        city, day = r["city_key"], r["date"]
        if (city not in icao or r["closed"] != "1" or (start is not None and day < str(start))
                or (end is not None and day >= str(end))
                or (r["listing"] == "arch" and (city, day) in main_days)
                or r["station_icao"] != (icao[city] or "").upper()):
            continue
        events[r["event_id"]] = (city, day, r["unit"])
    winners = {}
    for r in rows("bands.csv.gz"):
        if r["event_id"] in events and r["winner"] == "1":
            winners.setdefault(r["event_id"], []).append(r)
    out = {}
    for eid, ws in winners.items():
        if len(ws) != 1:
            continue
        city, day, unit = events[eid]
        label = bucket_label_c(ws[0]["band_lo"] or None, ws[0]["band_hi"] or None, unit)
        if label is not None:
            out[(city, day)] = label
    return out


def load(rest_all, start=None, end=None, icao=None, record=RECORD):
    """{(city_key, for_date): label_c}, for_date in [start, end): v_venue_truth,
    and the committed record for the city-days the database does not hold.
    icao: {city_key: current station} of the active cities (common.get_cities,
    the one definition of active); without it the record is not read."""
    params = [("select", "city_key,for_date,label_c,source")]
    if start is not None:
        params.append(("for_date", f"gte.{start}"))
    if end is not None:
        params.append(("for_date", f"lt.{end}"))
    rows = rest_all("v_venue_truth", params, order="city_key.asc,for_date.asc")
    out = {(r["city_key"], str(r["for_date"])): float(r["label_c"]) for r in rows
           if r.get("label_c") is not None}
    if icao and record and os.path.exists(os.path.join(record, "events.csv.gz")):
        for key, label in record_labels(icao, start, end, record).items():
            out.setdefault(key, label)
    return out
