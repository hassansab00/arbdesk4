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
