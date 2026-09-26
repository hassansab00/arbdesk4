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
    pairs = _pairs(days=30)
    written, logged = {}, []
    common = types.ModuleType("common")

    def rest_all(path, params=None, **k):
        p = dict(params)
        if path == "derived_city_day_features":
            return [{"city_key": c, "obs_date": d, "max_c": y}
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
    common.upsert_replace = lambda t, rows, key: written.setdefault(t, rows) and len(rows)
    common.log_run = lambda job, status, rows, detail: logged.append((job, status, rows, detail))
    monkeypatch.setitem(sys.modules, "common", common)
    d = sc.main(["--as-of", "2026-09-26"])
    assert len(written["derived_station_correction"]) == 2 * len(SOURCES) * len(sc.LEADS)
    assert written["derived_corrected_forecast"][0]["city_key"] == "hot"
    assert logged[0][0] == "P3.9_station_correction" and d["walk_forward"]["n"] > 0
