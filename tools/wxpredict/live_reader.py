"""WXPredict WP3b: every decision's features from the live record, as known then.

ONE IMPLEMENTATION. The live record (public.live_capture, written in the hourly
tick by scripts/live_capture.py; copied to data/mirror/live_capture each night
and archived to data/archive/live_capture after two days) keeps every input the
tick had, with the time it arrived. This reads it into the shapes
tools/wxpredict/features.py takes and builds each row with
features.decision_row, the function the training table is built with
(build_table.py). So a live row and the table's row for the same instant can
differ only where their sources differ, and the parity test (plan phase 2.3)
names each such difference.

FROM THE RECORD (the live sources):
  reports  every station report the tick's IEM read returned, made into the
           training's report row by fetch_obs.remarks (the METAR's T group and
           6-hour extremes) under the station's ICAO
  hourly   best_match `_previous_day1` hourly values ('hourly')
  daily    the event's own day at leads 1 and 2: best_match's daily row and the
           seven models' maxima, reduced from the recorded hourly values
           ('hourly', 'hourly2', 'models1', 'models2') by the nightly record's
           own functions (honest_record.daily_best_match, daily_models), the
           functions the training's daily files are made with
  market   every bucket's book at each tick, priced as the price record prices
           it (live_price)
FROM THE REPOSITORY (history the live system reads, not live input): the
station's whole past days (station_daily: climatology, yesterday, the forecast's
past error), the daily forecasts of the days before the event's own (the past
error), the cities and their zones, read by build_table's own loaders.

TWO WAYS OF KNOWING, each a filter on the record:
  rule     the whole record, whenever each value arrived; the training's own
           rules (REPORT_LAG_S, PUBLISH_H, the market at or before t) decide
           what a row may know. The row the builder would make from the live
           sources: parity compares it with the table's row.
  arrival  only what had reached the tick by t (captured_at <= t), then the
           same rules: what the live system actually had at the decision. Its
           difference from `rule` is the cost of the hourly :36 tick against
           the rule's instants (a report the rule knows 20 min after its
           reading reaches the tick at the next :36).

THE MARKET PRICE (step 2.1, extended 12 Oct): the book's midpoint, an empty bid
counted as 0 and an empty ask as 1. On the archived 2-hourly snapshots matched to
the price record's hourly point at hours p did not move (what_is_p.py's control,
its "one-sided" lines, 12 Oct), a book with no bid gives ask/2 within 0.005 of p
on 99.95% of 148,144 pairs, and a book with no ask gives (bid + 1)/2 within
0.005 on 99.90% of 3,067; either side alone is further off (93.67% and 98.04%).
Step 2.1 chose the midpoint for two-sided books (6 Oct: 54,415 pairs, 98.6%
within 0.01).

KNOWN SOURCE DIFFERENCES, not hidden here (the parity test counts them):
  - the tick asks IEM with trace=empty, the training's fetch with trace=T, so a
    trace of precipitation is 0.0 in the table and missing live;
  - Lucknow's hours sit at :30 UTC in the training's hourly file (built with
    timezone=auto) and at :00 in the record (asked in GMT);
  - a day's daily row is kept only with MIN_HOURS hourly temperatures
    (honest_record): the record holds lead-1 hours 17 h ahead, so at a local
    hour before 02:00 of D fewer than 20 of D's hours are there and the row
    falls back to lead 2, where the table, built after the day, has lead 1.

    python tools/wxpredict/live_reader.py --from 2026-10-13 --to 2026-10-15 \\
        [--mode rule|arrival|both] [--out rows.csv.gz] [--record FILE ...]
"""
import argparse
import bisect
import collections
import csv
import datetime as dt
import glob
import gzip
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                "scripts"))
from wxpredict import common, features  # noqa: E402
from wxpredict.fetch_obs import FIELDS as REPORT_FIELDS, remarks  # noqa: E402
import honest_record  # noqa: E402

MODES = ("rule", "arrival")
KINDS = ("report", "hourly", "hourly2", "models1", "models2", "books")
HOURLY_KIND = {1: "hourly", 2: "hourly2"}
MODELS_KIND = {1: "models1", 2: "models2"}
# what the record's report payload holds, in order (scripts/live_capture.py
# FULL_FIELDS: the training's fields and the METAR text)
PAYLOAD_FIELDS = REPORT_FIELDS + ["metar"]
RECORD_GLOBS = ("data/archive/live_capture/*.csv.gz", "data/mirror/live_capture/*.csv.gz")
WIDE_SPREAD = 0.10        # step 2.1: up to this spread the midpoint is the price
COLUMNS = ["live_mode", *features.COLUMNS, "mkt_spreads", "mkt_book_age_min", "books_seen"]


def unix(stamp):
    """Seconds since the epoch of a timestamp as Postgres or Python writes it."""
    s = str(stamp).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif len(s) > 3 and s[-3] in "+-" and s[-6] != ":" and ":" not in s[-3:]:
        s += ":00"                                     # '+00' -> '+00:00'
    return dt.datetime.fromisoformat(s).timestamp()


def truthy(x):
    return str(x).strip().lower() in ("true", "t", "1")


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------
class Record:
    """Every row of the live record, by kind and key, oldest first. A row the
    mirror and the archive both hold is read once."""

    def __init__(self, rows):
        self.kinds = {k: collections.defaultdict(list) for k in KINDS}
        seen = set()
        for kind, key, at, payload in rows:
            if kind not in self.kinds:
                raise ValueError(f"{kind}: not a kind the live record keeps")
            p = json.loads(payload) if isinstance(payload, str) else payload
            at = unix(at) if not isinstance(at, (int, float)) else float(at)
            sig = (kind, key, at, json.dumps(p, sort_keys=True))
            if sig in seen:
                continue
            seen.add(sig)
            self.kinds[kind][key].append((at, p))
        for by_key in self.kinds.values():
            for vals in by_key.values():
                vals.sort(key=lambda x: x[0])
        ticks = [at for at, _ in self.kinds["books"].get("all", [])]
        self.first = min(ticks) if ticks else None
        self.last = max(ticks) if ticks else None

    def values(self, kind, t=None):
        """{key: payload}: each key's newest value captured at or before t, or
        its newest of all when t is None."""
        out = {}
        for key, vals in self.kinds[kind].items():
            if t is None:
                out[key] = vals[-1][1]
                continue
            i = bisect.bisect_right([a for a, _ in vals], t) - 1
            if i >= 0:
                out[key] = vals[i][1]
        return out

    def books(self):
        """[(captured_at, payload)] of every tick's books, oldest first."""
        return self.kinds["books"].get("all", [])


def read_record(paths):
    """The rows of the record files given (the mirror's and the archive's CSVs,
    columns kind, key, captured_at, payload)."""
    rows = []
    for path in paths:
        with gzip.open(path, "rt", newline="") as f:
            for r in csv.DictReader(f):
                rows.append((r["kind"], r["key"], r["captured_at"], r["payload"]))
    return rows


def record_files(root=None):
    root = root or common.ROOT
    return sorted(p for g in RECORD_GLOBS for p in glob.glob(os.path.join(root, g)))


# ---------------------------------------------------------------------------
# station reports
# ---------------------------------------------------------------------------
def report_row(key, payload):
    """(IEM station id, the training's report row) from one recorded report:
    fetch_obs.cmd_reports' own transformation of IEM's answer."""
    station, valid = key.split("|", 1)
    rec = dict(zip(PAYLOAD_FIELDS, payload))
    row = {"valid": valid.strip().replace(" ", "T") + "Z"}
    for f in REPORT_FIELDS:
        row[f] = (rec.get(f) or "").strip()
    row.update(zip(("t10_c", "td10_c", "max6_c", "min6_c"), remarks(rec.get("metar"))))
    return station.strip().upper(), row


def station_reports(record, icao_of, t=None):
    """{ICAO: features.Reports} of every station the record answered for, as
    known at t (every report when t is None). `icao_of` maps IEM's id to ICAO."""
    by = collections.defaultdict(list)
    for key, payload in record.values("report", t).items():
        st, row = report_row(key, payload)
        icao = icao_of.get(st)
        if icao:
            by[icao].append(row)
    return {s: features.Reports(sorted(rows, key=lambda r: r["valid"])) for s, rows in by.items()}


# ---------------------------------------------------------------------------
# forecasts
# ---------------------------------------------------------------------------
def _by_city(record, kind, t):
    out = collections.defaultdict(dict)
    for key, vals in record.values(kind, t).items():
        city, hour = key.split("|", 1)
        out[city][hour] = vals
    return out


def hourly_series(record, t=None):
    """{city: (unix hours, rows)} of best_match `_previous_day1`, oldest first:
    build_table.load_hourly_forecasts' shape."""
    out = {}
    for city, hours in _by_city(record, "hourly", t).items():
        stamps = sorted(hours)
        out[city] = ([features.unix_of(h) for h in stamps],
                     [tuple(features.num(v) for v in hours[h]) for h in stamps])
    return out


def daily_rows(record, tz_names, t=None):
    """(bm, md) in build_table.load_daily_forecasts' shapes for every local day
    the record's hourly values cover, at leads 1 and 2: the nightly record's
    own reduction of the hourly values (honest_record.daily_best_match,
    daily_models), so a day with fewer than MIN_HOURS temperatures has no row."""
    bm, md = {}, collections.defaultdict(dict)
    for lead in (1, 2):
        for city, hours in _by_city(record, HOURLY_KIND[lead], t).items():
            tz = tz_names.get(city)
            if not tz:
                continue
            times = sorted(hours)
            answer = {"time": times}
            for i, v in enumerate(honest_record.HEATING):
                answer[f"{v}_previous_day{lead}"] = [hours[h][i] for h in times]
            for d, vals in honest_record.daily_best_match(answer, lambda v: f"{v}_previous_day{lead}", tz).items():
                bm[(city, lead, d)] = dict(zip(honest_record.BEST_MATCH_HEADER[3:], vals))
        for city, hours in _by_city(record, MODELS_KIND[lead], t).items():
            tz = tz_names.get(city)
            if not tz:
                continue
            times = sorted(hours)
            answer = {"time": times}
            for j, m in enumerate(honest_record.MODELS):
                answer[f"temperature_2m_previous_day{lead}_{m}"] = [hours[h][j] for h in times]
            for (m, d), (tmax, early) in honest_record.daily_models(
                    answer, lambda m: f"temperature_2m_previous_day{lead}_{m}", tz).items():
                md[(city, lead, d)][m] = (features.num(tmax), features.num(early))
    return bm, dict(md)


# ---------------------------------------------------------------------------
# the market
# ---------------------------------------------------------------------------
def live_price(bid, ask):
    """The price record's price of one book (module docstring): the midpoint,
    an empty bid counted as 0 and an empty ask as 1; None with neither."""
    if bid is None and ask is None:
        return None
    return ((0.0 if bid is None else bid) + (1.0 if ask is None else ask)) / 2


def book_series(record):
    """{band_id: (unix ticks, prices, spreads)}: every bucket's price at each
    tick that read its book (a bucket the tick did not get is absent from that
    tick, so its last price ages)."""
    out = collections.defaultdict(lambda: ([], [], []))
    for at, payload in record.books():
        for band, top in (payload.get("tops") or {}).items():
            bid, ask = top[0], top[1]
            p = live_price(bid, ask)
            if p is None:
                continue
            ts, ps, sp = out[band]
            ts.append(at)
            ps.append(p)
            sp.append((1.0 if ask is None else ask) - (0.0 if bid is None else bid))
    return dict(out)


def ladder_series(books, band_ids):
    """[(ts, ps)] per bucket of one ladder, lowest first: features' price
    series shape (build_table.load_prices)."""
    return [(books.get(b, ([], [], []))[0], books.get(b, ([], [], []))[1]) for b in band_ids]


def spreads_at(books, band_ids, t):
    out = []
    for b in band_ids:
        ts, _, sp = books.get(b, ([], [], []))
        i = bisect.bisect_right(ts, t) - 1
        out.append(sp[i] if i >= 0 else None)
    return out


# ---------------------------------------------------------------------------
# events
# ---------------------------------------------------------------------------
def read_definitions(root=None):
    """(markets, bands) as lists of dicts: the mirror's rows, and for the
    markets it does not hold yet the nightly definitions file's
    (data/training/market_definitions; ask_backtest.read_definitions' rule)."""
    root = root or common.ROOT
    out = {}
    for table, key in (("markets", "market_id"), ("bands", "band_id")):
        rows = {}
        for path in sorted(glob.glob(os.path.join(root, f"data/mirror/{table}/*.csv.gz"))):
            for r in common.read_csv(path):
                rows[r[key]] = r
        for path in sorted(glob.glob(os.path.join(root, f"data/training/market_definitions/{table}-*.csv.gz"))):
            for r in common.read_csv(path):
                rows.setdefault(r[key], r)
        out[table] = list(rows.values())
    return out["markets"], out["bands"]


def live_events(markets, bands, cities, first, last):
    """[event] in build_table.load_events' shape for every market of an active
    city dated first..last (ISO dates) whose ladder is whole and contiguous; its
    identity is the market, its station the city's, its bucket ids kept in
    `band_ids` and its winner unknown (None)."""
    by_market = collections.defaultdict(list)
    for b in bands:
        by_market[b["market_id"]].append(b)
    out = []
    for m in markets:
        c = cities.get(m["city_key"])
        if c is None or (c.get("status") or "active") != "active" or not c.get("icao"):
            continue
        if not (first <= m["resolution_date"] <= last):
            continue
        bs = sorted(by_market.get(m["market_id"], []), key=lambda b: int(b["band_index"]))
        lad = [(features.num(b["band_lo"]), features.num(b["band_hi"]), truthy(b["open_low"]), truthy(b["open_high"]))
               for b in bs]
        if len(lad) < 2 or not lad[0][2] or not lad[-1][3] or any(a[1] != b[0] for a, b in zip(lad, lad[1:])):
            continue
        out.append({"event_id": m["market_id"], "source": "venue", "city": m["city_key"],
                    "date": m["resolution_date"], "unit": m.get("unit") or c.get("unit") or "C",
                    "station": c["icao"].upper(), "bands": [(lo, hi) for lo, hi, _, _ in lad], "winner": None,
                    "band_ids": [b["band_id"] for b in bs]})
    out.sort(key=lambda e: (e["date"], e["city"], e["event_id"]))
    return out


# ---------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------
def instants(events, cities, first_t, last_t):
    """{t: [(event, day offset, local)]}: every decision instant of the events
    between first_t and last_t (unix seconds), by the instant."""
    out = collections.defaultdict(list)
    for e in events:
        tz = common.zone(cities[e["city"]])
        for off, local in features.decision_instants(dt.date.fromisoformat(e["date"]), tz):
            t = int(local.timestamp()) + features.SNAPSHOT_S
            if first_t <= t <= last_t:
                out[t].append((e, off, local))
    return dict(sorted(out.items()))


class OwnDay:
    """The daily forecasts one event's row reads: its own day from the record,
    every other day from the repository (Codex on #387). features reads them
    with .get only (pick_daily for the event's day, bias_features for the
    days before it), so the past error is the repository's whatever the
    record holds: a day at the record's edge with 20-23 of its hours would
    otherwise stand in for the whole archived day."""

    def __init__(self, live, history, city, day):
        self.live, self.history, self.city, self.day = live, history, city, day

    def get(self, key, default=None):
        src = self.live if key[0] == self.city and key[2] == self.day else self.history
        return src.get(key, default)


class View:
    """The live sources as one way of knowing sees them at t (rule: t None)."""

    def __init__(self, record, icao_of, tz_names, t=None):
        self.reports = station_reports(record, icao_of, t)
        self.hourly = hourly_series(record, t)
        self.bm, self.md = daily_rows(record, tz_names, t)


def build(record, events, cities, sdays, history_bm, history_md, first_t, last_t, modes=MODES):
    """[row dict]: every decision instant of the events between first_t and
    last_t, one row per way of knowing, each made by features.decision_row."""
    icao_of = {common.iem_id(c["icao"]): c["icao"].upper() for c in cities.values() if c.get("icao")}
    tz_names = {k: c["timezone"] for k, c in cities.items() if c.get("timezone")}
    books = book_series(record)
    ticks = [at for at, _ in record.books()]
    empty = features.Reports([])
    rows = []
    rule = View(record, icao_of, tz_names) if "rule" in modes else None
    for t, todo in instants(events, cities, first_t, last_t).items():
        views = {}
        if rule is not None:
            views["rule"] = rule
        if "arrival" in modes:
            views["arrival"] = View(record, icao_of, tz_names, t)
        for e, off, local in todo:
            tz = common.zone(cities[e["city"]])
            series = ladder_series(books, e["band_ids"])
            st_days = sdays.get(e["station"], {})
            for mode, v in views.items():
                rep = v.reports.get(e["station"], empty)
                ctx = features.event_context(e, rep, st_days, tz)
                bm = OwnDay(v.bm, history_bm, e["city"], e["date"])
                md = OwnDay(v.md, history_md, e["city"], e["date"])
                row = features.decision_row(e, ctx, off, local, rep, st_days, bm, md, v.hourly, series, tz)
                row["live_mode"] = mode
                sp = spreads_at(books, e["band_ids"], t)
                row["mkt_spreads"] = ";".join("" if s is None else features.fmt(s, 4) for s in sp)
                k = bisect.bisect_right(ticks, t)
                row["mkt_book_age_min"] = (t - ticks[k - 1]) / 60 if k else None   # the newest tick's
                row["books_seen"] = k
                rows.append(row)
    return rows


def write_rows(path, rows):
    out = ([r.get(c) if isinstance(r.get(c), str) else features.fmt(r.get(c)) for c in COLUMNS]
           for r in sorted(rows, key=lambda r: (r["date"], r["city_key"], r["decision_utc"], r["live_mode"])))
    return common.write_csv(path, COLUMNS, out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="first", required=True, help="first event date (ISO)")
    ap.add_argument("--to", dest="last", required=True, help="last event date (ISO)")
    ap.add_argument("--mode", choices=("rule", "arrival", "both"), default="both")
    ap.add_argument("--record", nargs="*", help="record files (default: the mirror's and the archive's)")
    ap.add_argument("--out", default=os.path.join(common.WX, "live", "live_rows.csv.gz"))
    args = ap.parse_args(argv)
    from wxpredict import build_table
    record = Record(read_record(args.record or record_files()))
    if record.first is None:
        raise SystemExit("the record holds no books: nothing to read")
    cities = common.cities()
    markets, bands = read_definitions()
    events = live_events(markets, bands, cities, args.first, args.last)
    sdays, _ = build_table.load_station_daily()
    bm, md = build_table.load_daily_forecasts()
    modes = MODES if args.mode == "both" else (args.mode,)
    rows = build(record, events, cities, sdays, bm, md, record.first, record.last, modes)
    n = write_rows(os.path.abspath(args.out), rows)      # 'rows.csv.gz' has no directory (Codex on #387)
    filled = collections.Counter(c for r in rows for c in features.COLUMNS if r.get(c) not in (None, ""))
    print(json.dumps({"rows": n, "events": len(events), "record": [record.first, record.last],
                      "by_mode": dict(collections.Counter(r["live_mode"] for r in rows)),
                      "filled": {c: filled[c] for c in features.COLUMNS}}, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
