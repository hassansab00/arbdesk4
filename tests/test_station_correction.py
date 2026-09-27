"""Each source's station error, learned and combined (plan v2.2 P3.9)."""
import datetime as dt
import random

import pytest

import station_correction as sc

SOURCES = ["m1", "m2", "m3", "m4", "m5"]
# m1 runs 2 C cold at "hot", m2 runs 1 C warm everywhere, the rest are honest.
BIAS = {("m1", "hot"): -2.0, ("m2", "hot"): 1.0, ("m2", "cold"): 1.0}


def _pairs(days=30, cities=("hot", "cold"), noise=0.3, seed=1, start=dt.date(2026, 8, 20)):
    rng = random.Random(seed)
    out = []
    for i in range(days):
        day = (start + dt.timedelta(days=i)).isoformat()
        for city in cities:
            truth = 25 + rng.gauss(0, 3)
            for s in SOURCES:
                for lead in sc.LEADS:
                    fc = truth + BIAS.get((s, city), 0.0) + rng.gauss(0, noise)
                    out.append((city, day, s, lead, fc, truth))
    return out


def test_a_known_station_bias_is_recovered_and_shrunk():
    t = sc.fit(_pairs())
    # error = station - source, so a source 2 C cold at "hot" needs +2
    b, n = t["cells"][("m1", 1, "hot")]
    assert n == 30 and 1.4 < b < 2.0          # shrunk toward the pool, not past the truth
    assert abs(t["cells"][("m3", 1, "cold")][0]) < 0.3
    assert t["cells"][("m2", 1, "cold")][0] == pytest.approx(-1.0, abs=0.2)


def test_a_city_with_too_few_days_sits_on_its_pool():
    pairs = _pairs() + [("new", "2026-09-01", s, 1, 30.0, 30.0) for s in SOURCES]
    t = sc.fit(pairs)
    b, n = t["cells"][("m1", 1, "new")]
    assert n == 1 and b == t["pooled"][("m1", 1)][0]


def test_bias_is_bounded_and_steps_at_most_max_step():
    wild = [("x", f"2026-09-{d:02d}", s, 1, 10.0, 30.0) for d in range(1, 21) for s in SOURCES]
    t = sc.fit(wild)
    assert t["cells"][("m1", 1, "x")][0] == sc.BOUND_C
    held = sc.fit(wild, previous={("m1", 1, "x"): 0.0})
    assert held["cells"][("m1", 1, "x")][0] == pytest.approx(sc.MAX_STEP_C)


def test_the_combination_is_equal_weight_and_needs_enough_sources():
    t = sc.fit(_pairs())
    fcs = {s: 25.0 for s in SOURCES}
    mean, spread, used = sc.combine(fcs, t, "hot", 1)
    assert mean == pytest.approx(sum(used.values()) / len(used))
    assert set(used) == set(SOURCES) and spread is not None
    assert sc.combine({"m1": 25.0, "m2": 25.0, "m3": 25.0}, t, "hot", 1) is None


def test_lead_zero_uses_the_lead_one_cell():
    t = sc.fit(_pairs())
    assert sc.correction(t, "m1", 0, "hot") == t["cells"][("m1", 1, "hot")][0]
    assert sc.correction(t, "unknown", 1, "hot") is None


def test_the_correction_beats_the_raw_mean_walk_forward():
    pairs = _pairs(days=40)
    days = sorted({p[1] for p in pairs})
    s = sc.walk_forward(pairs, days[-10:])
    assert s["n"] == 20 and s["mae_corrected"] < s["mae_raw_mean"]


def test_the_walk_forward_never_sees_the_day_it_scores_or_later():
    """Poison the scored day's own errors and every later day's: the score of
    that day must not move by one bit, because nothing on or after it may
    enter its fit."""
    pairs = _pairs(days=30)
    days = sorted({p[1] for p in pairs})
    scored = days[-5]
    clean = sc.walk_forward(pairs, [scored])
    poisoned = [(c, d, s, l, fc + (40 if d >= scored else 0), y) for c, d, s, l, fc, y in pairs
                if d != scored] + [p for p in pairs if p[1] == scored]
    assert sc.walk_forward(poisoned, [scored]) == clean


def test_the_version_names_the_day_and_the_fit():
    t = sc.fit(_pairs())
    v = sc.version_of(t, dt.date(2026, 9, 26))
    assert v.startswith("station-correction:2026-09-26:") and v == sc.version_of(t, dt.date(2026, 9, 26))
    assert v != sc.version_of(sc.fit(_pairs(seed=2)), dt.date(2026, 9, 26))


def test_a_run_writes_cells_and_open_days_and_logs(monkeypatch):
    import sys
    import types
    import common as real_common
    pairs = _pairs(days=30)
    written, logged = {}, []
    common = types.ModuleType("common")

    def rest_all(path, params=None, **k):
        p = dict(params)
        if path == "cities":
            return [{"city_key": c, "timezone": "UTC"} for c in {p[0] for p in pairs}]
        if path == "derived_city_day_features":
            return [{"city_key": c, "obs_date": d, "max_c": y, "computed_at": f"{d}T23:59:00+00:00"
                     if d == "2026-09-25" else "2026-09-26T05:00:00+00:00"}
                    for c, d, s, l, fc, y in pairs if s == "m1" and l == 1]
        if path == "weather_forecast_models" and p.get("source") == f"eq.{sc.FIT_SOURCE}":
            return [{"city_key": c, "model": s, "for_date": d, "lead_days": l, "forecast_max_c": fc}
                    for c, d, s, l, fc, y in pairs]
        if path == "weather_forecast_models":
            return [{"city_key": "hot", "model": s, "for_date": "2026-09-27", "lead_days": 1,
                     "forecast_max_c": 25.0, "run_at": "2026-09-26T00:00:00Z"} for s in SOURCES]
        if path == "derived_station_correction":
            return []
        raise AssertionError(path)
    common.rest_all = rest_all
    common.day_had_ended = real_common.day_had_ended
    common.upsert_replace = lambda t, rows, key: written.setdefault(t, rows) and len(rows)
    common.log_run = lambda job, status, rows, detail: logged.append((job, status, rows, detail))
    monkeypatch.setitem(sys.modules, "common", common)
    d = sc.main(["--as-of", "2026-09-26"])
    # 25 Sep was "computed" at 23:59 on the day itself: part of the day, so
    # it is not truth and none of its pairs was read
    assert d["pairs"] == len([p for p in pairs if p[1] != "2026-09-25"])
    assert len(written["derived_station_correction"]) == 2 * len(SOURCES) * len(sc.LEADS)
    assert written["derived_corrected_forecast"][0]["city_key"] == "hot"
    assert logged[0][0] == "P3.9_station_correction" and d["walk_forward"]["n"] > 0


def test_every_row_written_carries_this_runs_computed_at(monkeypatch):
    """merge-duplicates updates only the columns a row carries: the column's
    default fires on INSERT and never again. Without computed_at on the row, a
    re-fitted open day keeps the time it was first written, and the engine,
    which reads rows younger than max_age_hours by that column, drops it 36 h
    later: 21 of the 37 rows for 29 Sep would have gone from 28 Sep 17:15Z,
    before those cities' day-ahead evening."""
    import sys
    import types
    import common as real_common
    pairs = _pairs(days=30)
    written = {}
    common = types.ModuleType("common")

    def rest_all(path, params=None, **k):
        p = dict(params)
        if path == "cities":
            return [{"city_key": c, "timezone": "UTC"} for c in {q[0] for q in pairs}]
        if path == "derived_city_day_features":
            return [{"city_key": c, "obs_date": d, "max_c": y, "computed_at": "2026-09-26T05:00:00+00:00"}
                    for c, d, s, l, fc, y in pairs if s == "m1" and l == 1]
        if path == "weather_forecast_models" and p.get("source") == f"eq.{sc.FIT_SOURCE}":
            return [{"city_key": c, "model": s, "for_date": d, "lead_days": l, "forecast_max_c": fc}
                    for c, d, s, l, fc, y in pairs]
        if path == "weather_forecast_models":
            return [{"city_key": city, "model": s, "for_date": day, "lead_days": 1, "forecast_max_c": 25.0,
                     "run_at": "2026-09-26T00:00:00Z"}
                    for city in ("hot", "cold") for day in ("2026-09-27", "2026-09-28") for s in SOURCES]
        if path == "derived_station_correction":
            return []
        raise AssertionError(path)
    common.rest_all = rest_all
    common.day_had_ended = real_common.day_had_ended
    common.upsert_replace = lambda t, rows, key: (written.setdefault(t, rows), len(rows))[1]
    common.log_run = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "common", common)
    before = dt.datetime.now(dt.timezone.utc)
    sc.main(["--as-of", "2026-09-26"])
    after = dt.datetime.now(dt.timezone.utc)
    rows = written["derived_station_correction"] + written["derived_corrected_forecast"]
    assert len(written["derived_corrected_forecast"]) == 4
    stamps = {r.get("computed_at") for r in rows}
    assert len(stamps) == 1 and None not in stamps, "every row carries the run's one computed_at"
    t = dt.datetime.fromisoformat(stamps.pop())
    assert t.tzinfo is not None and before <= t <= after


def test_a_day_is_truth_only_once_it_has_ended():
    """derived_city_day_features' row for "today" holds the readings so far;
    on 26 Sep the 35 rows computed at 05:18Z were 4.1 C short on average."""
    import common
    assert common.day_had_ended("2026-09-26", "2026-09-27T05:20:00+00:00", "Europe/London")
    assert not common.day_had_ended("2026-09-26", "2026-09-26T05:18:00+00:00", "Europe/London")
    # Los Angeles' 26 Sep ends at 07:00 UTC on the 27th
    assert not common.day_had_ended("2026-09-26", "2026-09-27T05:20:00+00:00", "America/Los_Angeles")
    assert common.day_had_ended("2026-09-26", "2026-09-27T07:00:00+00:00", "America/Los_Angeles")
    # Tokyo's 26 Sep ended at 15:00 UTC on the 26th
    assert common.day_had_ended("2026-09-26", "2026-09-26T15:00:00Z", "Asia/Tokyo")
    assert not common.day_had_ended("2026-09-26", None, "Asia/Tokyo")


# ---------------------------------------------------------------------------
# A RERUN CANNOT STEP TWICE (plan v2.3 P5.14). The step is measured from the
# value in force BEFORE the night, which each row keeps beside its own value.
# ---------------------------------------------------------------------------
def test_the_anchor_is_the_newest_value_dated_before_the_night():
    last_night = {"bias_c": -0.25, "as_of": "2026-09-27", "prev_bias_c": None, "prev_as_of": None}
    assert sc.anchor_before(last_night, dt.date(2026, 9, 28)) == (-0.25, "2026-09-27")
    tonight = {"bias_c": -0.50, "as_of": "2026-09-28", "prev_bias_c": -0.25, "prev_as_of": "2026-09-27"}
    assert sc.anchor_before(tonight, dt.date(2026, 9, 28)) == (-0.25, "2026-09-27"), \
        "a re-run tonight steps from last night's value, not from tonight's first run"
    first_ever = {"bias_c": -1.75, "as_of": "2026-09-28", "prev_bias_c": None, "prev_as_of": None}
    assert sc.anchor_before(first_ever, dt.date(2026, 9, 28)) is None, \
        "a cell with nothing before tonight has no anchor on a re-run either, exactly as on the first run"


def _run_against(stored, pairs, as_of, monkeypatch):
    """One sc.main run against a table that returns `stored` and records what
    is written, the way the real table would on the next read."""
    import sys
    import types
    import common as real_common
    written = {}
    common = types.ModuleType("common")

    def rest_all(path, params=None, **k):
        p = dict(params)
        if path == "derived_city_day_features":
            return [{"city_key": c, "obs_date": d, "max_c": y, "computed_at": "2026-09-28T05:00:00+00:00"}
                    for c, d, s, l, fc, y in pairs if s == "m1" and l == 1]
        if path == "cities":
            return [{"city_key": c, "timezone": "UTC"} for c in {q[0] for q in pairs}]
        if path == "weather_forecast_models" and p.get("source") == f"eq.{sc.FIT_SOURCE}":
            return [{"city_key": c, "model": s, "for_date": d, "lead_days": l, "forecast_max_c": fc}
                    for c, d, s, l, fc, y in pairs]
        if path == "weather_forecast_models":
            return []
        if path == "derived_station_correction":
            return [dict(r) for r in stored]
        raise AssertionError(path)
    common.rest_all = rest_all
    common.day_had_ended = real_common.day_had_ended
    common.upsert_replace = lambda t, rows, key: (written.setdefault(t, rows), len(rows))[1]
    common.log_run = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "common", common)
    sc.main(["--as-of", as_of])
    return written["derived_station_correction"]


def test_a_rerun_the_same_night_writes_the_same_numbers(monkeypatch):
    """m1 runs 2 C cold at "hot", so its station error (station - model) is
    about +2 C. Last night held it at +0.25 on the way there. Tonight's run
    steps it to +0.50; a second run tonight, on the same data, must write
    +0.50 again, not +0.75 (as it did when the step was measured from the
    stored value, i.e. from tonight's own first run)."""
    pairs = _pairs(days=30)
    last_night = [{"city_key": "hot", "source": "m1", "lead_days": lead, "bias_c": 0.25, "as_of": "2026-09-27",
                   "prev_bias_c": None, "prev_as_of": None} for lead in sc.LEADS]
    first = _run_against(last_night, pairs, "2026-09-28", monkeypatch)
    cell = {(r["source"], r["lead_days"], r["city_key"]): r for r in first}
    for lead in sc.LEADS:
        r = cell[("m1", lead, "hot")]
        assert r["bias_c"] == pytest.approx(0.25 + sc.MAX_STEP_C)
        assert (r["prev_bias_c"], r["prev_as_of"]) == (0.25, "2026-09-27")
    second = _run_against(first, pairs, "2026-09-28", monkeypatch)
    assert [(r["city_key"], r["source"], r["lead_days"], r["bias_c"], r["prev_bias_c"], r["prev_as_of"])
            for r in second] == \
           [(r["city_key"], r["source"], r["lead_days"], r["bias_c"], r["prev_bias_c"], r["prev_as_of"])
            for r in first], "a same-night re-run changed a stored value"
    # the next night steps on from tonight's value, one step
    third = {(r["source"], r["lead_days"], r["city_key"]): r
             for r in _run_against(second, pairs, "2026-09-29", monkeypatch)}
    assert third[("m1", 1, "hot")]["bias_c"] == pytest.approx(0.25 + 2 * sc.MAX_STEP_C)
    assert third[("m1", 1, "hot")]["prev_as_of"] == "2026-09-28"


# ---------------------------------------------------------------------------
# The width around the combination (plan v2.3 P3.9 part 3)
# ---------------------------------------------------------------------------
def _errors(lead=1, cities=20, per_city=10, scale=None):
    """{(lead, city): errors}; city ci's errors are +/-(0.5 + 0.05 i)."""
    out = {}
    for i in range(cities):
        m = (scale or {}).get(f"c{i}", 0.5 + 0.05 * i)
        out[(lead, f"c{i}")] = [m if k % 2 else -m for k in range(per_city)]
    return out


def test_the_width_is_the_citys_error_shrunk_to_its_pool():
    errs = _errors()
    wt = sc.fit_width(errs)
    pool_mae = sum(0.5 + 0.05 * i for i in range(20)) / 20            # every city has 10 errors
    assert wt["pooled"][1][1] == pytest.approx(pool_mae)
    assert wt["pooled"][1][0] == pytest.approx(sc.MAE_TO_SIGMA * pool_mae)
    for i in (0, 7, 19):
        m = 0.5 + 0.05 * i
        want = sc.MAE_TO_SIGMA * (10 * m + sc.WIDTH_K * pool_mae) / (10 + sc.WIDTH_K)
        width, fitted, n, mae = wt["cells"][(1, f"c{i}")]
        assert (width, fitted, n) == (pytest.approx(want), pytest.approx(want), 10)
        assert mae == pytest.approx(m)


def test_below_min_n_a_city_sits_on_its_pool_and_a_new_city_gets_the_pool():
    errs = _errors()
    errs[(1, "thin")] = [5.0] * (sc.MIN_N - 1)
    wt = sc.fit_width(errs, cities={"newcomer"})
    assert wt["cells"][(1, "thin")][0] == pytest.approx(wt["pooled"][1][0])
    assert wt["cells"][(1, "newcomer")][:3] == (pytest.approx(wt["pooled"][1][0]),) * 2 + (0,)
    assert sc.width_for(wt, "nobody", 1) == pytest.approx(wt["pooled"][1][0])
    assert sc.width_for(wt, "c3", 0) == wt["cells"][(1, "c3")][0], "lead 0 uses lead 1, as correction"


def test_a_lead_with_too_few_errors_has_no_width_at_all():
    errs = _errors(cities=19)                                          # 190 < WIDTH_MIN_POOL
    assert sum(len(v) for v in errs.values()) < sc.WIDTH_MIN_POOL
    wt = sc.fit_width(errs)
    assert wt == {"cells": {}, "pooled": {}}
    assert sc.width_for(wt, "c1", 1) is None


def test_the_width_is_bounded_and_steps_at_most_ten_percent_a_night():
    huge = sc.fit_width(_errors(scale={f"c{i}": 9.0 for i in range(20)}))
    assert all(v[0] == sc.WIDTH_HI_C for v in huge["cells"].values())
    tiny = sc.fit_width(_errors(scale={f"c{i}": 0.01 for i in range(20)}))
    assert all(v[0] == sc.WIDTH_LO_C for v in tiny["cells"].values())
    errs = _errors()
    fresh = sc.fit_width(errs)
    stepped = sc.fit_width(errs, previous={(1, "c0"): 2.0, (1, "c19"): 0.6})
    assert stepped["cells"][(1, "c0")][0] == pytest.approx(2.0 * (1 - sc.WIDTH_MAX_STEP))
    assert stepped["cells"][(1, "c19")][0] == pytest.approx(0.6 * (1 + sc.WIDTH_MAX_STEP))
    assert stepped["cells"][(1, "c0")][1] == fresh["cells"][(1, "c0")][1], "fitted is before the step"
    assert stepped["cells"][(1, "c5")] == fresh["cells"][(1, "c5")], "no previous, no step"


def test_the_widths_errors_never_see_the_day_they_score_or_later():
    pairs = _pairs(days=40)
    days = sorted({p[1] for p in pairs})
    base = sc.oos_errors(pairs, days[-10:])
    cut = days[-5]
    poisoned = [(c, d, s, l, fc, y + (50.0 if d >= cut else 0.0)) for c, d, s, l, fc, y in pairs]
    after = sc.oos_errors(poisoned, days[-10:])
    for key in base:
        n_before = sum(1 for d in days[-10:] if d < cut)
        assert after[key][:n_before] == pytest.approx(base[key][:n_before]), \
            "a day's error changed when only later days were poisoned"
    assert set(base) == {(lead, c) for lead in sc.LEADS for c in ("hot", "cold")}


def test_the_version_names_the_night_and_changes_with_a_width():
    wt = sc.fit_width(_errors())
    v = sc.width_version_of(wt, dt.date(2026, 9, 28))
    assert v.startswith("station-width:2026-09-28:") and v == sc.width_version_of(wt, dt.date(2026, 9, 28))
    other = sc.fit_width(_errors(scale={"c0": 3.0}))
    assert sc.width_version_of(other, dt.date(2026, 9, 28)) != v


def _width_run(stored_widths, pairs, as_of, monkeypatch, width_boom=False):
    """One sc.main run; returns everything written and the log."""
    import sys
    import types
    import common as real_common
    written, logged = {}, []
    common = types.ModuleType("common")

    def rest_all(path, params=None, **k):
        p = dict(params)
        if path == "derived_city_day_features":
            return [{"city_key": c, "obs_date": d, "max_c": y, "computed_at": "2026-09-28T05:00:00+00:00"}
                    for c, d, s, l, fc, y in pairs if s == "m1" and l == 1]
        if path == "cities":
            return [{"city_key": c, "timezone": "UTC"} for c in {q[0] for q in pairs}]
        if path == "weather_forecast_models" and p.get("source") == f"eq.{sc.FIT_SOURCE}":
            return [{"city_key": c, "model": s, "for_date": d, "lead_days": l, "forecast_max_c": fc}
                    for c, d, s, l, fc, y in pairs]
        if path == "weather_forecast_models":
            return [{"city_key": city, "model": s, "for_date": day, "lead_days": lead, "forecast_max_c": 25.0,
                     "run_at": "2026-09-27T00:00:00Z"}
                    for city in ("hot", "cold") for day, lead in (("2026-09-29", 1), ("2026-09-30", 2))
                    for s in SOURCES]
        if path == "derived_station_correction":
            return []
        if path == "derived_station_width":
            if width_boom:
                raise RuntimeError("relation derived_station_width does not exist")
            return [dict(r) for r in stored_widths]
        raise AssertionError(path)
    common.rest_all = rest_all
    common.day_had_ended = real_common.day_had_ended
    common.upsert_replace = lambda t, rows, key: (written.setdefault(t, rows), len(rows))[1]
    common.log_run = lambda job, status, rows, detail: logged.append((job, status, rows, detail))
    monkeypatch.setitem(sys.modules, "common", common)
    monkeypatch.setattr(sc, "WIDTH_MIN_POOL", 40)          # two synthetic cities, 30 days
    sc.main(["--as-of", as_of])
    return written, logged


def test_a_run_stores_the_width_and_every_open_day_carries_it(monkeypatch):
    pairs = _pairs(days=40, start=dt.date(2026, 8, 19))
    written, logged = _width_run([], pairs, "2026-09-28", monkeypatch)
    widths = {(r["lead_days"], r["city_key"]): r for r in written["derived_station_width"]}
    assert set(widths) == {(lead, c) for lead in sc.LEADS for c in ("hot", "cold")}
    version = next(iter(widths.values()))["version"]
    assert version.startswith("station-width:2026-09-28:")
    for r in widths.values():
        assert r["sigma_c"] == r["fitted_sigma_c"] and r["prev_sigma_c"] is None and r["as_of"] == "2026-09-28"
        assert sc.WIDTH_LO_C <= r["sigma_c"] <= sc.WIDTH_HI_C and r["n"] == 30
    for f in written["derived_corrected_forecast"]:
        assert f["width_c"] == widths[(f["lead_days"], f["city_key"])]["sigma_c"]
        assert f["width_version"] == version
    job, status, _n, detail = logged[0]
    assert status == "ok" and detail["width"]["version"] == version and detail["width"]["days"] == 30
    assert detail["priors"]["width_max_step"] == sc.WIDTH_MAX_STEP


def test_a_rerun_the_same_night_writes_the_same_widths(monkeypatch):
    pairs = _pairs(days=40, start=dt.date(2026, 8, 19))
    last_night = [{"city_key": c, "lead_days": lead, "sigma_c": 1.0, "as_of": "2026-09-27",
                   "prev_sigma_c": None, "prev_as_of": None} for c in ("hot", "cold") for lead in sc.LEADS]
    first, _ = _width_run(last_night, pairs, "2026-09-28", monkeypatch)
    second, _ = _width_run(first["derived_station_width"], pairs, "2026-09-28", monkeypatch)
    key = lambda rows: [(r["city_key"], r["lead_days"], r["sigma_c"], r["prev_sigma_c"], r["prev_as_of"])
                        for r in rows]
    assert key(second["derived_station_width"]) == key(first["derived_station_width"])
    for r in first["derived_station_width"]:
        assert (r["prev_sigma_c"], r["prev_as_of"]) == (1.0, "2026-09-27")
        assert 0.9 - 1e-9 <= r["sigma_c"] <= 1.1 + 1e-9, "one step from last night, not two"


def test_a_failed_width_read_costs_the_width_not_the_correction(monkeypatch):
    pairs = _pairs(days=40, start=dt.date(2026, 8, 19))
    written, logged = _width_run([], pairs, "2026-09-28", monkeypatch, width_boom=True)
    assert "derived_station_width" not in written, "no width is fitted without its step bound"
    assert written["derived_station_correction"] and written["derived_corrected_forecast"]
    assert all(f["width_c"] is None and f["width_version"] is None for f in written["derived_corrected_forecast"])
    job, status, _n, detail = logged[0]
    assert status == "attention" and "derived_station_width" in detail["width"]["error"]
