"""The station correction learned from the served forecasts, skill-weighted
(own_w:v1 built into scripts/station_correction.py, Hassan 16 Nov 2026).

What must hold: with the learner off nothing changes (the same combination and
the same version string); with it on, leads 0-2 come from the served forecasts
with weights that are bounded, stepped from the weight in force (1 before the
first night) and identical in target to the shadow learner that was measured
(tools/own_w_forward.target); lead 3 keeps the previous-runs fit; a rerun the
same night writes the same numbers."""
import datetime as dt
import hashlib
import json
import os
import random
import sys

import pytest

import station_correction as sc

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES = ["m1", "m2", "m3", "m4", "m5"]
NOISE = {"m1": 0.3, "m2": 0.3, "m3": 1.5, "m4": 0.6, "m5": 0.6}     # m3 is the poor model
BIAS = {("m1", "hot"): -2.0, ("m2", "hot"): 1.0}


def _pairs(days=30, cities=("hot", "cold"), leads=(0, 1, 2), seed=3, start=dt.date(2026, 8, 20), bias=None):
    bias = BIAS if bias is None else bias
    rng = random.Random(seed)
    out = []
    for i in range(days):
        day = (start + dt.timedelta(days=i)).isoformat()
        for city in cities:
            truth = 25 + rng.gauss(0, 3)
            for s in SOURCES:
                for lead in leads:
                    out.append((city, day, s, lead, truth + bias.get((s, city), 0.0) + rng.gauss(0, NOISE[s]), truth))
    return out


def test_without_weights_the_combination_and_version_are_what_they_were():
    t = sc.fit([p for p in _pairs(leads=(1, 2, 3))])
    fcs = {s: 25.0 + i for i, s in enumerate(SOURCES)}
    mean, spread, used = sc.combine(fcs, t, "hot", 1)
    vals = list(used.values())
    assert mean == sum(vals) / len(vals)
    old = json.dumps({"as_of": "2026-09-26", "k": sc.K, "min_n": sc.MIN_N, "bound": sc.BOUND_C, "step": sc.MAX_STEP_C,
                      "cells": sorted((list(k) + [round(v[0], 6), v[1]]) for k, v in t["cells"].items())}, default=str)
    assert sc.version_of(t, "2026-09-26") == f"station-correction:2026-09-26:{hashlib.sha256(old.encode()).hexdigest()[:10]}"


def test_with_weights_the_combination_is_the_weighted_mean_and_the_version_names_them():
    t = sc.fit(_pairs(leads=(1,)))
    v0 = sc.version_of(t, "2026-09-26")
    t["weights"] = {(s, 1, "hot"): w for s, w in zip(SOURCES, (2.0, 1.0, 0.5, 1.0, 1.0))}
    fcs = {s: 25.0 + i for i, s in enumerate(SOURCES)}
    mean, _spread, used = sc.combine(fcs, t, "hot", 1)
    ws = (2.0, 1.0, 0.5, 1.0, 1.0)
    assert mean == pytest.approx(sum(used[s] * w for s, w in zip(SOURCES, ws)) / sum(ws))
    assert sc.version_of(t, "2026-09-26") != v0


def test_a_lead_zero_cell_serves_lead_zero_and_without_one_lead_one_does():
    t = sc.fit(_pairs(leads=(1,)))
    assert sc.correction(t, "m1", 0, "hot") == t["cells"][("m1", 1, "hot")][0]
    t["cells"][("m1", 0, "hot")] = (0.123, 30)
    assert sc.correction(t, "m1", 0, "hot") == 0.123


def test_weights_are_bounded_and_step_from_one_on_the_first_night():
    """No station biases here, so a model's weight follows its noise: m1 and m2
    (0.3 C) gain, m3 (1.5 C) loses."""
    pairs = _pairs(bias={}, days=40)
    w, pools = sc.fit_weights(pairs)
    assert w and all(sc.REL_PRIOR - sc.REL_MAX_STEP - 1e-12 <= v <= sc.REL_PRIOR + sc.REL_MAX_STEP + 1e-12
                     for v in w.values())
    for city in ("hot", "cold"):
        assert w[("m3", 1, city)] == pytest.approx(sc.REL_PRIOR - sc.REL_MAX_STEP), "the poor model loses weight"
        assert w[("m1", 1, city)] == pytest.approx(sc.REL_PRIOR + sc.REL_MAX_STEP)
    assert all(sc.REL_LO <= v <= sc.REL_HI for v in pools.values())
    # thirty nights later every weight sits inside its bounds, the poor model at the floor
    prev = {}
    for _ in range(30):
        prev, _p = sc.fit_weights(pairs, prev)
    assert all(sc.REL_LO <= v <= sc.REL_HI for v in prev.values())
    assert prev[("m3", 1, "cold")] == sc.REL_LO and prev[("m1", 1, "cold")] > 1.2


def test_a_city_with_no_data_steps_toward_its_pool_weight_not_onto_it():
    """Codex on #521: the pool's weight served unstepped would jump a city with
    no data tonight straight to 0.5 or 2.0."""
    t = sc.fit(_pairs(leads=(1,)))
    t["weights"] = {}
    t["pool_weights"] = {("m3", 1): sc.REL_LO, ("m1", 1): sc.REL_HI}
    assert sc.weight(t, "m3", 1, "nowhere") == pytest.approx(sc.REL_PRIOR - sc.REL_MAX_STEP)
    assert sc.weight(t, "m1", 1, "nowhere") == pytest.approx(sc.REL_PRIOR + sc.REL_MAX_STEP)
    t["prev_weights"] = {("m3", 1, "nowhere"): 0.7}
    assert sc.weight(t, "m3", 1, "nowhere") == pytest.approx(0.6), "from the weight in force, one step"
    assert sc.weight(t, "m2", 1, "nowhere") == sc.REL_PRIOR, "no pool weight: equal weight"


def test_the_weight_target_is_the_shadow_learners_target():
    """fit_weights with each previous weight already at its target returns the
    target itself; that target equals tools/own_w_forward.target's."""
    pytest.importorskip("numpy")
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import own_w_forward as O
    pairs = _pairs(cities=("hot", "cold", "thin"), days=30)
    pairs = [p for p in pairs if not (p[0] == "thin" and p[1] > "2026-08-22")]       # 3 days: on its pool
    for lead in (0, 1, 2):
        idx = {s: i for i, s in enumerate(SOURCES)}
        errors = {}
        for c, _d, s, l, fc, y in pairs:
            if l == lead:
                errors.setdefault((c, idx[s]), []).append(y - fc)
        tgt = O.target(errors)
        prev = {(s, lead, c): tgt["rel"][(c, idx[s])] for c in ("hot", "cold", "thin") for s in SOURCES}
        w, pools = sc.fit_weights([p for p in pairs if p[3] == lead], prev)
        for c in ("hot", "cold", "thin"):
            for s in SOURCES:
                assert w[(s, lead, c)] == pytest.approx(tgt["rel"][(c, idx[s])], abs=1e-9), (lead, c, s)
        for s in SOURCES:
            assert pools[(s, lead)] == pytest.approx(tgt["pool_rel"][idx[s]], abs=1e-9)


def test_own_pairs_take_each_models_newest_run_of_each_run_date():
    rows = [
        {"city_key": "hot", "model": "m1", "for_date": "2026-09-10", "lead_days": 1, "forecast_max_c": 20.0,
         "run_at": "2026-09-09T00:00:00Z"},
        {"city_key": "hot", "model": "m1", "for_date": "2026-09-10", "lead_days": 1, "forecast_max_c": 21.0,
         "run_at": "2026-09-09T12:00:00Z"},
        {"city_key": "hot", "model": "m2", "for_date": "2026-09-10", "lead_days": 1, "forecast_max_c": 22.0,
         "run_at": "2026-09-09T06:00:00Z"},
        {"city_key": "hot", "model": "m1", "for_date": "2026-09-10", "lead_days": 0, "forecast_max_c": 23.0,
         "run_at": "2026-09-10T06:00:00Z"},
        {"city_key": "hot", "model": "m1", "for_date": "2026-09-11", "lead_days": 1, "forecast_max_c": 9.0,
         "run_at": "2026-09-10T06:00:00Z"},           # no label: not a pair
    ]
    pairs = sc.own_pairs(rows, {("hot", "2026-09-10"): 22.5})
    assert sorted(pairs) == sorted([("hot", "2026-09-10", "m1", 1, 21.0, 22.5), ("hot", "2026-09-10", "m2", 1, 22.0, 22.5),
                                    ("hot", "2026-09-10", "m1", 0, 23.0, 22.5)])


def test_the_learner_is_own_w_only_when_the_setting_names_it_and_is_enabled():
    def ra(value):
        return lambda path, params=None, **k: [{"key": "station_correction_learner", "value": value}]
    assert sc.learner_of(ra({"enabled": True, "learner": "own_w:v1"})) == sc.OWN_W
    assert sc.learner_of(ra(json.dumps({"enabled": True, "learner": "own_w:v1"}))) == sc.OWN_W
    assert sc.learner_of(ra({"enabled": False, "learner": "own_w:v1"})) == sc.P39
    assert sc.learner_of(ra({"enabled": True, "learner": "other"})) == sc.P39
    assert sc.learner_of(lambda *a, **k: []) == sc.P39

    def boom(*a, **k):
        raise RuntimeError("down")
    assert sc.learner_of(boom) == sc.P39


def _main(monkeypatch, stored, as_of, learner=True):
    import types
    import common as real_common
    fit_pairs = _pairs(days=40, leads=(1, 2, 3), start=dt.date(2026, 8, 25))
    truth = {(c, d): y for c, d, s, l, fc, y in fit_pairs if s == "m1" and l == 1}
    rng = random.Random(5)
    own = [(c, d, s, l, y + rng.gauss(0, NOISE[s]), y) for (c, d), y in sorted(truth.items())
           for s in SOURCES for l in (0, 1, 2)]
    written = {}
    common = types.ModuleType("common")

    def rest_all(path, params=None, **k):
        p = dict(params)
        if path == "cities":
            return [{"city_key": c, "timezone": "UTC"} for c in ("hot", "cold")]
        if path == "derived_city_day_features":
            return [{"city_key": c, "obs_date": d, "max_c": y, "computed_at": "2026-10-30T05:00:00+00:00"}
                    for (c, d), y in truth.items()]
        if path == "weather_forecast_models" and p.get("source") == f"eq.{sc.FIT_SOURCE}":
            return [{"city_key": c, "model": s, "for_date": d, "lead_days": l, "forecast_max_c": fc}
                    for c, d, s, l, fc, y in fit_pairs]
        if path == "weather_forecast_models" and "run_at" in p.get("select", "") and p.get("lead_days") == "lte.2":
            return [{"city_key": c, "model": s, "for_date": d, "lead_days": l, "forecast_max_c": fc,
                     "run_at": (dt.date.fromisoformat(d) - dt.timedelta(days=l)).isoformat() + "T06:00:00Z"}
                    for c, d, s, l, fc, y in own if (c, d) in truth]
        if path == "weather_forecast_models":
            return [{"city_key": "hot", "model": s, "for_date": as_of, "lead_days": lead, "forecast_max_c": 25.0 + i,
                     "run_at": f"{as_of}T00:00:00Z"} for i, s in enumerate(SOURCES) for lead in (0,)]
        if path == "settings":
            if p.get("key") == "eq.station_correction_learner":
                return [{"key": "station_correction_learner",
                         "value": {"enabled": learner, "learner": "own_w:v1"}}]
            return [{"value": {"source": "station"}}]
        if path == "v_venue_truth":
            return []
        if path == "derived_station_correction":
            return stored
        if path in ("derived_station_width", "ingest_log"):
            return []
        raise AssertionError((path, p))
    common.rest_all = rest_all
    common.day_had_ended = real_common.day_had_ended
    common.get_cities = lambda **k: []
    common.upsert_replace = lambda t, rows, key: written.setdefault(t, rows) and len(rows)
    common.log_run = lambda *a: None
    monkeypatch.setitem(sys.modules, "common", common)
    d = sc.main(["--as-of", as_of])
    return d, written


def test_a_night_with_own_w_writes_weighted_leads_0_to_2_and_keeps_lead_3(monkeypatch):
    d, w = _main(monkeypatch, [], "2026-10-05")
    rows = w["derived_station_correction"]
    assert d["learner"] == sc.OWN_W and d["own_w"]["weights"] > 0
    # the active learner is scored too (Codex on #521), on served forecasts
    wf = d["own_w"]["walk_forward"]["by_lead"]
    assert wf["1"]["n"] > 0 and wf["1"]["mae_own_bias_weighted"] <= wf["1"]["mae_raw_mean"]
    assert "previous-runs" in d["walk_forward_is"]
    by = {(r["source"], r["lead_days"], r["city_key"]): r for r in rows}
    for lead in (0, 1, 2):
        r = by[("m3", lead, "hot")]
        assert r["learner"] == sc.OWN_W
        assert sc.REL_PRIOR - sc.REL_MAX_STEP - 1e-9 <= r["rel_weight"] < sc.REL_PRIOR, "the poor model loses weight"
    assert by[("m1", 3, "hot")]["learner"] == sc.P39 and by[("m1", 3, "hot")]["rel_weight"] is None
    fc = w["derived_corrected_forecast"][0]
    assert fc["lead_days"] == 0 and fc["version"] == d["version"]
    # the lead-0 combination is the weighted mean of the lead-0 cells
    ws = {s: by[(s, 0, "hot")]["rel_weight"] for s in SOURCES}
    assert fc["combined_c"] == pytest.approx(sum(fc["sources"][s] * ws[s] for s in SOURCES) / sum(ws.values()), abs=1e-3)
    # a rerun the same night writes the same numbers; the next night steps once more
    _d2, w2 = _main(monkeypatch, rows, "2026-10-05")
    key = lambda rs: sorted((r["city_key"], r["source"], r["lead_days"], r["bias_c"], r["rel_weight"]) for r in rs)
    assert key(w2["derived_station_correction"]) == key(rows)
    _d3, w3 = _main(monkeypatch, w2["derived_station_correction"], "2026-10-06")
    nxt = {(r["source"], r["lead_days"], r["city_key"]): r for r in w3["derived_station_correction"]}
    first = by[("m3", 1, "hot")]["rel_weight"]
    assert nxt[("m3", 1, "hot")]["prev_rel_weight"] == first
    assert first - sc.REL_MAX_STEP - 1e-9 <= nxt[("m3", 1, "hot")]["rel_weight"] <= first + sc.REL_MAX_STEP + 1e-9


def test_switched_off_every_lead_is_the_previous_runs_fit_and_the_weights_are_cleared(monkeypatch):
    d, w = _main(monkeypatch, [], "2026-10-05", learner=False)
    assert d["learner"] == sc.P39 and d["own_w"] is None
    rows = w["derived_station_correction"]
    assert {r["lead_days"] for r in rows} == set(sc.LEADS)
    assert all(r["learner"] == sc.P39 and r["rel_weight"] is None for r in rows)


def test_the_migration_seeds_the_switch_on_and_bounds_the_weights():
    sql = open(os.path.join(ROOT, "supabase", "migrations",
                            "20261116130000_station_correction_learns_from_served_forecasts.sql")).read()
    assert "'station_correction_learner'" in sql and "'enabled', true" in sql and "'learner', 'own_w:v1'" in sql
    assert "rel_weight between 0.5 and 2.0" in sql
    assert sc.REL_LO == 0.5 and sc.REL_HI == 2.0
