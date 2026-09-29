"""Plan v2.4 P3.10 part 3.1: the forecast corrections learn from the venue's
truth (scripts/venue_truth.py, v_venue_truth), not the station maximum as the
desk read it. Measured 28 Sep: F cities' station labels differed from the
venue's reading on 132 of 311 city-days (0.21 C too high on average); C cities'
named a lower bucket than the winner on 10.3% of city-days before Sep."""
import csv
import datetime as dt
import gzip
import pathlib
import random
import sys
import types

import station_correction as sc
import venue_truth as vt

ROOT = pathlib.Path(__file__).resolve().parents[1]
SOURCES = ["m1", "m2", "m3", "m4"]


def test_a_bucket_names_the_midpoint_of_the_readings_it_holds():
    assert vt.bucket_label_c(22, 23, "C") == 22.0                  # the venue read 22 C
    assert abs(vt.bucket_label_c(72, 74, "F") - (72.5 - 32) * 5 / 9) < 1e-12   # it read 72 or 73 F
    assert vt.bucket_label_c(None, 17, "C") is None                # a tail names no value
    assert vt.bucket_label_c(26, None, "C") is None


def test_the_setting_chooses_and_the_venue_is_the_default():
    def rest(value=None, boom=False):
        def r(path, params=None, **k):
            assert path == "settings"
            if boom:
                raise RuntimeError("down")
            return [] if value is None else [{"value": value}]
        return r
    assert vt.source(rest()) == "venue"
    assert vt.source(rest({"source": "station"})) == "station"
    assert vt.source(rest({"source": "venue"})) == "venue"
    assert vt.source(rest({"source": "guess"})) == "venue"
    assert vt.source(rest(boom=True)) == "venue"


def _record(tmp):
    """A tiny committed record: london (EGLC) and nyc (KLGA)."""
    events = [
        # event_id, slug, city_slug, city_key, date, unit, resolution_source, station_icao, station_text, created_at, closed, listing
        ["1", "s1", "london", "london", "2026-08-01", "C", "", "EGLC", "", "", "1", "main"],
        ["2", "s2", "london", "london", "2026-08-02", "C", "", "LFPG", "", "", "1", "main"],   # another station
        ["3", "s3", "nyc", "nyc", "2026-08-01", "F", "", "KLGA", "", "", "1", "main"],
        ["4", "s4", "nyc", "nyc", "2026-08-02", "F", "", "KLGA", "", "", "1", "arch"],       # arch with a main twin
        ["5", "s5", "nyc", "nyc", "2026-08-02", "F", "", "KLGA", "", "", "1", "main"],
        ["6", "s6", "london", "london", "2026-08-03", "C", "", "EGLC", "", "", "1", "main"],  # the tail won
        ["7", "s7", "london", "london", "2026-08-04", "C", "", "EGLC", "", "", "0", "main"],  # not resolved
        ["8", "s8", "london", "london", "2026-08-25", "C", "", "EGLC", "", "", "1", "main"],  # the database has it
    ]
    bands = [
        ["1", "0", "", "", "22", "1", "0", "t", "0"], ["1", "1", "", "22", "23", "0", "0", "t", "1"],
        ["2", "0", "", "22", "23", "0", "0", "t", "1"],
        ["3", "0", "", "70", "72", "0", "0", "t", "0"], ["3", "1", "", "72", "74", "0", "0", "t", "1"],
        ["4", "0", "", "60", "62", "0", "0", "t", "1"],
        ["5", "0", "", "80", "82", "0", "0", "t", "1"],
        ["6", "0", "", "30", "", "0", "1", "t", "1"],
        ["7", "0", "", "22", "23", "0", "0", "t", ""],
        ["8", "0", "", "15", "16", "0", "0", "t", "1"],
    ]
    for name, header, rows in (
            ("events.csv.gz", ["event_id", "slug", "city_slug", "city_key", "date", "unit", "resolution_source",
                               "station_icao", "station_text", "created_at", "closed", "listing"], events),
            ("bands.csv.gz", ["event_id", "band_index", "label", "band_lo", "band_hi", "open_low", "open_high",
                              "token_yes", "winner"], bands)):
        with gzip.open(tmp / name, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
    return tmp


def test_the_record_gives_the_winning_bucket_on_the_studys_filters(tmp_path):
    rec = _record(tmp_path)
    got = vt.record_labels({"london": "EGLC", "nyc": "KLGA"}, record=str(rec))
    assert got == {("london", "2026-08-01"): 22.0,
                   ("nyc", "2026-08-01"): (72.5 - 32) * 5 / 9,
                   ("nyc", "2026-08-02"): (80.5 - 32) * 5 / 9,     # the main listing, not the arch twin
                   ("london", "2026-08-25"): 15.0}
    # another station, an open bucket, an unresolved event and an inactive city give nothing
    assert vt.record_labels({"nyc": "KLGA"}, record=str(rec)).keys() == {("nyc", "2026-08-01"), ("nyc", "2026-08-02")}
    assert vt.record_labels({"london": "EGLC"}, start="2026-08-02", end="2026-08-26", record=str(rec)) == {
        ("london", "2026-08-25"): 15.0}


def test_the_database_comes_first_and_the_record_fills_the_rest(tmp_path):
    rec = _record(tmp_path)
    seen = []

    def rest_all(path, params=None, **k):
        seen.append((path, dict(params)))
        assert path == "v_venue_truth"
        return [{"city_key": "london", "for_date": "2026-08-25", "label_c": 15.4, "source": "venue_reading"}]
    got = vt.load(rest_all, "2026-08-01", "2026-09-01", icao={"london": "EGLC"}, record=str(rec))
    assert got == {("london", "2026-08-01"): 22.0, ("london", "2026-08-25"): 15.4}   # the reading, not 15.0
    assert seen[0][1]["for_date"] in ("gte.2026-08-01", "lt.2026-09-01")
    assert vt.load(rest_all, "2026-08-01", "2026-09-01", record=str(rec)) == {("london", "2026-08-25"): 15.4}


def _pairs(days=40, seed=3):
    """Two cities whose station labels read 0.5 C above what the venue settled on."""
    rng = random.Random(seed)
    start = dt.date(2026, 8, 10)
    rows = []
    for i in range(days):
        d = (start + dt.timedelta(days=i)).isoformat()
        for city, truth in (("hot", 30.0), ("cold", 12.0)):
            y = truth + rng.gauss(0, 0.6)
            for s, bias in zip(SOURCES, (-1.0, 0.5, 0.0, 1.2)):
                rows.append((city, d, s, 1, y - bias + rng.gauss(0, 0.3), y))
    return rows


def _run(monkeypatch, setting=None):
    pairs = _pairs()
    written, logged = {}, []
    common = types.ModuleType("common")
    import common as real_common

    def rest_all(path, params=None, **k):
        p = dict(params)
        if path == "settings":
            return [] if setting is None else [{"value": {"source": setting}}]
        if path == "v_venue_truth":
            return [{"city_key": c, "for_date": d, "label_c": y, "source": "venue_reading"}
                    for c, d, s, l, fc, y in pairs if s == "m1"]
        if path == "derived_city_day_features":
            return [{"city_key": c, "obs_date": d, "max_c": y + 0.5, "computed_at": "2026-09-20T05:00:00+00:00"}
                    for c, d, s, l, fc, y in pairs if s == "m1"]
        if path == "cities":
            return [{"city_key": c, "timezone": "UTC"} for c in ("hot", "cold")]
        if path == "weather_forecast_models" and p.get("source") == f"eq.{sc.FIT_SOURCE}":
            return [{"city_key": c, "model": s, "for_date": d, "lead_days": l, "forecast_max_c": fc}
                    for c, d, s, l, fc, y in pairs]
        if path in ("weather_forecast_models", "derived_station_correction", "derived_station_width"):
            return []
        if path == "ingest_log":
            return []                    # weather_history: never pruned
        raise AssertionError(path)
    common.rest_all = rest_all
    common.day_had_ended = real_common.day_had_ended
    common.get_cities = lambda **k: [{"city_key": "hot", "icao": "HOT1"}, {"city_key": "cold", "icao": "CLD1"}]
    common.upsert_replace = lambda t, rows, key: (written.setdefault(t, rows), len(rows))[1]
    common.log_run = lambda job, status, rows, detail: logged.append(detail)
    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setattr(vt, "RECORD", str(ROOT / "no-such-record"))
    return sc.main(["--as-of", "2026-09-19"]), written


def test_the_correction_learns_the_venues_truth_and_scores_both_on_it(monkeypatch):
    d, written = _run(monkeypatch)
    lab = d["labels"]
    assert lab["source"] == "venue"
    both = lab["scored_on_venue_truth"]
    assert both["venue"]["n"] == both["station"]["n"] > 0, "the two label sets are scored on the same city-days"
    # the station labels read 0.5 C high: fitted on them, the correction is 0.5 C high against the venue
    assert abs(both["station"]["bias_c"] - 0.5) < 0.1 and abs(both["venue"]["bias_c"]) < 0.1
    assert both["venue"]["mae_c"] < both["station"]["mae_c"]
    m1 = {r["city_key"]: r["bias_c"] for r in written["derived_station_correction"] if r["source"] == "m1"}
    assert abs(m1["hot"] - (-1.0)) < 0.3, "the cell learns the source's error against the venue"


def test_the_setting_can_put_the_station_labels_back(monkeypatch):
    d, written = _run(monkeypatch, setting="station")
    assert d["labels"]["source"] == "station"
    m1 = {r["city_key"]: r["bias_c"] for r in written["derived_station_correction"] if r["source"] == "m1"}
    assert abs(m1["hot"] - (-0.5)) < 0.3, "fitted on labels 0.5 C high, the cell is 0.5 C higher"


def test_the_view_is_for_the_service_role_and_the_settlement_is_the_authority():
    sql = (ROOT / "supabase" / "migrations" / "20260928120000_the_venue_is_the_truth.sql").read_text()
    assert "revoke all on public.v_venue_truth from public, anon, authenticated;" in sql
    assert "grant select on public.v_venue_truth to service_role;" in sql
    assert "security_invoker = true" in sql
    assert "w.winners = 1" in sql                                   # two winners: no bucket label
    assert "floor(r.reading_c * 9 / 5 + 32 + 0.5)" in sql          # the venue's whole F reading
