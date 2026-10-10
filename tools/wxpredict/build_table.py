"""WXPredict phase 1: one training table, every row knowing only what was known.

One row per (event, decision time). An event is one city's "Highest temperature
in <city> on <day>" market on Polymarket (data/training/market_history). The
decision times are every hour of the event's local day D (00:00-23:00) and
every three hours of the day before (D-1 00:00-21:00), on the city's clock,
each SNAPSHOT_S after the hour.

WHAT A ROW MAY KNOW AT DECISION TIME t
-------------------------------------
- A station report: when valid + REPORT_LAG_S <= t (REPORT_LAG_S measured
  against IEM, see the constant).
- An hourly forecast value for hour H (best_match, `_previous_day1`): when
  H - (24 - PUBLISH_H) h <= t. `_previous_day1` is a run started at least 24 h
  before H; PUBLISH_H is P2.9's stated publication allowance (the record's
  README: assumed from the providers' schedules, not verified run by run).
- A daily forecast row (lead L, window ending at hour E local): when its last
  hour E on day D satisfies D E:00 - (24 L - PUBLISH_H) h <= t. So the lead-1
  00-17 window is known from D 00:00, the lead-1 whole day from D 06:00, the
  lead-2 whole day from D-1 06:00.
- A market price: a point of the venue's hourly series at or before t. The
  series stamps every point 0-59 s past the hour (all 6,473,670 points of the
  5 Oct record), so the decision is taken SNAPSHOT_S after the hour: the row
  sees that hour's snapshot, the same price the model is judged against (the
  market at the decision), and nothing later.
- A day's station maximum (yesterday, a forecast's past error, climatology):
  when the whole local day ended REPORT_LAG_S before t, and only a whole day
  (whole_day: no gap over common.WHOLE_DAY_MAX_GAP_H in its reports, midnight
  to midnight). The label and the unlisted station days read the same rule.

THE LABEL
---------
The venue's winning bucket (index into the ladder, lowest first) - the only
truth. The station's own maximum of D in the market's unit is a second column,
kept for the agreement check. `label_unit` is the weather model's target: the
station's maximum moved into the winning bucket when the two disagree
(venue_label), the station's maximum on a day the venue did not list.

    python tools/wxpredict/build_table.py            # -> data/training/wxpredict/table/
    python tools/wxpredict/build_table.py --limit 200

The table is not committed (it rebuilds byte for byte from the committed
sources); its row count, columns and sha256 are, in
data/training/wxpredict/table_meta.json.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import os
import sys
from array import array

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common  # noqa: E402

OUT_DIR = os.path.join(common.WX, "table")
META = os.path.join(common.WX, "table_meta.json")

# Every feature, and the constants that say what a row may know, live in
# features.py: one implementation for the table and the live reader (phase
# 2.3). They are imported here under their old names.
from wxpredict.features import (  # noqa: E402,F401
    REPORT_LAG_S, PUBLISH_H, SNAPSHOT_S, DAY_HOURS, EVE_HOURS, MARKET_MAX_AGE_S, PRICE_FLOOR,
    CLIM_HALF_WINDOW, BIAS_WINDOWS, MODELS, SKY_OKTAS, WX_FLAGS, unix_of, num, f_to_c, c_to_f,
    round_half_up, unit_reading, fmt, bucket_of, bucket_rep, sky, wx_bits, Reports, NOT_WHOLE,
    UNFINISHED, whole_day, climatology, hourly_known, daily_known, pick_daily, ladder_at, implied,
    activity, COLUMNS, decision_instants, venue_label, event_context, decision_row, build_event,
    obs_features, fc_features, bias_features, market_features,
)

STATION_FROM = "2025-07-15"    # the first day previous_runs covers whole (14 Jul is partial)


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------
def load_events(stations_with_reports, first=None, last=None):
    """([event], {reason: count}). Eligible: an active city; closed; exactly one
    winner; the main listing when an 'arch' twin exists; a contiguous ladder;
    a settlement station whose reports are in the repository."""
    act = common.active_cities()
    rows = list(common.read_csv(os.path.join(common.MH, "events.csv.gz")))
    main_days = {(r["city_key"], r["date"]) for r in rows if r["listing"] == "main"}
    listed = {(r["city_key"], r["date"]) for r in rows}
    left = collections.Counter()
    ev = {}
    for r in rows:
        if first and r["date"] < first or last and r["date"] > last:
            left["outside the dates asked"] += 1
            continue
        if r["city_key"] not in act:
            left["city not active"] += 1
            continue
        if r["closed"] != "1":
            left["not closed"] += 1
            continue
        if r["listing"] == "arch" and (r["city_key"], r["date"]) in main_days:
            left["arch twin of a main listing"] += 1
            continue
        if r["station_icao"] not in stations_with_reports:
            left["no settlement station reports"] += 1
            continue
        ev[r["event_id"]] = {"event_id": r["event_id"], "source": "venue", "city": r["city_key"],
                             "date": r["date"], "unit": r["unit"], "station": r["station_icao"], "bands": []}
    for r in common.read_csv(os.path.join(common.MH, "bands.csv.gz")):
        e = ev.get(r["event_id"])
        if e is not None:
            e["bands"].append((int(r["band_index"]), num(r["band_lo"]), num(r["band_hi"]),
                               r["open_low"] == "1", r["open_high"] == "1", r["winner"]))
    out = []
    for eid in sorted(ev, key=lambda k: (ev[k]["date"], ev[k]["city"], k)):
        e = ev[eid]
        bands = sorted(e["bands"])
        wins = [b[0] for b in bands if b[5] == "1"]
        if len(wins) != 1 or any(b[5] not in ("0", "1") for b in bands):
            left["not exactly one winner"] += 1
            continue
        if len(bands) < 2 or not bands[0][3] or not bands[-1][4] or \
                any(a[2] != b[1] for a, b in zip(bands, bands[1:])):
            left["ladder not contiguous"] += 1
            continue
        e["bands"] = [(lo, hi) for _, lo, hi, _, _, _ in bands]
        e["winner"] = wins[0]
        out.append(e)
    return out, dict(left), listed


def station_events(venue_days, stations, sdaily, refused, first=None, last=None):
    """(events, {reason: count}): the city-days the venue did not list, as rows
    for the weather model (component A trains on every whole station day; the
    venue's label exists only on listed days). Each active city at its station
    today, from the first day the honest forecast record covers to the last
    whole day of reports. A day that is not whole (whole_day) is left out and
    counted, and so is a day inside that span with no report at all."""
    out = []
    left = collections.Counter()
    for city, c in sorted(common.active_cities().items()):
        st = (c.get("icao") or "").upper()
        if st not in stations or not sdaily.get(st):
            continue
        unit = c.get("unit") or "C"
        days = {**{d: None for d in sdaily[st]}, **refused.get(st, {})}
        span = sorted(d for d in days if d >= STATION_FROM and (not first or d >= first)
                      and (not last or d <= last))
        if not span:
            continue
        d, end = dt.date.fromisoformat(span[0]), dt.date.fromisoformat(span[-1])
        while d <= end:
            k = d.isoformat()
            d += dt.timedelta(days=1)
            if (city, k) in venue_days:
                continue
            if k not in days:
                left["no report"] += 1
            elif days[k] is not None:
                left[days[k]] += 1
            else:
                out.append({"event_id": f"wx:{city}:{k}", "source": "station", "city": city, "date": k,
                            "unit": unit, "station": st, "bands": [], "winner": None})
    return out, dict(left)


def load_reports(station):
    return Reports(common.read_csv(os.path.join(common.REPORTS, f"{station}.csv.gz")))


def load_station_daily():
    """({station: {date: (tmax_c, tmax_f, peak local hour)}} for the whole
    days, {station: {date: why not}} for the rest). Every past-day read -
    yesterday, climatology, the forecast's past error, the unlisted days -
    takes the first, so none can read a day that is not whole."""
    rows = list(common.read_csv(common.STATION_DAILY))
    newest = collections.defaultdict(int)
    for r in rows:
        newest[r["station"]] = max(newest[r["station"]], unix_of(r["last_valid"]))
    out = collections.defaultdict(dict)
    refused = collections.defaultdict(dict)
    cities = common.cities()
    zones = {}
    for r in rows:
        tz = zones.get(r["city_key"]) or zones.setdefault(r["city_key"], common.zone(cities[r["city_key"]]))
        why = whole_day(float(r["max_gap_h"]),
                        common.local_day_bounds(dt.date.fromisoformat(r["local_date"]), tz)[1], newest[r["station"]])
        if why:
            refused[r["station"]][r["local_date"]] = why
            continue
        at = dt.datetime.fromtimestamp(unix_of(r["tmax_at"]), common.UTC).astimezone(tz)
        out[r["station"]][r["local_date"]] = (float(r["tmax_c"]), float(r["tmax_f"]), at.hour + at.minute / 60)
    return dict(out), dict(refused)


# ---------------------------------------------------------------------------
# forecasts
# ---------------------------------------------------------------------------
def load_daily_forecasts():
    bm = {}
    for r in common.read_csv(os.path.join(common.PR, "best_match_daily.csv.gz")):
        bm[(r["city_key"], int(r["lead_days"]), r["for_date"])] = r
    md = collections.defaultdict(dict)
    for r in common.read_csv(os.path.join(common.PR, "models_daily.csv.gz")):
        md[(r["city_key"], int(r["lead_days"]), r["for_date"])][r["model"]] = (num(r["tmax_c"]),
                                                                              num(r["tmax_00_17_c"]))
    return bm, md


def load_hourly_forecasts():
    """{city: (unix hours, rows)} best_match `_previous_day1`, oldest first."""
    out = collections.defaultdict(lambda: ([], []))
    for r in common.read_csv(os.path.join(common.PR, "best_match_hourly_day1_utc.csv.gz")):
        u = unix_of(r["utc"])
        ts, rows = out[r["city_key"]]
        ts.append(u)
        rows.append((num(r["t"]), num(r["td"]), num(r["cloud"]), num(r["sw"]), num(r["wind"]), num(r["precip"]),
                     num(r["pmsl"])))
    for k, (ts, rows) in out.items():
        order = sorted(range(len(ts)), key=ts.__getitem__)
        out[k] = ([ts[i] for i in order], [rows[i] for i in order])
    return dict(out)


# ---------------------------------------------------------------------------
# the market
# ---------------------------------------------------------------------------
def load_prices(events):
    """{event_id: [(ts, ps) per band]} for the events asked, from the day
    before D-1 to the end of D (every decision's window and its 6 h of history)."""
    want = {}
    cities = common.cities()
    for e in events:
        tz = common.zone(cities[e["city"]])
        d = dt.date.fromisoformat(e["date"])
        lo = int(dt.datetime.combine(d - dt.timedelta(days=2), dt.time(0), tz).timestamp())
        hi = int(dt.datetime.combine(d + dt.timedelta(days=1), dt.time(0), tz).timestamp())
        want[e["event_id"]] = (lo, hi, len(e["bands"]))
    out = {k: [(array("q"), array("d")) for _ in range(n)] for k, (_, _, n) in want.items()}
    for r in common.read_csv(os.path.join(common.MH, "prices.csv.gz")):
        w = want.get(r["event_id"])
        if w is None:
            continue
        t = int(r["t"])
        if w[0] <= t < w[1]:
            b = int(r["band_index"])
            if b < w[2]:
                out[r["event_id"]][b][0].append(t)
                out[r["event_id"]][b][1].append(float(r["p"]))
    for series in out.values():
        for ts, ps in series:
            order = sorted(range(len(ts)), key=ts.__getitem__)
            if any(order[i] != i for i in range(len(order))):
                ts[:] = array("q", [ts[i] for i in order])
                ps[:] = array("d", [ps[i] for i in order])
    return out


# ---------------------------------------------------------------------------
# the table
# ---------------------------------------------------------------------------
def build(limit=0, first=None, last=None, out_dir=OUT_DIR, write_meta=True):
    stations = {f[:-7] for f in os.listdir(common.REPORTS) if f.endswith(".csv.gz")}
    events, left, listed = load_events(stations, first, last)
    sdaily, refused = load_station_daily()
    unlisted, station_left = station_events(listed, stations, sdaily, refused, first, last)
    events += unlisted
    events.sort(key=lambda e: (e["date"], e["city"], e["event_id"]))
    if limit:
        events = events[:limit]
    cities = common.cities()
    bm, md = load_daily_forecasts()
    hourly = load_hourly_forecasts()
    prices = load_prices([e for e in events if e["source"] == "venue"])
    reports = {s: load_reports(s) for s in sorted({e["station"] for e in events})}
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "wxpredict_table.csv.gz")
    cov = Coverage(left, station_left)

    def lines():
        for e in events:                      # in (date, city, event) order
            tz = common.zone(cities[e["city"]])
            for v in build_event(e, reports[e["station"]], sdaily.get(e["station"], {}), bm, md, hourly,
                                 prices, tz):
                row = [x if isinstance(x, str) else fmt(x) for x in (v.get(c) for c in COLUMNS)]
                cov.add(v, row)
                yield row

    n = common.write_csv(path, COLUMNS, lines())
    meta = cov.result(n)
    if write_meta:
        with open(META, "w") as f:
            json.dump(meta, f, indent=1, sort_keys=True)
            f.write("\n")
    return meta


class Coverage:
    """What the table holds, counted as it is written: rows, how often each
    column is filled, the label check, and the content's sha256."""

    def __init__(self, left, station_left=None):
        self.left = left
        self.station_left = station_left or {}
        self.local_agrees = 0
        self.filled = collections.Counter()
        self.sha = hashlib.sha256()
        self.events = {}
        self.rows_on_d = 0
        self.complete = 0
        self.dates = set()
        self.cities = set()

    def add(self, v, row):
        self.sha.update(("\x1f".join(row) + "\n").encode())
        self.local_agrees += dt.datetime.fromisoformat(v["decision_local"]).timestamp() == v["decision_utc"]
        for c, x in zip(COLUMNS, row):
            if x != "":
                self.filled[c] += 1
        self.events[v["event_id"]] = (v["unit"], v["station_in_winner"], v["source"])
        self.rows_on_d += v["day_offset"] == 0
        self.complete += bool(v.get("mkt_complete"))
        self.dates.add(v["date"])
        self.cities.add(v["city_key"])

    def result(self, n):
        judged = [w for _, w, src in self.events.values() if src == "venue" and w is not None]
        dates = sorted(self.dates)
        return {
            "rows": n, "events": len(self.events), "cities": len(self.cities),
            "dates": [dates[0], dates[-1]] if dates else [],
            "events_by_source": dict(collections.Counter(src for _, _, src in self.events.values())),
            "events_by_unit": dict(collections.Counter(u for u, _, _ in self.events.values())),
            "left_out": self.left, "station_days_left_out": self.station_left,
            "whole_day_max_gap_h": common.WHOLE_DAY_MAX_GAP_H,
            "venue_days_station_not_whole": sum(1 for _, w, src in self.events.values()
                                                if src == "venue" and w is None),
            "decision_local_parses_back": self.local_agrees,
            "report_lag_s": REPORT_LAG_S, "publish_h": PUBLISH_H, "snapshot_s": SNAPSHOT_S,
            "market_max_age_s": MARKET_MAX_AGE_S,
            "station_max_in_winner": {"agree": sum(judged), "judged": len(judged)},
            "rows_on_d": self.rows_on_d, "rows_market_complete": self.complete,
            "filled": {c: self.filled[c] for c in COLUMNS}, "sha256": self.sha.hexdigest(), "columns": COLUMNS,
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--from", dest="first", default=None)
    ap.add_argument("--to", dest="last", default=None)
    args = ap.parse_args()
    meta = build(args.limit, args.first, args.last, write_meta=not args.limit)
    show = {k: meta[k] for k in ("rows", "events", "cities", "dates", "events_by_source", "events_by_unit", "left_out",
                                 "station_days_left_out", "venue_days_station_not_whole", "decision_local_parses_back",
                                 "station_max_in_winner", "rows_on_d", "rows_market_complete", "sha256")}
    print(json.dumps(show, indent=1), file=sys.stderr)


if __name__ == "__main__":
    main()
