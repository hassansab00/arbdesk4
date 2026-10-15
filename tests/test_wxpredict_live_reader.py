"""WXPredict WP3b, the live reader (tools/wxpredict/live_reader.py): a decision's
features from the live record, as known then, made by the training's own
functions. The main test builds one city's days twice - once as the training's
sources, once as the live record - and requires the live row to be the table's
row, column for column; the rest pin each conversion and the two ways of
knowing."""
import datetime as dt
import json
import math
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tools"))

import honest_record  # noqa: E402
import live_capture  # noqa: E402
from wxpredict import features, fetch_obs  # noqa: E402
from wxpredict import live_reader as lr  # noqa: E402

UTC = dt.timezone.utc
CITY = "madrid"
ICAO = "LEMD"
TZ = "Europe/Madrid"
DAY = dt.date(2026, 10, 14)
CITIES = {CITY: {"city_key": CITY, "icao": ICAO, "timezone": TZ, "status": "active", "unit": "C"}}
BANDS = [(None, 22.0), (22.0, 23.0), (23.0, 24.0), (24.0, 25.0), (25.0, None)]


def test_the_payload_is_what_the_tick_keeps():
    assert lr.PAYLOAD_FIELDS == live_capture.FULL_FIELDS
    assert lr.KINDS == ("report", "hourly", "hourly2", "models1", "models2", "books", "focus_book", "wx_features", "hourly0", "models0")
    mig = (ROOT / "supabase" / "migrations" / "20261015174914_current_forecast_capture.sql").read_text()
    for kind in lr.KINDS:
        assert f"'{kind}'" in mig, f"{kind}: the newest kind list keeps it"
    assert lr.HOURLY_KIND == {lead: live_capture.HOURLY_KIND[lead] for lead in (1, 2)}
    assert lr.MODELS_KIND == {lead: live_capture.MODELS_KIND[lead] for lead in (1, 2)}
    assert live_capture.HOURLY_KIND[0] == "hourly0" and live_capture.MODELS_KIND[0] == "models0"


@pytest.mark.parametrize("stamp", ["2026-10-12 08:36:12.5+00", "2026-10-12T08:36:12.5+00:00",
                                   "2026-10-12T08:36:12.5Z"])
def test_both_writers_times_read_the_same(stamp):
    assert lr.unix(stamp) == dt.datetime(2026, 10, 12, 8, 36, 12, 500000, tzinfo=UTC).timestamp()


# ---------------------------------------------------------------------------
# conversions
# ---------------------------------------------------------------------------
def metar(at, t10, td10):
    return (f"{ICAO} {at:%d%H%M}Z 27005KT 9999 FEW040 {round(t10 / 10):02d}/{round(td10 / 10):02d} Q1015 "
            f"RMK T0{t10:03d}0{td10:03d}")


def iem_rec(at, t10, td10):
    """One report as IEM answers it (the fields fetch_obs asks for)."""
    tmpf = round((t10 / 10) * 9 / 5 + 32, 1)
    vals = {"tmpf": str(tmpf), "dwpf": str(round((td10 / 10) * 9 / 5 + 32, 1)), "relh": "60.0", "drct": "270.00",
            "sknt": "5.00", "gust": "", "p01i": "0.00", "alti": "29.98", "mslp": "1015.0", "vsby": "6.21",
            "skyc1": "FEW", "skyc2": "", "skyc3": "", "skyl1": "4000.00", "skyl2": "", "skyl3": "",
            "wxcodes": "", "metar": metar(at, t10, td10)}
    return vals


def training_report_row(at, rec):
    """fetch_obs.cmd_reports' own transformation of one IEM row."""
    valid = at.strftime("%Y-%m-%d %H:%M").replace(" ", "T") + "Z"
    return dict(zip(fetch_obs.REPORT_HEADER,
                    [valid, *[(rec.get(k) or "").strip() for k in fetch_obs.FIELDS], *fetch_obs.remarks(rec["metar"])]))


def test_a_recorded_report_is_the_trainings_row():
    at = dt.datetime(2026, 10, 13, 12, 30, tzinfo=UTC)
    rec = iem_rec(at, 215, 102)
    payload = [rec[f] for f in live_capture.FULL_FIELDS]
    station, row = lr.report_row(f"{ICAO}|2026-10-13 12:30", payload)
    assert station == ICAO
    assert row == training_report_row(at, rec)
    assert row["t10_c"] == 21.5 and row["td10_c"] == 10.2, "the T group, not the whole degrees"


def test_the_record_reads_a_row_once_and_knows_when():
    rows = [("hourly", "madrid|2026-10-13T12:00", "2026-10-12 19:36:05+00", "[20.0]"),
            ("hourly", "madrid|2026-10-13T12:00", "2026-10-12T19:36:05+00:00", "[20.0]"),     # the mirror's copy
            ("hourly", "madrid|2026-10-13T12:00", "2026-10-12 20:36:05+00", "[21.0]")]
    rec = lr.Record(rows)
    assert len(rec.kinds["hourly"]["madrid|2026-10-13T12:00"]) == 2
    assert rec.values("hourly", lr.unix("2026-10-12 20:00:00+00")) == {"madrid|2026-10-13T12:00": [20.0]}
    assert rec.values("hourly", lr.unix("2026-10-12 19:00:00+00")) == {}
    assert rec.values("hourly") == {"madrid|2026-10-13T12:00": [21.0]}
    with pytest.raises(ValueError):
        lr.Record([("trades", "x", "2026-10-12 19:36:05+00", "[]")])


def test_a_books_sides_are_kept_beside_its_spread():
    """A one-sided book's spread counts the empty side as 0 or 1, so an
    ask-only book at 0.05 has a spread of 0.05; its sides say so (Codex on #391)."""
    payload = json.dumps({"tops": {"two": [0.40, 0.43], "ask_only": [None, 0.05], "bid_only": [0.95, None]}})
    rec = lr.Record([("books", "all", "2026-10-13 08:36:05+00", payload)])
    books = lr.book_series(rec)
    t = lr.unix("2026-10-13 09:00:00+00")
    ids = ["two", "ask_only", "bid_only", "none"]
    assert lr.spreads_at(books, ids, t) == [pytest.approx(0.03), pytest.approx(0.05), pytest.approx(0.05), None]
    assert lr.spreads_at(books, ids, t, which=3) == ["ba", "a", "b", None]
    assert lr.spreads_at(books, ids, lr.unix("2026-10-13 08:00:00+00"), which=3) == [None] * 4, "nothing before the tick"
    assert "mkt_sides" in lr.COLUMNS


def test_the_live_price_is_the_price_records():
    assert lr.live_price(0.41, 0.43) == pytest.approx(0.42)
    assert lr.live_price(None, 0.001) == pytest.approx(0.0005), "no bid: half the ask (99.95% of 148,144)"
    assert lr.live_price(0.999, None) == pytest.approx(0.9995), "no ask: halfway to 1 (99.90% of 3,067)"
    assert lr.live_price(None, None) is None


# ---------------------------------------------------------------------------
# one city's days, as the training's sources and as the live record
# ---------------------------------------------------------------------------
def temp10(at):
    """Tenths of a degree C: a daily cycle peaking mid-afternoon UTC, rising a little a day."""
    h = at.hour + at.minute / 60
    return int(round(10 * (18 + 0.6 * (at.day - 12) + 5 * math.sin((h - 9) / 24 * 2 * math.pi))))


def hourly_vals(at, lead):
    t = temp10(at) / 10 + (0.3 if lead == 2 else 0.0)
    return [round(t, 1), round(t - 7, 1), 40, max(0.0, round(600 * math.sin((at.hour - 6) / 12 * math.pi), 1)),
            12.0, 0.0, 1015.0]


def model_vals(at, lead):
    return [round(temp10(at) / 10 + 0.2 * k - (0.1 if lead == 2 else 0.0), 1) for k in range(len(honest_record.MODELS))]


def prices_at(at):
    """A ladder that drifts: the price of each band at a tick."""
    w = (at.hour % 6) / 100
    return [0.05, 0.15 + w, 0.40 - w, 0.30, 0.10]


@pytest.fixture(scope="module")
def city_days():
    start = dt.datetime(2026, 10, 12, 0, tzinfo=UTC)
    hours = [start + dt.timedelta(hours=h) for h in range(4 * 24)]
    reports = [start + dt.timedelta(minutes=30 * k) for k in range(3 * 48 + 2)]
    ticks = [start + dt.timedelta(hours=h, minutes=36) for h in range(3 * 24)]
    # the training's sources
    rows = [training_report_row(at, iem_rec(at, temp10(at), temp10(at) - 70)) for at in reports]
    rep = features.Reports(rows)
    hourly = {CITY: ([int(h.timestamp()) for h in hours], [tuple(hourly_vals(h, 1)) for h in hours])}
    stamps = [h.strftime("%Y-%m-%dT%H:%M") for h in hours]
    heating = {"hourly": {"time": stamps, **{f"{v}_previous_day{lead}": [hourly_vals(h, lead)[i] for h in hours]
                                             for i, v in enumerate(honest_record.HEATING) for lead in (1, 2)}}}
    models = {"hourly": {"time": stamps,
                         **{f"temperature_2m_previous_day{lead}_{m}": [model_vals(h, lead)[j] for h in hours]
                            for j, m in enumerate(honest_record.MODELS) for lead in (1, 2)}}}
    bm_rows, md_rows = honest_record.previous_rows(CITY, heating, models, TZ)
    # build_table.load_daily_forecasts reads these rows back from the files' text
    bm = {(r[0], int(r[1]), r[2]): dict(zip(honest_record.BEST_MATCH_HEADER, honest_record._text(r)))
          for r in bm_rows}
    md = {}
    for r in md_rows:
        txt = honest_record._text(r)
        md.setdefault((r[0], int(r[1]), r[3]), {})[r[2]] = (features.num(txt[4]), features.num(txt[5]))
    series = [([int(t.timestamp()) for t in ticks], [prices_at(t)[b] for t in ticks]) for b in range(len(BANDS))]
    # the same days as the live record
    record = []
    for at in reports:
        rec = iem_rec(at, temp10(at), temp10(at) - 70)
        record.append(("report", f"{ICAO}|{at:%Y-%m-%d %H:%M}", (at + dt.timedelta(minutes=12)).timestamp(),
                       [rec[f] for f in live_capture.FULL_FIELDS]))
    for h in hours:
        key = f"{CITY}|{h:%Y-%m-%dT%H:%M}"
        record.append(("hourly", key, (h - dt.timedelta(hours=17)).timestamp(), hourly_vals(h, 1)))
        record.append(("hourly2", key, (h - dt.timedelta(hours=41)).timestamp(), hourly_vals(h, 2)))
        record.append(("models1", key, (h - dt.timedelta(hours=17)).timestamp(), model_vals(h, 1)))
        record.append(("models2", key, (h - dt.timedelta(hours=41)).timestamp(), model_vals(h, 2)))
    band_ids = [f"b{i}" for i in range(len(BANDS))]
    for t in ticks:
        ps = prices_at(t)
        tops = {b: [round(p - 0.01, 4), round(p + 0.01, 4)] + [None] * 8 for b, p in zip(band_ids, ps)}
        record.append(("books", "all", t.timestamp(), {"buckets": len(BANDS), "complete": True, "failed": [],
                                                       "no_book": [], "tops": tops}))
    sdays = {ICAO: {(DAY - dt.timedelta(days=k)).isoformat(): (17.0 + k * 0.1, 62.6, 15.0) for k in range(1, 400)}}
    event = {"event_id": "m1", "source": "venue", "city": CITY, "date": DAY.isoformat(), "unit": "C",
             "station": ICAO, "bands": BANDS, "winner": None, "band_ids": band_ids}
    return {"rep": rep, "hourly": hourly, "bm": bm, "md": md, "series": series, "record": record,
            "sdays": sdays, "event": event, "ticks": ticks}


def training_rows(d):
    from zoneinfo import ZoneInfo
    e, tz = d["event"], ZoneInfo(TZ)
    st = d["sdays"][ICAO]
    ctx = features.event_context(e, d["rep"], st, tz)
    out = {}
    for off, local in features.decision_instants(DAY, tz):
        row = features.decision_row(e, ctx, off, local, d["rep"], st, d["bm"], d["md"], d["hourly"], d["series"], tz)
        out[row["decision_utc"]] = row
    return out


def test_a_live_row_is_the_tables_row(city_days):
    """ONE IMPLEMENTATION: every feature the table's row has, the live row has,
    equal, when the record holds what the training's files hold."""
    d = city_days
    want = training_rows(d)
    record = lr.Record(d["record"])
    # the repository's daily files are the training's own (history)
    rows = lr.build(record, [d["event"]], CITIES, d["sdays"], d["bm"], d["md"], record.first, record.last,
                    modes=("rule",))
    got = {r["decision_utc"]: r for r in rows}
    assert len(got) >= 20, "the record's span holds most of D-1 and D"
    compared = 0
    for t, row in got.items():
        for c in features.COLUMNS:
            a, b = row.get(c), want[t].get(c)
            if isinstance(a, float) and isinstance(b, float):
                # a book's midpoint, (p - 0.01 + p + 0.01) / 2, is p to 1e-14
                assert a == pytest.approx(b, abs=1e-9), (c, dt.datetime.fromtimestamp(t, UTC), a, b)
            else:
                assert a == b, (c, dt.datetime.fromtimestamp(t, UTC), a, b)
            compared += 1
    assert compared == len(got) * len(features.COLUMNS)
    on_d = [r for r in got.values() if r["day_offset"] == 0 and r["local_hour"] >= 12]
    assert on_d and all(r["mkt_complete"] and r["rmax_c"] is not None and r["fc_bm_tmax_c"] is not None
                        and r["fc_models_n"] == len(honest_record.MODELS) and r["bias_n30"] >= 1
                        for r in on_d), "the rows are not empty"


def test_arrival_knows_only_what_had_reached_the_tick(city_days):
    """A report the rule knows 20 min after its reading, the tick had only
    once it arrived (here 12 min after the reading, so the rule binds) -
    and a later arrival than the rule allows is what `arrival` shows."""
    d = city_days
    late = []
    for kind, key, at, payload in d["record"]:
        if kind == "report":
            at += 50 * 60                     # every report reached the tick 62 min after its reading
        late.append((kind, key, at, payload))
    record = lr.Record(late)
    rows = lr.build(record, [d["event"]], CITIES, d["sdays"], d["bm"], d["md"], record.first, record.last)
    by = {}
    for r in rows:
        by.setdefault(r["decision_utc"], {})[r["live_mode"]] = r
    pairs = [v for v in by.values() if len(v) == 2]
    assert pairs
    later = [v for v in pairs if v["arrival"]["obs_age_min"] is not None and v["rule"]["obs_age_min"] is not None]
    assert later and all(v["arrival"]["obs_age_min"] > v["rule"]["obs_age_min"] for v in later), \
        "the tick saw each report later than the rule allows, so its newest report is older"
    assert all(v["arrival"]["mkt_prices"] == v["rule"]["mkt_prices"] for v in pairs), \
        "a book is known when the tick read it, either way"


def test_a_day_is_reduced_only_when_the_record_holds_it_whole(city_days):
    d = city_days
    record = lr.Record(d["record"])
    bm, md = lr.daily_rows(record, {CITY: TZ})
    assert (CITY, 1, DAY.isoformat()) in bm and (CITY, 2, DAY.isoformat()) in bm
    assert set(md[(CITY, 1, DAY.isoformat())]) == set(honest_record.MODELS)
    # the record's hours stop at 15 Oct 23Z: Madrid's 16 Oct has two of them,
    # fewer than MIN_HOURS, so no row
    assert (CITY, 1, (DAY + dt.timedelta(days=1)).isoformat()) in bm
    assert (CITY, 1, (DAY + dt.timedelta(days=2)).isoformat()) not in bm


def test_the_past_error_reads_the_repository_whatever_the_record_holds(city_days):
    """Codex on #387: only the event's own day comes from the record. A day
    the record holds (here 13 Oct, whole) is still read from the repository
    by every other event's row, so a day at the record's edge, reduced from
    20-23 of its hours, can never stand in for the archived day."""
    d = city_days
    live, _ = lr.daily_rows(lr.Record(d["record"]), {CITY: TZ})
    prior = (CITY, 1, (DAY - dt.timedelta(days=1)).isoformat())
    assert prior in live
    history = {prior: {"tmax_c": "99"}, (CITY, 1, DAY.isoformat()): {"tmax_c": "-99"}}
    own = lr.OwnDay(live, history, CITY, DAY.isoformat())
    assert own.get(prior) == {"tmax_c": "99"}, "a day before the event's: the repository's"
    assert own.get((CITY, 1, DAY.isoformat())) is live[(CITY, 1, DAY.isoformat())], "the event's day: the record's"
    assert own.get((CITY, 1, "2026-01-01"), {}) == {} and own.get(("paris", 1, DAY.isoformat())) is None


def test_the_command_writes_where_it_is_told(city_days, tmp_path, monkeypatch):
    """Codex on #387: `--out rows.csv.gz` (no directory) wrote nothing."""
    import csv
    import gzip
    import json
    from wxpredict import build_table, common
    d = city_days
    rec = tmp_path / "record.csv.gz"
    with gzip.open(rec, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "key", "captured_at", "payload", "written_at"])
        for kind, key, at, payload in d["record"]:
            stamp = dt.datetime.fromtimestamp(at, UTC).isoformat()
            w.writerow([kind, key, stamp, json.dumps(payload), stamp])
    markets = [{"market_id": "m1", "city_key": CITY, "resolution_date": DAY.isoformat(), "unit": "C"}]
    bands = [{"band_id": f"b{i}", "market_id": "m1", "band_index": str(i),
              "band_lo": "" if lo is None else str(lo), "band_hi": "" if hi is None else str(hi),
              "open_low": str(lo is None), "open_high": str(hi is None)} for i, (lo, hi) in enumerate(BANDS)]
    monkeypatch.setattr(lr, "read_definitions", lambda root=None: (markets, bands))
    monkeypatch.setattr(common, "cities", lambda: CITIES)
    monkeypatch.setattr(build_table, "load_station_daily", lambda: (d["sdays"], {}))
    monkeypatch.setattr(build_table, "load_daily_forecasts", lambda: (d["bm"], d["md"]))
    monkeypatch.chdir(tmp_path)
    assert lr.main(["--from", DAY.isoformat(), "--to", DAY.isoformat(), "--record", str(rec),
                    "--out", "rows.csv.gz"]) == 0
    with gzip.open(tmp_path / "rows.csv.gz", "rt") as f:
        rows = list(csv.DictReader(f))
    assert rows and {r["live_mode"] for r in rows} == {"rule", "arrival"}
    assert list(rows[0]) == lr.COLUMNS


def test_only_whole_ladders_are_events():
    markets = [{"market_id": "m1", "city_key": CITY, "resolution_date": "2026-10-14", "unit": "C"},
               {"market_id": "m2", "city_key": CITY, "resolution_date": "2026-10-15", "unit": "C"},
               {"market_id": "m3", "city_key": "nowhere", "resolution_date": "2026-10-14", "unit": "C"}]

    def band(m, i, lo, hi, ol, oh):
        return {"band_id": f"{m}-{i}", "market_id": m, "band_index": str(i), "band_lo": lo, "band_hi": hi,
                "open_low": ol, "open_high": oh}
    bands = [band("m1", 1, "22", "23", "False", "False"), band("m1", 0, "", "22", "True", "False"),
             band("m1", 2, "23", "", "False", "True"),
             band("m2", 0, "", "22", "True", "False"), band("m2", 1, "23", "", "False", "True")]
    (e,) = lr.live_events(markets, bands, CITIES, "2026-10-13", "2026-10-15")
    assert e["event_id"] == "m1" and e["band_ids"] == ["m1-0", "m1-1", "m1-2"]
    assert e["bands"] == [(None, 22.0), (22.0, 23.0), (23.0, None)] and e["station"] == ICAO


def test_a_row_at_any_instant_is_the_readers_own_row_on_the_hour(city_days):
    """row_at (the live shadow's): on one of the table's instants it is the
    row build() makes in arrival mode, column for column."""
    d = city_days
    record = lr.Record(d["record"])
    rows = lr.build(record, [d["event"]], CITIES, d["sdays"], d["bm"], d["md"], record.first, record.last,
                    modes=("arrival",))
    noon = next(r for r in rows if r["day_offset"] == 0 and r["local_hour"] == 13)
    one = lr.row_at(record, d["event"], CITIES, d["sdays"], d["bm"], d["md"], noon["decision_utc"])
    for c in lr.COLUMNS:
        assert one.get(c) == noon.get(c), c


def test_a_row_at_quarter_past_knows_the_report_v2_counts(city_days):
    """The fixture's reports reach the tick 12 min after the reading. At 15
    past, the :00 report is 15 min old: v2 (8 min) counts it, v1 (20 min)
    counts the half-hour report before it."""
    d = city_days
    record = lr.Record(d["record"])
    from zoneinfo import ZoneInfo
    t = int(dt.datetime(2026, 10, 14, 13, 15, tzinfo=ZoneInfo(TZ)).timestamp())
    v2 = lr.row_at(record, d["event"], CITIES, d["sdays"], d["bm"], d["md"], t,
                   report_lag_s=features.REPORT_LAG_V2_S)
    v1 = lr.row_at(record, d["event"], CITIES, d["sdays"], d["bm"], d["md"], t)
    assert v2["decision_utc"] == t and v2["local_hour"] == 13 and v2["live_mode"] == "arrival"
    assert v2["obs_age_min"] == pytest.approx(15) and v1["obs_age_min"] == pytest.approx(45)
    far = int(dt.datetime(2026, 10, 16, 12, tzinfo=ZoneInfo(TZ)).timestamp())
    assert lr.row_at(record, d["event"], CITIES, d["sdays"], d["bm"], d["md"], far) is None, "not D-1 or D"
