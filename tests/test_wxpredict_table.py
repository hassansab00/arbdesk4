"""WXPredict phase 1 (approved 5 Oct 2026): the training table knows only what
was known at each decision, and its label is the venue's winner.

tools/wxpredict/build_table.py, fetch_obs.py and fetch_forecasts.py, and the
seed step added to tools/market_history.py."""
import datetime as dt
import gzip
import json
import math
import pathlib
import sys
from array import array
from zoneinfo import ZoneInfo

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from wxpredict import build_table as bt  # noqa: E402
from wxpredict import common  # noqa: E402
from wxpredict import fetch_forecasts as ff  # noqa: E402
from wxpredict import fetch_obs as fo  # noqa: E402

LONDON = ZoneInfo("Europe/London")
DAY = dt.date(2026, 7, 15)
BANDS = [(None, 20), (20, 21), (21, 22), (22, 23), (23, 24), (24, None)]


def _unix(day, hour, minute=0, tz=LONDON):
    return int(dt.datetime.combine(day, dt.time(hour, minute), tz).timestamp())


def _report(when, temp_c, **kw):
    """A report row as fetch_obs writes it; `when` is unix seconds."""
    row = {k: "" for k in fo.REPORT_HEADER}
    row["valid"] = dt.datetime.fromtimestamp(when, dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    row["tmpf"] = f"{temp_c * 9 / 5 + 32:.2f}"
    row["dwpf"] = f"{(temp_c - 8) * 9 / 5 + 32:.2f}"
    row["mslp"] = "1015"
    row.update({k: str(v) for k, v in kw.items()})
    return row


def _day_reports(peak=23.0, peak_hour=15):
    rows = []
    for h in range(24):
        for m in (20, 50):
            x = h + m / 60
            rows.append(_report(_unix(DAY, h, m), round(peak - 0.25 * (x - peak_hour) ** 2)))
    return rows


# ---------------------------------------------------------------------------
# what the station reports say
# ---------------------------------------------------------------------------
def test_the_remarks_give_tenths_and_the_six_hour_extremes_from_rmk_only():
    assert fo.remarks("KLGA 010551Z 17006KT 7SM SCT015 22/21 A3003 RMK AO2 SLP168 60014 T02220206 10239 "
                      "20217 58009 $") == (22.2, 20.6, 23.9, 21.7)
    assert fo.remarks("KXXX 010551Z 10012KT RMK T10221006 11005") == (-2.2, -0.6, -0.5, "")
    # a body with no remarks has no groups, whatever its 5-digit tokens look like
    assert fo.remarks("EGLC 010020Z AUTO 28008KT 10000 NCD 17/11 Q1019") == ("", "", "", "")
    assert fo.remarks("") == ("", "", "", "")


def test_a_reading_is_whole_degrees_of_the_markets_unit():
    assert bt.unit_reading(22.8, 73.0, "F") == 73          # T group 22.8 C -> IEM 73.00 F
    assert bt.unit_reading(24.0, 75.2, "F") == 75          # a whole-C special: 75.2 F
    assert bt.unit_reading(23.3, 73.94, "F") == 74
    assert bt.unit_reading(17.0, 62.6, "C") == 17
    assert bt.unit_reading(22.5, None, "C") == 23           # tenths, half up
    assert bt.unit_reading(-0.5, None, "C") == 0


def test_a_value_falls_in_one_half_open_bucket():
    assert bt.bucket_of(19, BANDS) == 0 and bt.bucket_of(20, BANDS) == 1
    assert bt.bucket_of(23, BANDS) == 4 and bt.bucket_of(30, BANDS) == 5
    f = [(None, 60), (60, 62), (62, None)]
    assert bt.bucket_of(61, f) == 1 and bt.bucket_of(62, f) == 2


def test_no_observation_feature_reads_a_report_before_it_was_known():
    rows = _day_reports()
    t = _unix(DAY, 13) + bt.SNAPSHOT_S
    d0 = common.local_day_bounds(DAY, LONDON)[0]
    before = {}
    bt.obs_features(before, bt.Reports([r for r in rows if r["valid"] <= "2026-07-15T12:00Z"]), t, d0, 0,
                    LONDON, "C", BANDS, {}, DAY)
    later = {}
    # every later report, and a 40 C reading valid 1 minute before the decision, change nothing
    hot = _report(t - 60, 40.0)
    bt.obs_features(later, bt.Reports(sorted(rows + [hot], key=lambda r: r["valid"])), t, d0, 0, LONDON, "C",
                    BANDS, {}, DAY)
    assert before == later
    assert before["rmax_c"] < 40 and before["obs_age_min"] * 60 >= bt.REPORT_LAG_S


def test_the_running_maximum_is_todays_and_lands_in_its_bucket():
    rows = _day_reports(peak=23.0, peak_hour=15)
    v = {}
    t = _unix(DAY, 18) + bt.SNAPSHOT_S
    bt.obs_features(v, bt.Reports(rows), t, common.local_day_bounds(DAY, LONDON)[0], 0, LONDON, "C", BANDS,
                    {}, DAY)
    assert v["rmax_unit"] == 23 and v["rmax_bucket"] == 4
    assert v["n_reports_today"] == sum(1 for r in rows if bt.num(r["tmpf"]) is not None
                                       and dt.datetime.strptime(r["valid"], "%Y-%m-%dT%H:%MZ")
                                       .replace(tzinfo=dt.timezone.utc).timestamp() + bt.REPORT_LAG_S <= t)


def test_the_day_before_has_no_running_maximum_of_d():
    v = {}
    t = _unix(DAY - dt.timedelta(days=1), 15) + bt.SNAPSHOT_S
    bt.obs_features(v, bt.Reports(_day_reports()), t, common.local_day_bounds(DAY, LONDON)[0], -1, LONDON, "C",
                    BANDS, {}, DAY)
    assert "rmax_c" not in v


# ---------------------------------------------------------------------------
# what the forecasts said, and when it was out
# ---------------------------------------------------------------------------
def test_an_hourly_value_is_known_seventeen_hours_before_its_hour():
    hour = _unix(DAY, 15)
    assert bt.hourly_known(hour, hour - 17 * 3600)
    assert not bt.hourly_known(hour, hour - 17 * 3600 - 1)


def test_a_daily_row_is_known_when_its_last_hour_is():
    tz = LONDON
    midnight = _unix(DAY, 0)
    assert bt.daily_known(DAY, 1, 17, midnight, tz)              # lead 1, 00-17: at midnight
    assert not bt.daily_known(DAY, 1, 23, midnight, tz)          # lead 1, whole day: not yet
    assert bt.daily_known(DAY, 1, 23, _unix(DAY, 6), tz)         # ... from 06:00
    assert bt.daily_known(DAY, 2, 23, _unix(DAY - dt.timedelta(days=1), 6), tz)


def test_the_freshest_known_daily_forecast_is_taken():
    def row(v):
        return {"tmax_c": str(v), "tmax_00_17_c": str(v - 1), "cloud_09_17_pct": "", "shortwave_06_17_wh_m2": "",
                "wind_09_17_kmh": "", "precip_00_17_mm": "", "td_09_17_c": ""}
    bm = {("london", 1, DAY.isoformat()): row(25), ("london", 2, DAY.isoformat()): row(28)}
    md = {("london", 1, DAY.isoformat()): {"gfs_seamless": (24.0, 23.0)}}
    src, v, models, _ = bt.pick_daily("london", DAY, _unix(DAY, 9), LONDON, bm, md)
    assert (src, v, models) == ("l1_day", 25.0, {"gfs_seamless": 24.0})
    src, v, models, _ = bt.pick_daily("london", DAY, _unix(DAY, 2), LONDON, bm, md)
    assert (src, v, models) == ("l1_00_17", 24.0, {"gfs_seamless": 23.0})
    src, v, _, _ = bt.pick_daily("london", DAY, _unix(DAY - dt.timedelta(days=1), 12), LONDON, bm, md)
    assert (src, v) == ("l2_day", 28.0)


def test_no_forecast_feature_reads_an_hour_not_yet_published():
    hours = [_unix(DAY, h) for h in range(24)]
    rows = [(20.0 + (5 if h == 20 else 0), 10.0, 50.0, 100.0, 10.0, 0.0, 1015.0) for h in range(24)]
    v = {}
    t = _unix(DAY, 2) + bt.SNAPSHOT_S          # hours up to 19:00 are known; 20:00's 25 C is not
    bt.fc_features(v, "london", DAY, t, LONDON, {}, {}, {"london": (hours, rows)}, bt.Reports([]))
    assert v["fch_n_known"] == 20 and v["fch_day_max_c"] == 20.0
    v = {}
    bt.fc_features(v, "london", DAY, _unix(DAY, 4) + bt.SNAPSHOT_S, LONDON, {}, {}, {"london": (hours, rows)},
                   bt.Reports([]))
    assert v["fch_day_max_c"] == 25.0


# ---------------------------------------------------------------------------
# the past: climatology and the forecast's recent error
# ---------------------------------------------------------------------------
def test_climatology_reads_only_days_before_the_day_before():
    days = {}
    for y in (2022, 2023, 2024, 2025):
        for k in range(-20, 21):
            d = DAY.replace(year=y) + dt.timedelta(days=k)
            days[d.isoformat()] = (20.0, 68.0, 14.0, 24)
    days[(DAY - dt.timedelta(days=1)).isoformat()] = (99.0, 210.0, 14.0, 24)   # yesterday: not yet whole
    days[DAY.isoformat()] = (99.0, 210.0, 14.0, 24)
    mean, sd, n, peak = bt.climatology(days, DAY)
    assert mean == 20.0 and sd == 0.0 and peak == 14.0
    assert n == 4 * (2 * bt.CLIM_HALF_WINDOW + 1) - 0     # the four past years' windows


def test_the_forecasts_recent_error_reads_only_whole_past_days():
    bm, sdays = {}, {}
    for k in range(1, 40):
        d = (DAY - dt.timedelta(days=k)).isoformat()
        bm[("london", 1, d)] = {"tmax_c": "20"}
        sdays[d] = (21.0, 69.8, 14.0, 24)
    sdays[(DAY - dt.timedelta(days=1)).isoformat()] = (40.0, 104.0, 14.0, 24)
    v = {}
    # at 00:01 on D yesterday ended only REPORT_LAG_S ago at most: not used
    bt.bias_features(v, "london", "EGLC", DAY, _unix(DAY, 0) + bt.SNAPSHOT_S, LONDON, sdays, bm, {})
    assert v["bias7_bm_c"] == 1.0 and v["bias_n30"] == 30
    v = {}
    bt.bias_features(v, "london", "EGLC", DAY, _unix(DAY, 12), LONDON, sdays, bm, {})
    assert v["bias7_bm_c"] > 1.0                            # by noon it is


# ---------------------------------------------------------------------------
# the market at the decision
# ---------------------------------------------------------------------------
def _series(points):
    return [(array("q", [t for t, _ in pts]), array("d", [p for _, p in pts])) for pts in points]


def test_the_market_is_the_hours_snapshot_and_nothing_later():
    h = _unix(DAY, 12)
    pts = [[(h - 3600 + 20, 0.1), (h + 30, 0.2), (h + 3600 + 20, 0.9)] for _ in BANDS]
    lad = bt.ladder_at(_series(pts), h + bt.SNAPSHOT_S)
    assert [p for p, _ in lad] == [0.2] * len(BANDS)
    assert all(a == bt.SNAPSHOT_S - 30 for _, a in lad)


def test_a_ladder_with_a_stale_or_missing_bucket_is_not_a_market():
    fresh = [(0.5, 60)] * 6
    ok = bt.implied(fresh, BANDS)
    assert ok[0] is True and abs(sum(ok[2]) - 1) < 1e-12
    assert bt.implied(fresh[:-1] + [(0.5, bt.MARKET_MAX_AGE_S + 1)], BANDS)[0] is False
    assert bt.implied(fresh[:-1] + [(None, None)], BANDS)[0] is False


def test_the_markets_features_never_read_a_later_price():
    h = _unix(DAY, 14)
    base = [[(h - k * 3600 + 15, 0.02 if i else 0.9) for k in range(8, -1, -1)] for i in range(len(BANDS))]
    later = [pts + [(h + 3600 + 15, 0.99 if i == 3 else 0.001)] for i, pts in enumerate(base)]
    a, b = {"rmax_bucket": 2}, {"rmax_bucket": 2}
    bt.market_features(a, _series(base), BANDS, h + bt.SNAPSHOT_S)
    bt.market_features(b, _series(later), BANDS, h + bt.SNAPSHOT_S)
    assert a == b and a["mkt_complete"] is True and a["mkt_top"] == 0


# ---------------------------------------------------------------------------
# a whole event, end to end
# ---------------------------------------------------------------------------
def test_an_events_rows_carry_the_venue_label_and_every_decision_hour():
    e = {"event_id": "1", "source": "venue", "city": "london", "date": DAY.isoformat(), "unit": "C",
         "station": "EGLC", "bands": BANDS, "winner": 4}
    after = _report(_unix(DAY + dt.timedelta(days=1), 0, 20), 15.0)
    rows = bt.build_event(e, bt.Reports(_day_reports(peak=23.0) + [after]), {}, {}, {}, {}, {}, LONDON)
    assert len(rows) == len(bt.EVE_HOURS) + len(bt.DAY_HOURS)
    assert {r["winner"] for r in rows} == {4} and {r["station_max_unit"] for r in rows} == {23}
    assert all(r["station_in_winner"] for r in rows)
    assert [r["decision_utc"] % 3600 for r in rows] == [bt.SNAPSHOT_S] * len(rows)
    assert sorted(r["decision_utc"] for r in rows) == [r["decision_utc"] for r in rows]


def test_the_weather_models_label_is_the_venues_reading():
    assert bt.venue_label(23, 4, 23, 24) == 23                     # they agree
    assert bt.venue_label(21, 4, 23, 24) == 23                     # the venue's bucket wins
    assert bt.venue_label(64, 2, 60, 62) == 61                     # F: the nearest whole degree inside
    assert bt.venue_label(30, 5, 24, None) == 30                   # an open top bucket holds it
    assert bt.venue_label(None, 1, 60, 62) == 60 and bt.venue_label(None, 0, None, 20) == 19
    assert bt.venue_label(23, None, None, None) == 23              # an unlisted day: the station's


def test_a_day_whose_reports_stop_inside_it_has_no_station_label():
    e = {"event_id": "1", "source": "venue", "city": "london", "date": DAY.isoformat(), "unit": "C",
         "station": "EGLC", "bands": BANDS, "winner": 4}
    rows = bt.build_event(e, bt.Reports([r for r in _day_reports() if r["valid"] < "2026-07-15T18:00Z"]),
                          {}, {}, {}, {}, {}, LONDON)
    assert {r["station_max_unit"] for r in rows} == {None} and {r["winner"] for r in rows} == {4}
    assert {r["label_unit"] for r in rows} == {23}                 # the winning bucket's reading


def test_the_table_writes_the_same_bytes_twice(tmp_path):
    rows = [["a", "1.5", ""], ["b", "", "x"]]
    p1, p2 = tmp_path / "a.csv.gz", tmp_path / "b.csv.gz"
    common.write_csv(str(p1), ["k", "v", "w"], rows)
    common.write_csv(str(p2), ["k", "v", "w"], rows)
    assert p1.read_bytes() == p2.read_bytes()
    assert gzip.open(p1, "rt").read() == "k,v,w\na,1.5,\nb,,x\n"


def test_the_committed_meta_describes_this_builder():
    meta = json.loads((ROOT / "data" / "training" / "wxpredict" / "table_meta.json").read_text())
    assert meta["columns"] == bt.COLUMNS
    assert (meta["report_lag_s"], meta["publish_h"], meta["snapshot_s"]) == \
        (bt.REPORT_LAG_S, bt.PUBLISH_H, bt.SNAPSHOT_S)
    assert meta["station_max_in_winner"]["judged"] == meta["events_by_source"]["venue"]


# ---------------------------------------------------------------------------
# the sources
# ---------------------------------------------------------------------------
def test_a_us_station_is_asked_and_answered_under_its_iem_id():
    assert common.iem_id("KLGA") == "LGA" and common.iem_id("EGLC") == "EGLC" and common.iem_id("klax") == "LAX"


def test_the_daily_reduction_takes_the_local_day_and_the_first_time_at_the_maximum():
    tz = {"EGLC": LONDON}
    utc = dt.timezone.utc
    rd = [("EGLC", dt.datetime(2026, 7, 14, 23, 20, tzinfo=utc), 70.0, None),     # 00:20 BST on the 15th
          ("EGLC", dt.datetime(2026, 7, 15, 12, 20, tzinfo=utc), 75.2, None),
          ("EGLC", dt.datetime(2026, 7, 15, 13, 20, tzinfo=utc), 75.2, None),
          ("EGLC", dt.datetime(2026, 7, 15, 23, 20, tzinfo=utc), 60.8, None)]      # the 16th, locally
    days = fo.reduce_daily(rd, tz)
    d = days[("EGLC", DAY)]
    assert d["n"] == 3 and d["tmax_at"] == dt.datetime(2026, 7, 15, 12, 20, tzinfo=utc)
    assert math.isclose(d["tmax_c"], 24.0) and ("EGLC", DAY + dt.timedelta(days=1)) in days


def test_a_refetched_forecast_value_is_written_as_the_record_writes_it():
    assert [ff.fmt(x) for x in (0.0, 1022.0, 16.3, 69, None)] == ["0", "1022", "16.3", "69", ""]
    ans = {"utc_offset_seconds": 19800, "hourly": {"time": ["2026-09-26T00:00"],
                                                   **{f"{v}_previous_day1": [1.5] for v in ff.VARS.values()}}}
    assert ff.rows_of("lucknow", ans)[0][:3] == ["lucknow", "2026-09-25T18:30", "1.5"]


def test_an_answer_with_a_daylight_saving_step_is_refused():
    ans = {"utc_offset_seconds": 46800, "hourly": {"time": ["2026-09-27T01:00", "2026-09-27T03:00"],
                                                   **{f"{v}_previous_day1": [1.0, 2.0] for v in ff.VARS.values()}}}
    with pytest.raises(ValueError):
        ff.rows_of("wellington", ans)


def test_a_daylight_saving_day_has_its_real_instants_once_each():
    spring, autumn = dt.date(2026, 3, 29), dt.date(2026, 10, 25)
    for day, n_day in ((spring, 23), (autumn, 25), (DAY, 24)):
        inst = bt.decision_instants(day, LONDON)
        stamps = [int(x.timestamp()) for _, x in inst]
        assert len(stamps) == len(set(stamps)) and stamps == sorted(stamps)
        assert sum(1 for off, _ in inst if off == 0) == n_day
        assert all(x.minute == 0 for _, x in inst)
    hours = [x.hour for off, x in bt.decision_instants(autumn, LONDON) if off == 0]
    assert hours.count(1) == 2 and 1 not in [x.hour for off, x in bt.decision_instants(spring, LONDON) if off == 0]
    # a half-hour zone's decisions are on its own whole hours
    assert all(x.minute == 0 for _, x in bt.decision_instants(DAY, ZoneInfo("Asia/Kolkata")))


def test_three_hours_of_rain_are_summed_period_by_period():
    base = _unix(DAY, 9, 51)
    rows = [_report(base, 15.0, p01i="0.10"), _report(base + 1200, 15.0, p01i="0.05"),       # special, 10:11
            _report(base + 3600, 15.0, p01i="0.20"), _report(base + 2 * 3600, 15.0, p01i="0.30")]
    rep = bt.Reports(rows)
    assert rep.routine_minute == 51
    # periods closing 10:51, 11:51 and 12:51: 0.20 (the special's 0.05 is inside it) + 0.30; 09:51 is before
    assert math.isclose(rep.precip_since(base, len(rows)), 0.50)
    assert math.isclose(rep.precip_since(base - 1, len(rows)), 0.60)


def test_the_market_seed_keeps_only_finished_series(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("market_history", ROOT / "tools" / "market_history.py")
    mh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mh)
    out = tmp_path / "record"
    out.mkdir()

    def gz(name, header, rows):
        with gzip.open(out / name, "wt", newline="") as f:
            f.write(",".join(header) + "\n" + "".join(",".join(map(str, r)) + "\n" for r in rows))
    gz("events.csv.gz", ["event_id", "date", "closed"], [["a", "2026-09-01", 1], ["b", "2026-09-27", 1],
                                                        ["c", "2026-09-01", 0], ["d", "2026-09-01", 1]])
    gz("bands.csv.gz", ["event_id", "band_index", "winner"],
       [["a", 0, 1], ["a", 1, 0], ["b", 0, 1], ["c", 0, ""], ["d", 0, 1], ["d", 1, 1]])
    gz("prices.csv.gz", ["event_id", "band_index", "t", "p"], [["a", 0, 10, 0.5], ["a", 0, 20, 0.75], ["b", 0, 5, 0.1]])
    monkeypatch.setattr(mh, "OUT", str(out))

    class A:
        cache = str(tmp_path / "cache")
        seed_before = "2026-09-25"
    mh.cmd_seed(A)
    lines = [json.loads(x) for x in open(tmp_path / "cache" / "prices.jsonl")]
    # a: resolved and early enough (both buckets, one with no points); b too late; c open; d two winners
    assert lines == [{"event_id": "a", "band_index": 0, "points": [[10, 0.5], [20, 0.75]]},
                     {"event_id": "a", "band_index": 1, "points": []}]
    with pytest.raises(SystemExit):
        mh.cmd_seed(A)                  # never over a cache that holds anything
