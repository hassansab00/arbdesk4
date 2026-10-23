"""Build forecast rows from receipt-stamped live inputs and committed history.

Arrival mode only uses inputs captured by the decision time. Rule mode supports
feature parity checks under the model's publication allowances. Contract-authority
receipts separately constrain the qualifying maximum.
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
import contract_inputs  # noqa: E402
import metar_contract  # noqa: E402

MODES = ("rule", "arrival")
# every kind the record keeps; 'focus_book' (WP5, the focus cities' books each
# minute) is read by the same-day pilot, not by these rows
KINDS = ("report", "hourly", "hourly2", "models1", "models2", "books", "focus_book", "wx_features", "hourly0", "models0", "awc_metar")
HOURLY_KIND = {1: "hourly", 2: "hourly2"}
MODELS_KIND = {1: "models1", 2: "models2"}
# what the record's report payload holds, in order (scripts/live_capture.py
# FULL_FIELDS: the training's fields and the METAR text)
PAYLOAD_FIELDS = REPORT_FIELDS + ["metar"]
RECORD_GLOBS = ("data/archive/live_capture/*.csv.gz", "data/mirror/live_capture/*.csv.gz")
WIDE_SPREAD = 0.10        # step 2.1: up to this spread the midpoint is the price
COLUMNS = ["live_mode", *features.COLUMNS, "mkt_spreads", "mkt_sides", "mkt_book_age_min", "books_seen"]


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

    def authority(self, key, t):
        vals = self.kinds[contract_inputs.KIND].get(key, [])
        index = bisect.bisect_right([at for at, _ in vals], t)-1
        return vals[index] if index >= 0 else None

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
    if isinstance(payload, dict):
        if payload.get("schema") != "iem-report-set-v1":
            raise ValueError("unknown station report schema")
        rows, selected = payload.get("rows"), payload.get("selected")
        if not isinstance(rows, list) or not rows or type(selected) is not int or not 0 <= selected < len(rows):
            raise ValueError("invalid station report set")
        payload = rows[selected]
    if not isinstance(payload, list) or len(payload) != len(PAYLOAD_FIELDS):
        raise ValueError("invalid station report fields")
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
        if kind in ("hourly0", "models0") and isinstance(vals, dict):
            if vals.get("schema") != "nwp-current-snapshot-v1" or not isinstance(vals.get("hours"), dict):
                raise ValueError("invalid current forecast snapshot")
            out[city].update(vals["hours"])
        else:
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
    """{band_id: (unix ticks, prices, spreads, sides)}: every bucket's price at
    each tick that read its book (a bucket the tick did not get is absent from
    that tick, so its last price ages). A one-sided book's spread counts the
    empty side as 0 or 1, so its sides ('ba' both, 'b' a bid only, 'a' an ask
    only) are kept beside it: a spread alone cannot tell an ask-only book at
    0.05 from a two-sided one (Codex on #391)."""
    out = collections.defaultdict(lambda: ([], [], [], []))
    for at, payload in record.books():
        for band, top in (payload.get("tops") or {}).items():
            bid, ask = top[0], top[1]
            p = live_price(bid, ask)
            if p is None:
                continue
            ts, ps, sp, sd = out[band]
            ts.append(at)
            ps.append(p)
            sp.append((1.0 if ask is None else ask) - (0.0 if bid is None else bid))
            sd.append("ba" if bid is not None and ask is not None else "b" if bid is not None else "a")
    return dict(out)


def ladder_series(books, band_ids):
    """[(ts, ps)] per bucket of one ladder, lowest first: features' price
    series shape (build_table.load_prices)."""
    return [(books.get(b, ([], [], [], []))[0], books.get(b, ([], [], [], []))[1]) for b in band_ids]


def spreads_at(books, band_ids, t, which=2):
    """Each bucket's spread (which=2) or sides (which=3) at its newest tick at
    or before t; None for a bucket with no book by then."""
    out = []
    for b in band_ids:
        entry = books.get(b, ([], [], [], []))
        i = bisect.bisect_right(entry[0], t) - 1
        out.append(entry[which][i] if i >= 0 else None)
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
                    "station": (m.get('contract_station') or c["icao"]).upper(), "bands": [(lo, hi) for lo, hi, _, _ in lad], "winner": None,
                    "band_ids": [b["band_id"] for b in bs],
                    "observation_contract":m.get('observation_contract','legacy'),
                    "rules_sha256":m.get('rules_sha256'),"rules_text":m.get('rules_text'),
                    "contract_station":m.get('contract_station')})
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
        self.record = record
        self.reports = station_reports(record, icao_of, t)
        self.hourly = hourly_series(record, t)
        self.bm, self.md = daily_rows(record, tz_names, t)


def build(record, events, cities, sdays, history_bm, history_md, first_t, last_t, modes=MODES, report_lag_s=None):
    """[row dict]: every decision instant of the events between first_t and
    last_t, one row per way of knowing, each made by features.decision_row.
    report_lag_s: when a report is known after its reading, as the table the
    rows are compared with or scored by was built (v1's 20 min when None; the
    v2 table's features.REPORT_LAG_V2_S)."""
    with features.report_lag(features.REPORT_LAG_S if report_lag_s is None else report_lag_s):
        return _build(record, events, cities, sdays, history_bm, history_md, first_t, last_t, modes)


def _build(record, events, cities, sdays, history_bm, history_md, first_t, last_t, modes):
    icao_of = {common.iem_id(c["icao"]): c["icao"].upper() for c in cities.values() if c.get("icao")}
    tz_names = {k: c["timezone"] for k, c in cities.items() if c.get("timezone")}
    books = book_series(record)
    ticks = [at for at, _ in record.books()]
    rows = []
    rule = View(record, icao_of, tz_names) if "rule" in modes else None
    for t, todo in instants(events, cities, first_t, last_t).items():
        views = {}
        if rule is not None:
            views["rule"] = rule
        if "arrival" in modes:
            views["arrival"] = View(record, icao_of, tz_names, t)
        for e, off, local in todo:
            for mode, v in views.items():
                rows.append(_row(e, off, local, t, mode, v, books, ticks, cities, sdays, history_bm, history_md))
    return rows


def _row(e, off, local, t, mode, v, books, ticks, cities, sdays, history_bm, history_md):
    """One event's row at one instant, as one way of knowing (`v`) sees it."""
    tz = common.zone(cities[e["city"]])
    st_days = sdays.get(e["station"], {})
    rep = v.reports.get(e["station"], features.Reports([]))
    ctx = features.event_context(e, rep, st_days, tz)
    bm = OwnDay(v.bm, history_bm, e["city"], e["date"])
    md = OwnDay(v.md, history_md, e["city"], e["date"])
    qualifying = None
    if e.get('observation_contract','legacy') != 'legacy':
        qualifying = {}
        receipt = v.record.authority(e['city']+'|'+e['date'], t)
        if receipt and e.get('observation_contract') == contract_inputs.CONTRACT:
            received, payload = receipt
            market = dict(city_key=e['city'],resolution_date=e['date'],unit=e['unit'],rules_sha256=e.get('rules_sha256'),
                          contract_station=e.get('contract_station'))
            qualifying = contract_inputs.qualifying(payload,dt.datetime.fromtimestamp(received,dt.timezone.utc).isoformat(),
                market,cities[e['city']],dt.datetime.fromtimestamp(t,dt.timezone.utc))
            if qualifying:
                raw=gzip.decompress(__import__('base64').b64decode(payload['raw_gzip']))
                observations=metar_contract.raw_reports(raw,e['station'],dt.datetime.fromtimestamp(received,dt.timezone.utc),strict=False)
                weather=[]
                for observation in observations:
                    temp=observation['weather_temp_c']
                    if temp is None: continue
                    r={name:'' for name in REPORT_FIELDS}
                    r.update(valid=observation['when'].isoformat(),tmpf=str(temp*9/5+32),t10_c=str(temp),
                             td10_c='',max6_c='',min6_c='')
                    weather.append(r)
                authority_reports=features.Reports(weather)
                # Retain richer same-station IEM fields at shared instants;
                # use AWC temperature reports where IEM is absent or late.
                merged=features.Reports([])
                readings=dict(zip(authority_reports.t,authority_reports.rec))
                readings.update(zip(rep.t,rep.rec))
                merged.t=sorted(readings)
                merged.rec=[readings[at] for at in merged.t]
                minutes=collections.Counter((at//60)%60 for at in merged.t)
                merged.routine_minute=minutes.most_common(1)[0][0] if minutes else 0
                rep=merged
    row = features.decision_row(e, ctx, off, local, rep, st_days, bm, md, v.hourly,
                                ladder_series(books, e["band_ids"]), tz,qualifying=qualifying)
    if qualifying is not None:
        row['observation_contract'] = e.get('observation_contract')
        row['authority_input'] = qualifying
    row["live_mode"] = mode
    sp = spreads_at(books, e["band_ids"], t)
    row["mkt_spreads"] = ";".join("" if s is None else features.fmt(s, 4) for s in sp)
    row["mkt_sides"] = ";".join(s or "" for s in spreads_at(books, e["band_ids"], t, which=3))
    k = bisect.bisect_right(ticks, t)
    row["mkt_book_age_min"] = (t - ticks[k - 1]) / 60 if k else None   # the newest tick's
    row["books_seen"] = k
    return row


def row_at(record, e, cities, sdays, history_bm, history_md, t, report_lag_s=None):
    """The event's arrival-mode row at any instant t (unix seconds), not only
    the table's on-the-hour ones: what the live system had at t (captured_at
    <= t), then the training's rules with the given report lag. The decision
    is taken SNAPSHOT_S after `local`, as every row's is, so decision_utc is t
    (to the second). The live shadow (wxrd_live.py) asks for the instant the
    tick priced its call at. None when t is not on the event's day or the day
    before, on the city's clock."""
    tz = common.zone(cities[e["city"]])
    local = dt.datetime.fromtimestamp(int(t) - features.SNAPSHOT_S, tz)
    off = (local.date() - dt.date.fromisoformat(e["date"])).days
    if off not in (-1, 0):
        return None
    icao_of = {common.iem_id(c["icao"]): c["icao"].upper() for c in cities.values() if c.get("icao")}
    tz_names = {k: c["timezone"] for k, c in cities.items() if c.get("timezone")}
    with features.report_lag(features.REPORT_LAG_S if report_lag_s is None else report_lag_s):
        v = View(record, icao_of, tz_names, int(t))
        return _row(e, off, local, int(t), "arrival", v, book_series(record), [at for at, _ in record.books()],
                    cities, sdays, history_bm, history_md)


def write_rows(path, rows):
    out = ([r.get(c) if isinstance(r.get(c), str) else features.fmt(r.get(c)) for c in COLUMNS]
           for r in sorted(rows, key=lambda r: (r["date"], r["city_key"], r["decision_utc"], r["live_mode"])))
    return common.write_csv(path, COLUMNS, out)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="first", required=True, help="first event date (ISO)")
    ap.add_argument("--to", dest="last", required=True, help="last event date (ISO)")
    ap.add_argument("--mode", choices=("rule", "arrival", "both"), default="both")
    ap.add_argument("--report-lag-s", type=int, default=features.REPORT_LAG_S,
                    help="match the training contract (v1: 1200, v2: 480)")
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
    rows = build(record, events, cities, sdays, bm, md, record.first, record.last, modes,
                 report_lag_s=args.report_lag_s)
    n = write_rows(os.path.abspath(args.out), rows)      # 'rows.csv.gz' has no directory (Codex on #387)
    filled = collections.Counter(c for r in rows for c in features.COLUMNS if r.get(c) not in (None, ""))
    print(json.dumps({"rows": n, "events": len(events), "record": [record.first, record.last],
                      "by_mode": dict(collections.Counter(r["live_mode"] for r in rows)),
                      "filled": {c: filled[c] for c in features.COLUMNS}}, indent=1), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
