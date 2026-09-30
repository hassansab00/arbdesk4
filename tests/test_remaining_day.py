"""Plan v2 P7.2 stage 1: the remaining-day distribution (scripts/remaining_day.py).

The plan's tests: feature functions are pure, no feature reads a reading after
the decision time, the ladder sums to 1, the P3.1 atom invariants hold; plus
the Rule 11 guards (minimum samples, a city shrunk to the pool, bounds, a
version on every fit)."""
import datetime as dt
import math
import random

import pytest

import remaining_day as rd

DAY = dt.date(2026, 7, 15)


def _forecast(peak=24.0, peak_hour=15):
    return {h: (peak - 0.12 * (h - peak_hour) ** 2, 40.0, 300.0 if 6 <= h <= 18 else 0.0) for h in range(24)}


def _readings(upto=23.5, base=14.0):
    return [(h + m / 60, base + 0.6 * min(h + m / 60, 15) - 0.3 * max(0, h + m / 60 - 15))
            for h in range(24) for m in (0, 30) if h + m / 60 <= upto]


def test_features_read_nothing_after_the_decision_hour():
    before = rd.features(_readings(upto=11.0), _forecast(), 11, DAY, 1.0)
    # the rest of the day's readings, including a heatwave at 16:00, change nothing
    later = _readings(upto=23.5) + [(16.0, 45.0)]
    assert rd.features(later, _forecast(), 11, DAY, 1.0) == before
    assert before["R"] == max(v for h, v in _readings(upto=11.0))


def test_features_are_pure():
    readings, fc = _readings(upto=13.0), _forecast()
    snap = (list(readings), dict(fc))
    a = rd.features(readings, fc, 13, DAY, 0.8)
    b = rd.features(list(reversed(readings)), fc, 13, DAY, 0.8)
    assert a == b and (readings, fc) == snap
    assert len(a["x"]) == len(rd.FEATURES)


def test_too_little_to_go_on_is_no_row():
    assert rd.features(_readings(upto=1.0), _forecast(), 9, DAY, 1.0) is None       # no reading near 09
    assert rd.features(_readings(), {h: v for h, v in _forecast().items() if h < 10}, 9, DAY, 1.0) is None
    assert rd.features(_readings(), _forecast(), 20, DAY, 1.0) is None              # < 6 forecast hours left


def _synthetic(n_days=60, cities=("a", "b", "c", "d"), hour=11, seed=3, shift=None):
    """Days whose maximum is the forecast's plus noise, with city biases."""
    rng = random.Random(seed)
    rows = []
    for c_i, c in enumerate(cities):
        for k in range(n_days):
            day = dt.date(2026, 1, 1) + dt.timedelta(days=k)
            peak = 15 + 10 * rng.random()
            bias = (shift or {}).get(c, 0.0)
            f = rd.features(_readings(upto=hour, base=peak - 9 + rng.gauss(0, 0.7)), _forecast(peak), hour, day,
                            0.5 + rng.random())
            y = max(f["R"], peak + bias + rng.gauss(0, 0.8))
            f.update(city=c, date=day, y=y)
            rows.append(f)
    return rows


@pytest.fixture(scope="module")
def fitted():
    rows = _synthetic(n_days=150, cities=("a", "b", "c", "d", "tiny"), shift={"a": 2.0})
    # "tiny" has too few days to leave the pool (Rule 11)
    rows = [r for r in rows if r["city"] != "tiny" or r["date"] < dt.date(2026, 1, 1) + dt.timedelta(days=10)]
    old = rd.MIN_TRAIN_ROWS
    rd.MIN_TRAIN_ROWS = 100
    try:
        return rows, rd.fit_hour(rows)
    finally:
        rd.MIN_TRAIN_ROWS = old


def test_an_hour_below_its_minimum_sample_is_not_fitted():
    assert rd.fit_hour(_synthetic(n_days=10)) is None       # 40 rows < MIN_TRAIN_ROWS


def test_a_city_leaves_the_pool_only_with_enough_days(fitted):
    rows, p = fitted
    assert "tiny" not in p["b_city"] and "tiny" not in p["a_city"]
    assert set(p["b_city"]) == {"a", "b", "c", "d"}
    # the city whose maxima run 2 C above the forecast learned it
    r = next(r for r in rows if r["city"] == "b")
    hot = rd.median(rd.distribution(p, "a", r["x"], r["R"]))
    plain = rd.median(rd.distribution(p, "b", r["x"], r["R"]))
    assert 1.0 < hot - plain < 3.0
    # a city it has never seen gets the pool
    assert rd.distribution(p, "nowhere", r["x"], r["R"]) == rd.distribution(p, "tiny", r["x"], r["R"])


def test_the_forecast_dominates_when_the_morning_says_little(fitted):
    """Plan P7.2: before the day has shown much, the prediction follows the
    forecast, not the running maximum."""
    rows, p = fitted
    early = [r for r in rows if r["city"] in ("b", "c", "d") and r["fc_day"] - r["R"] > 2]
    assert len(early) > 200                      # most of the synthetic mornings are far below the day
    errs = [abs(rd.median(rd.distribution(p, r["city"], r["x"], r["R"])) - r["fc_day"]) for r in early]
    to_floor = [abs(rd.median(rd.distribution(p, r["city"], r["x"], r["R"])) - r["R"]) for r in early]
    assert sum(errs) / len(errs) < 0.8 < sum(to_floor) / len(to_floor)


def test_the_distribution_is_a_maximum_distribution(fitted):
    rows, p = fitted
    for r in rows[:50]:
        d = rd.distribution(p, r["city"], r["x"], r["R"])
        assert rd.cdf(d, r["R"] - 0.01) == 0.0                   # a maximum cannot go down
        vs = [r["R"] + 0.05 * i for i in range(400)]
        cs = [rd.cdf(d, v) for v in vs]
        assert all(b >= a - 1e-12 for a, b in zip(cs, cs[1:]))
        assert cs[-1] > 0.999
        assert r["R"] <= rd.quantile(d, 0.1) <= rd.median(d) <= rd.quantile(d, 0.9)


def test_every_learned_output_is_bounded():
    p = {"mu": [0.0] * len(rd.FEATURES), "sd": [1.0] * len(rd.FEATURES),
         "logit": [0.0] * (len(rd.FEATURES) + 1), "a_city": {}, "b_city": {},
         "a_pool": [99.0] + [0.0] * len(rd.FEATURES), "a_scale": [-5.0] + [0.0] * len(rd.FEATURES),
         "b_pool": [-99.0] + [0.0] * len(rd.FEATURES), "b_scale": [1e6] + [0.0] * len(rd.FEATURES)}
    d = rd.distribution(p, "x", [0.0] * len(rd.FEATURES), 20.0)
    assert d["log_rise"] == rd.BOUNDS["log_rise"][1] and d["scale_a"] == rd.BOUNDS["scale_a"][0]
    assert d["centre"] == 20.0 + rd.BOUNDS["rise_b"][0] and d["scale_b"] == rd.BOUNDS["scale_b"][1]


LADDER_C = ([{"band_id": "<=21", "band_lo": None, "band_hi": 22, "open_low": True, "open_high": False}]
            + [{"band_id": str(k), "band_lo": k, "band_hi": k + 1, "open_low": False, "open_high": False}
               for k in range(22, 29)]
            + [{"band_id": ">=29", "band_lo": 29, "band_hi": None, "open_low": False, "open_high": True}])


def _d(R=25.0, p_set=0.3, centre=26.5):
    return {"R": R, "p_set": p_set, "log_rise": math.log(1.5), "scale_a": 0.5, "centre": centre, "scale_b": 1.0}


def test_the_ladder_sums_to_one_and_keeps_the_atom_invariants():
    d = _d()
    probs = dict(rd.ladder_probabilities(d, "C", LADDER_C))
    assert abs(sum(probs.values()) - 1.0) < 1e-12
    # R = 25.0 reads 25: nothing below it without the measurement layer
    assert all(probs[b] == 0.0 for b in ("<=21", "22", "23", "24"))
    assert probs["25"] == pytest.approx(rd.cdf(d, 25.5))
    # with it, the bucket below keeps q_down of the atom and nothing lower gets any
    q = dict(rd.ladder_probabilities(d, "C", LADDER_C, q_down=0.02, q_up=0.05))
    assert q["24"] == pytest.approx(0.02 * rd.cdf(d, 25.5)) and q["23"] == 0.0
    assert abs(sum(q.values()) - 1.0) < 1e-12


def test_the_ladder_matches_the_whole_degree_score_above_the_floor():
    d = _d()
    probs = dict(rd.ladder_probabilities(d, "C", LADDER_C))
    for k in (26, 27, 28):
        assert probs[str(k)] == pytest.approx(rd.whole_bucket_probability(d, "C", k))


def test_fahrenheit_edges_follow_the_whole_degree_lattice():
    ladder = [{"band_id": "<=77", "band_lo": None, "band_hi": 78, "open_low": True, "open_high": False},
              {"band_id": "78-79", "band_lo": 78, "band_hi": 80, "open_low": False, "open_high": False},
              {"band_id": "80-81", "band_lo": 80, "band_hi": 82, "open_low": False, "open_high": False},
              {"band_id": ">=82", "band_lo": 82, "band_hi": None, "open_low": False, "open_high": True}]
    d = _d(R=(80 - 32) * 5 / 9, centre=27.5)          # read 80 F
    probs = dict(rd.ladder_probabilities(d, "F", ladder))
    assert probs["<=77"] == 0.0 and probs["78-79"] == 0.0
    assert probs["80-81"] == pytest.approx(rd.cdf(d, (81.5 - 32) * 5 / 9))
    assert abs(sum(probs.values()) - 1.0) < 1e-12


def test_every_fit_carries_a_version_that_names_it(fitted):
    _, p = fitted
    params, version = {11: p}, rd.version_of({11: p})
    assert version.startswith(f"{rd.VERSION_PREFIX}:{p['last_date']}:") and version == rd.version_of(params)
    other = dict(p, b_scale=[v + 1e-3 for v in p["b_scale"]])
    assert rd.version_of({11: other}) != version
    blob = rd.to_json(params, version)
    assert version in blob and '"weight_a": 0.5' in blob


def test_the_width_factor_is_learned_on_training_days_and_bounded(fitted):
    """Rule 11 for the calibrated width: prior 1.0, inside WIDEN_GRID's range,
    chosen on the training window's last days only, carried in the version."""
    rows, p = fitted
    assert rd.WIDEN_GRID[0] == 1.0 and max(rd.WIDEN_GRID) <= 1.6
    assert p["widen"] in rd.WIDEN_GRID
    # too few inner rows to say: stays on the prior
    assert rd.choose_widen(rows[:300]) == (1.0, None, rd.choose_widen(rows[:300])[2])
    wide = dict(p, widen=1.3)
    r = rows[0]
    d1, d13 = rd.distribution(dict(p, widen=1.0), r["city"], r["x"], r["R"]), rd.distribution(wide, r["city"], r["x"], r["R"])
    assert d13["scale_b"] == pytest.approx(min(rd.BOUNDS["scale_b"][1], d1["scale_b"] * 1.3))
    assert rd.version_of({11: wide}) != rd.version_of({11: dict(p, widen=1.0)})


def test_the_width_factor_reaches_its_coverage_target_on_the_inner_days():
    rows = _synthetic(n_days=150, cities=("a", "b", "c", "d"))
    old = (rd.MIN_TRAIN_ROWS, rd.MIN_INNER_ROWS)
    rd.MIN_TRAIN_ROWS, rd.MIN_INNER_ROWS = 100, 50
    try:
        k, cover, n = rd.choose_widen(rows)
    finally:
        rd.MIN_TRAIN_ROWS, rd.MIN_INNER_ROWS = old
    assert n == len([r for r in rows if r["date"] >= sorted({r["date"] for r in rows})[120]])
    assert cover >= rd.TARGET_COVER or k == rd.WIDEN_GRID[-1]


# ---------------------------------------------------------------------------
# Challenger A: the readings received by the decision time (rd2, 30 Sep;
# docs/CHALLENGER_A_PREREG.md). A separate feature contract.
# ---------------------------------------------------------------------------
import datetime as _dt


def _day_inputs():
    forecast = {h: (15.0 + 8 * math.sin(math.pi * max(0, h - 6) / 14), 40.0, 300.0) for h in range(24)}
    readings = [(h + m / 60, 15.0 + 8 * math.sin(math.pi * max(0, h + m / 60 - 6) / 14))
                for h in range(24) for m in (0, 30)]
    return forecast, readings


def test_rd2_is_a_separate_contract():
    assert rd.VERSION_PREFIX_AT == "rd2" != rd.VERSION_PREFIX
    assert rd.FEATURES_AT[:len(rd.FEATURES)] == rd.FEATURES
    assert rd.FEATURES_AT[len(rd.FEATURES):] == ["obs_age_h", "min_frac"]
    assert rd.version_of({13: {"last_date": "2026-07-31"}}, prefix="rd2").startswith("rd2:2026-07-31:")


def test_a_reading_after_the_hour_reaches_the_decision_only_through_rd2():
    """13:36 decision, a reading at 13:30 already received: rd1 cuts at 13:00."""
    forecast, readings = _day_inputs()
    day = _dt.date(2026, 7, 15)
    avail = rd.available(readings, 13.6, lag_h=0)
    assert max(t for t, _ in avail) == 13.5
    a = rd.features_at(avail, forecast, 13.6, day, 1.0)
    b = rd.features(avail, forecast, 13, day, 1.0)
    assert a["latest_at"] == 13.5 and abs(a["obs_age_h"] - 0.1) < 1e-9
    assert a["now"] != b["now"], "rd1 reads the 13:00 reading, rd2 the 13:30 one"


def test_a_later_reading_cannot_change_an_earlier_prediction():
    forecast, readings = _day_inputs()
    day = _dt.date(2026, 7, 15)
    base = rd.features_at(rd.available(readings, 12.6), forecast, 12.6, day, 1.0)
    later = readings + [(12.95, 40.0), (15.0, 45.0)]      # a spike after the decision
    again = rd.features_at(rd.available(later, 12.6), forecast, 12.6, day, 1.0)
    assert base == again


def test_a_reading_not_yet_received_is_not_available():
    """Received 10 min after its valid time (the historical rule): at 13:36 the
    13:30 reading is not in, the 13:00 one is."""
    _, readings = _day_inputs()
    avail = rd.available(readings, 13.6)
    assert max(t for t, _ in avail) == 13.0


def test_a_stale_latest_reading_gives_no_prediction():
    forecast, readings = _day_inputs()
    old = [p for p in readings if p[0] <= 11.0]
    assert rd.features_at(old, forecast, 12.6, _dt.date(2026, 7, 15), 1.0) is None, "1.6 h old"
    assert rd.features_at(old, forecast, 12.4, _dt.date(2026, 7, 15), 1.0) is not None, "1.4 h old"


def test_the_forecast_is_read_between_hours_and_the_rest_after_the_decision():
    forecast, readings = _day_inputs()
    assert abs(rd._fc_at(forecast, 13.5) - (forecast[13][0] + forecast[14][0]) / 2) < 1e-9
    a = rd.features_at(rd.available(readings, 13.6, 0), forecast, 13.6, _dt.date(2026, 7, 15), 1.0)
    rest = max(forecast[h][0] for h in range(14, 24))
    assert abs(a["x"][0] - (rest - a["now"])) < 1e-9
    assert abs(a["x"][-1] - 0.6) < 1e-9, "min_frac"


# ---------------------------------------------------------------------------
# Challenger C: the models' bias-corrected day-before maxima (rd3, 30 Sep).
# ---------------------------------------------------------------------------
def test_rd3_is_a_separate_contract_with_three_more_inputs():
    assert rd.VERSION_PREFIX_C == "rd3"
    assert rd.FEATURES_C == rd.FEATURES + ["models_rise_c", "models_frac_up", "models_n"]


def test_the_model_bias_shrinks_to_the_pool_and_the_pool_to_zero():
    # a model 2 C warm everywhere over 60 days; one city seen 40 days, 5 C warm
    pairs = [("gfs", "a", 2.0)] * 60 + [("gfs", "b", 5.0)] * 40
    bias, pooled = rd.fit_model_bias(pairs)
    n = 100
    assert abs(pooled["gfs"] - (sum(e for *_, e in pairs) / n) * n / (n + 30)) < 1e-9
    wb = 40 / 70
    assert abs(bias[("gfs", "b")] - (wb * 5.0 + (1 - wb) * pooled["gfs"])) < 1e-9
    assert bias[("gfs", "b")] < 5.0, "forty days do not move it all the way"
    big, _ = rd.fit_model_bias([("x", "c", 50.0)] * 1000)
    assert big[("x", "c")] == rd.MODEL_BIAS_BOUND_C, "bounded"


def test_under_the_minimum_sample_the_prior_stands():
    """Rule 11: a city seen fewer than MIN_BIAS_DAYS days takes its model's
    pooled bias; a model seen fewer takes 0."""
    few = rd.MIN_BIAS_DAYS - 1
    bias, pooled = rd.fit_model_bias([("gfs", "a", 2.0)] * 60 + [("gfs", "b", 5.0)] * few)
    assert bias[("gfs", "b")] == pooled["gfs"]
    bias, pooled = rd.fit_model_bias([("jma", "a", 3.0)] * few)
    assert pooled["jma"] == 0.0 and bias[("jma", "a")] == 0.0


def test_the_bias_table_round_trips_and_is_in_the_version():
    bias, pooled = rd.fit_model_bias([("gfs", "a", 2.0)] * 60)
    bj, pj = rd.bias_to_json(bias, pooled)
    assert rd.bias_from_json(bj, pj) == ({k: round(v, 6) for k, v in bias.items()},
                                         {k: round(v, 6) for k, v in pooled.items()})
    params = {11: {"last_date": "2026-09-25", "mu": [0.0]}}
    v1 = rd.version_of_c(params, bj, pj)
    v2 = rd.version_of_c(params, {"gfs|a": 0.0}, pj)
    assert v1.startswith("rd3:2026-09-25:") and v1 != v2, "a different bias table is a different version"


def test_models_summaries_and_the_missing_provider_fallback():
    models = {"a": 25.0, "b": 24.0, "c": 23.0, "d": 20.0}
    f = rd.models_features(models, 22.0, "x", {}, {})
    assert f == [(3.0 + 2.0 + 1.0 + 0.0) / 4, 3 / 4, 4.0]
    f2 = rd.models_features(models, 22.0, "x", {("a", "x"): 3.0}, {})
    assert f2[0] == (0.0 + 2.0 + 1.0 + 0.0) / 4, "a 3 C warm model's rise is taken off"
    assert rd.models_features({"a": 25.0, "b": 24.0, "c": 23.0}, 22.0, "x", {}, {}) is None
