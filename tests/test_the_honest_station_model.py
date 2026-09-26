"""The honest training record and the station model learned from it (plan v2.2 P2.9).

The desk's weather model trained on afternoon weather that had already been
observed and lost to the public forecast by 0.969 C forward. These tests hold
the replacement to the plan's two tests - no input after the cutoff, and one
definition of every feature for training and for prediction - and to Rule 11.
"""
import copy
import datetime as dt
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import honest_record as hr
import probability_engine as pe
import station_mos as sm

FIXTURE = os.path.join(ROOT, "tests", "fixtures", "previous_runs_amsterdam_2025-07-18_4days.json")


def _fixture():
    with open(FIXTURE) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# the record
# ---------------------------------------------------------------------------
def test_the_nightly_rules_rewrite_the_committed_record_exactly():
    """Four real days of Amsterdam's raw answer (26 Sep, from the same
    requests the record was made from) reduce to the committed rows, text for
    text: the SQL that made the record and the Python that extends it agree."""
    fx = _fixture()
    bm, md = hr.previous_rows("amsterdam", fx["heating"], fx["models"])
    want_bm = {tuple(r[:3]): r for r in hr.read_rows(hr.BEST_MATCH_FILE)
               if r[0] == "amsterdam" and "2025-07-18" <= r[2] <= "2025-07-21"}
    want_md = {tuple(r[:4]): r for r in hr.read_rows(hr.MODELS_FILE)
               if r[0] == "amsterdam" and "2025-07-18" <= r[3] <= "2025-07-21"}
    assert len(bm) == len(want_bm) == 8 and len(md) == len(want_md) == 56
    for r in map(hr._text, bm):
        assert want_bm[tuple(r[:3])] == r
    for r in map(hr._text, md):
        assert want_md[tuple(r[:4])] == r


def test_every_training_window_ends_by_the_cutoff_hour():
    for col, (lo, hi) in hr.WINDOWS.items():
        if col in hr.COMPARISON_ONLY:
            continue
        assert hi <= hr.LAST_HOUR, f"{col} reads {hi}:00, after the cutoff"


def test_no_input_moves_when_the_hours_after_the_cutoff_change():
    """The lookahead test: rewrite every value after 17:00 and only the
    comparison column (tmax_c) may change."""
    fx = _fixture()
    changed = copy.deepcopy(fx)
    for js in (changed["heating"], changed["models"]):
        h = js["hourly"]
        for k, vals in h.items():
            if k == "time":
                continue
            h[k] = [(v + 9.0 if v is not None else v) if int(t[11:13]) > hr.LAST_HOUR else v
                    for t, v in zip(h["time"], vals)]
    before = hr.previous_rows("amsterdam", fx["heating"], fx["models"])
    after = hr.previous_rows("amsterdam", changed["heating"], changed["models"])
    tmax_col = hr.BEST_MATCH_HEADER.index("tmax_c")
    for b, a in zip(before[0], after[0]):
        assert b[:tmax_col] + b[tmax_col + 1:] == a[:tmax_col] + a[tmax_col + 1:]
    for b, a in zip(before[1], after[1]):
        assert b[-1] == a[-1], "tmax_00_17_c moved with an hour after 17:00"


def test_training_and_forward_rows_come_from_one_definition():
    """The same hourly values, named as the Previous Runs API names lead 1 and
    as the forecast API names the current run, give the same rows."""
    fx = _fixture()
    cur_h = {"time": fx["heating"]["hourly"]["time"],
             **{v: fx["heating"]["hourly"][f"{v}_previous_day1"] for v in hr.HEATING}}
    cur_m = {"time": fx["models"]["hourly"]["time"],
             **{f"temperature_2m_{m}": fx["models"]["hourly"][f"temperature_2m_previous_day1_{m}"]
                for m in hr.MODELS}}
    offset = fx["heating"]["utc_offset_seconds"]
    fetched = dt.datetime(2025, 7, 18, 12, tzinfo=dt.timezone.utc) - dt.timedelta(seconds=offset) \
        - dt.timedelta(days=1)                       # local "today" = 17 Jul, so 18 Jul is lead 1
    fbm, fmd = hr.current_rows("amsterdam", {"hourly": cur_h, "utc_offset_seconds": offset},
                               {"hourly": cur_m, "utc_offset_seconds": offset}, fetched)
    pbm, pmd = hr.previous_rows("amsterdam", fx["heating"], fx["models"])
    lead1 = {tuple(r[:3]): r for r in pbm if r[1] == 1}
    assert fbm and all(lead1[tuple(r[:3])] == r for r in fbm if r[1] == 1)
    lead1m = {tuple(r[:4]): r for r in pmd if r[1] == 1}
    assert fmd and all(lead1m[tuple(r[:4])] == r for r in fmd if r[1] == 1)


def test_the_merge_replaces_a_day_it_reads_again_and_keeps_the_rest():
    old = [["a", "1", "2026-09-01", "1"], ["a", "1", "2026-09-02", "2"]]
    new = [["a", 1, "2026-09-02", 5.0], ["a", 1, "2026-09-03", 6.5]]
    assert hr.merge(old, new, 3) == [["a", "1", "2026-09-01", "1"], ["a", "1", "2026-09-02", "5"],
                                     ["a", "1", "2026-09-03", "6.5"]]


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------
def _rows(n_days=240, cities=("a", "b"), slope=0.5, seed=3):
    import random
    rng = random.Random(seed)
    out = []
    start = dt.date(2025, 9, 1)
    for d in range(n_days):
        for c in cities:
            x = [rng.gauss(20, 5), rng.gauss(1, 0.3)] + [rng.gauss(0, 0.5) for _ in range(4)] + \
                [rng.gauss(0, 1) for _ in range(len(sm.FEATURES) - 6)]
            base = x[0]
            y = base + 1.0 + slope * x[sm.FEATURES.index("cloud_09_17")] + rng.gauss(0, 0.2)
            out.append({"city": c, "date": start + dt.timedelta(days=d), "x": x, "base": base, "y": y,
                        "full": {}})
    return out


def test_the_fit_finds_a_known_relation():
    praw, craw, _ = sm.choose_and_fit(_rows())
    cf = craw["a"]
    assert cf["cloud_09_17"] == pytest.approx(0.5, abs=0.05)
    row = {"x": [20.0, 1.0] + [0.0] * 4 + [0.0] * (len(sm.FEATURES) - 6), "base": 20.0}
    row["x"][sm.FEATURES.index("cloud_09_17")] = 2.0
    assert sm.predict(cf, row) == pytest.approx(22.0, abs=0.15)


def test_a_city_below_the_minimum_uses_the_pool():
    rows = _rows(n_days=200) + [r for r in _rows(n_days=sm.MIN_CITY_DAYS - 1, cities=("small",))]
    praw, craw, _ = sm.choose_and_fit(rows)
    assert craw["small"] == praw


def test_the_correction_is_bounded():
    cf = {f: 0.0 for f in sm.FEATURES}
    cf["intercept"] = 50.0
    row = {"x": [20.0] + [0.0] * (len(sm.FEATURES) - 1), "base": 20.0}
    assert sm.predict(cf, row) == 20.0 + sm.BOUND_C


def test_a_night_moves_a_city_by_at_most_the_step():
    old = {f: 0.0 for f in sm.FEATURES}
    old["intercept"] = 0.0
    new = dict(old, intercept=1.0)
    ref = {"x": [0.0] * len(sm.FEATURES), "base": 0.0}
    stepped, a = sm.bounded_step(new, old, ref)
    assert a == pytest.approx(sm.MAX_STEP_C)
    assert sm.correction(stepped, ref["x"]) == pytest.approx(sm.MAX_STEP_C)
    assert sm.bounded_step(dict(old, intercept=0.1), old, ref) == (dict(old, intercept=0.1), 1.0)
    assert sm.bounded_step(new, None, ref) == (new, 1.0), "the first night has nothing to step from"


def test_the_version_names_the_coefficients():
    a = sm.version_of({("x", 1): {"intercept": 1.0}}, dt.date(2026, 9, 27), {})
    b = sm.version_of({("x", 1): {"intercept": 1.1}}, dt.date(2026, 9, 27), {})
    assert a != b and a.startswith("station-mos:2026-09-27:")


def test_the_last_station_max_is_one_the_nightly_job_can_know():
    """The job runs at ~05Z, before the target's eve has ended in most cities:
    lead 1 sees the day before the eve."""
    labels = {("a", "2026-09-25"): 20.0, ("a", "2026-09-26"): 21.0}
    assert sm.last_known_obs(labels, "a", dt.date(2026, 9, 27), 1) == 20.0
    assert sm.last_known_obs(labels, "a", dt.date(2026, 9, 28), 2) == 20.0


def test_features_need_four_models_and_every_input():
    bm = ["a", "1", "2026-09-27", "20", "20", "12", "14", "10", "11", "50", "80", "4000", "10", "0", "1015", "24"]
    four = {"ecmwf_ifs025": 20.0, "gfs_seamless": 21.0, "icon_seamless": 19.0, "jma_seamless": 20.0}
    x, base = sm.features(bm, four, 19.0, dt.date(2026, 9, 27))
    assert base == 20.0 and len(x) == len(sm.FEATURES)
    assert sm.features(bm, dict(list(four.items())[:3]), 19.0, dt.date(2026, 9, 27)) == (None, None)
    assert sm.features(bm, four, None, dt.date(2026, 9, 27)) == (None, None)


# ---------------------------------------------------------------------------
# the engine
# ---------------------------------------------------------------------------
P39 = {"city_key": "london", "for_date": "2026-09-28", "lead_days": 1, "combined_c": 22.4, "spread_c": 0.6,
       "n_sources": 7, "version": "station-correction:2026-09-27:abc"}
MOS = {"city_key": "london", "for_date": "2026-09-28", "lead_days": 1, "mos_c": 22.0, "blend_c": 22.2,
       "p39_version": "station-correction:2026-09-27:abc", "version": "station-mos:2026-09-27:def"}


def _engine(monkeypatch, mos_on, mos_row=MOS):
    def rest(path, params=None, **k):
        key = dict(params or []).get("key")
        if path == "settings":
            on = True if key == "eq.station_correction_pricing" else mos_on
            return [{"value": {"enabled": on, "min_lead_days": 1, "max_age_hours": 36}}]
        if path == "derived_corrected_forecast":
            return [dict(P39)]
        if path == "derived_mos_forecast":
            return [dict(mos_row)] if mos_row else []
        raise AssertionError(path)
    monkeypatch.setattr(pe, "rest", rest)
    pe._station_cache = None
    try:
        return pe._station_corrected_for("london", "2026-09-28", 1)
    finally:
        pe._station_cache = None


def test_off_the_engine_prices_from_p39_alone(monkeypatch):
    row = _engine(monkeypatch, mos_on=False)
    assert row["combined_c"] == 22.4 and "+" not in row["version"]


def test_on_the_engine_prices_from_the_blend_and_names_both_versions(monkeypatch):
    row = _engine(monkeypatch, mos_on=True)
    assert row["combined_c"] == pytest.approx(22.2)
    assert row["version"] == "station-correction:2026-09-27:abc+station-mos:2026-09-27:def"
    assert row["p39_c"] == 22.4 and row["mos_c"] == 22.0


def test_a_blend_made_from_another_p39_is_not_used(monkeypatch):
    row = _engine(monkeypatch, mos_on=True, mos_row=dict(MOS, p39_version="station-correction:2026-09-26:old"))
    assert row["combined_c"] == 22.4


def test_a_broken_station_model_read_keeps_p39(monkeypatch):
    row = _engine(monkeypatch, mos_on=True, mos_row=dict(MOS, blend_c="not a number"))
    assert row["combined_c"] == 22.4 and "+" not in row["version"]
