"""The hit tournament (plan v2.1 P3.8), on synthetic cities where the answer
is known: which recipe puts the most probability on the bucket that settles,
chosen and scored without ever seeing the day it is graded on."""
import datetime as dt
import math
import random
import time

import pytest

import hit_tournament as ht


def _ladder_c(lo=10, hi=35):
    bands = [{"band_id": "low", "band_lo": None, "band_hi": float(lo), "open_low": True, "open_high": False}]
    bands += [{"band_id": str(t), "band_lo": float(t), "band_hi": float(t + 1),
               "open_low": False, "open_high": False} for t in range(lo, hi)]
    bands += [{"band_id": "high", "band_lo": float(hi), "band_hi": None, "open_low": False, "open_high": True}]
    return bands


def _winner(bands, observed_c):
    from probability_engine import venue_round
    r = venue_round(observed_c, "C")
    for i, b in enumerate(bands):
        if b["open_low"] and r < b["band_hi"]:
            return i
        if b["open_high"] and r >= b["band_lo"]:
            return i
        if not b["open_low"] and not b["open_high"] and b["band_lo"] <= r < b["band_hi"]:
            return i


def _city(name, n, rng, models, live_offset=2.0, lane="asof", start=dt.date(2026, 8, 1)):
    """models: {name: (bias, noise sd)}. The live price is centred live_offset off."""
    bands = _ladder_c()
    days = []
    for i in range(n):
        obs = 22.0 + 4 * math.sin(i / 5.0) + rng.gauss(0, 0.3)
        fc = {m: obs + b + rng.gauss(0, sd) for m, (b, sd) in models.items()}
        from probability_engine import compute_band_probabilities
        live = [p for _b, p in compute_band_probabilities(obs + live_offset, 1.5, "C", bands)]
        days.append(ht.Day(city=name, date=(start + dt.timedelta(days=i)).isoformat(), unit="C",
                           bands=bands, winner=_winner(bands, obs), observed=obs,
                           live=live, market=None, forecasts={lane: fc}))
    return days


def test_no_day_is_priced_before_it_has_min_train_days_behind_it():
    rng = random.Random(1)
    days = _city("a", 12, rng, {"good": (0.0, 0.3)})
    scored = ht.walk_city(days, "asof")
    first_dates = sorted({d for s in scored.values() for d in s})
    assert first_dates[0] == days[ht.MIN_TRAIN].date


def test_the_fast_walk_is_the_same_arithmetic_as_fit_and_predict():
    rng = random.Random(2)
    days = _city("a", 15, rng, {"good": (0.0, 0.4), "warm": (1.5, 0.4)})
    scored = ht.walk_city(days, "asof")
    i = 10
    models = {"good", "warm"}
    for key in ("centre=invmse|bias=recent|width=1.25", "centre=model:warm|bias=long|width=0.8"):
        params = ht.fit(key, days[:i], "asof", models)
        probs = ht.predict(key, params, days[i], "asof")
        assert ht.score(probs, days[i].winner) == pytest.approx(scored[key][days[i].date])


def test_a_biased_model_loses_to_its_bias_correction_and_the_good_model_wins():
    rng = random.Random(3)
    days = _city("a", 40, rng, {"good": (0.0, 0.3), "warm": (2.5, 0.3)})
    scored = ht.walk_city(days, "asof")
    best = min(scored, key=lambda k: ht._mean_ll(scored[k])[0])
    assert "model:warm|bias=none" not in best
    assert ht._mean_ll(scored["centre=model:warm|bias=long|width=1.0"])[0] < \
        ht._mean_ll(scored["centre=model:warm|bias=none|width=1.0"])[0]


def test_the_forward_champion_beats_a_live_price_two_degrees_off():
    rng = random.Random(4)
    days = []
    for c in ("a", "b", "c"):
        days += _city(c, 40, rng, {"good": (0.0, 0.3), "warm": (1.0, 0.8)}, live_offset=2.0)
    t_rows, r_rows, summaries = ht.run_lane(days, "asof")
    s = summaries[0]
    assert s["n_live_days"] > 0 and s["champion_log_loss"] < s["live_log_loss"]
    assert s["gain_vs_live_lo"] > 0
    assert all(r["state"] == "shadow" for r in r_rows), "nothing goes live until the engine reads it"
    assert all(r["recipe_version"] and len(r["recipe_version"]) == 12 for r in r_rows)


def test_the_choice_for_a_day_never_uses_that_day():
    """Change only the last day's scores: the forward champion for that day
    must not move, because it was chosen before the day was seen."""
    rng = random.Random(5)
    days = _city("a", 30, rng, {"good": (0.0, 0.3), "warm": (1.0, 0.5)})
    by_city = {"a": ht.walk_city(days, "asof")}
    dates = sorted({d for s in by_city["a"].values() for d in s})
    before = ht.forward_champion(by_city, "a", dates)[dates[-1]][0]
    for key in by_city["a"]:
        if dates[-1] in by_city["a"][key]:
            by_city["a"][key][dates[-1]] = (99.0, 0.0, 2.0, 50.0)
    after = ht.forward_champion(by_city, "a", dates)[dates[-1]][0]
    assert before == after


def test_a_city_leaves_the_pooled_recipe_only_with_twenty_days_and_a_gate():
    per = {"x": {f"2026-09-{d:02d}": (1.0, 0, 0, 0) for d in range(1, 11)},
           "p": {f"2026-09-{d:02d}": (2.0, 0, 0, 0) for d in range(1, 11)}}
    assert ht.city_choice(per, "p") == ("p", "pooled"), "ten days are not enough"
    per = {"x": {f"2026-09-{d:02d}": (1.0 + 0.01 * (d % 3), 0, 0, 0) for d in range(1, 29)},
           "p": {f"2026-09-{d:02d}": (2.0, 0, 0, 0) for d in range(1, 29)}}
    assert ht.city_choice(per, "p") == ("x", "city")


def test_a_night_moves_the_parameters_at_most_one_step():
    prev = {"bias_c": 0.5, "sigma_c": 1.0, "weights": {"a": 0.5, "b": 0.5}}
    new = {"bias_c": 2.0, "sigma_c": 2.0, "weights": {"a": 0.9, "b": 0.1}}
    out = ht.bound_step(new, prev)
    assert out["bias_c"] == pytest.approx(0.8)
    assert out["sigma_c"] == pytest.approx(1.1)
    assert abs(out["weights"]["a"] - 0.5) <= 0.1 + 1e-9
    assert ht.bound_step(new, None) == new


def test_the_version_names_the_recipe_and_its_parameters():
    p = {"bias_c": 0.1, "sigma_c": 1.2, "weights": {}}
    v1 = ht.recipe_version("centre=mean|bias=long|width=1.0", p)
    assert v1 == ht.recipe_version("centre=mean|bias=long|width=1.0", dict(p))
    assert v1 != ht.recipe_version("centre=mean|bias=long|width=1.0", {**p, "bias_c": 0.2})


def test_the_research_lane_never_promotes():
    rng = random.Random(6)
    days = _city("a", 30, rng, {"best_match": (0.0, 0.3)}, lane="research")
    _t, r_rows, _s = ht.run_lane(days, "research")
    assert r_rows and all(r["state"] == "shadow" and "never promotes" in r["reason"] for r in r_rows)


def test_evidence_rows_become_days_with_one_winner():
    ladder = [{"city_key": "a", "for_date": "2026-09-01", "band_id": str(t), "band_lo": t,
               "band_hi": t + 1, "open_low": False, "open_high": False, "unit": "C",
               "settled_yes": t == 22, "observed_max_c": 22.3, "live_prob": 0.1, "market_price": None}
              for t in range(18, 26)]
    fc = [{"city_key": "a", "for_date": "2026-09-01", "lane": "asof", "model": "m", "forecast_max_c": 21.9}]
    days = ht.build_days(ladder, fc)
    assert len(days) == 1 and days[0].bands[days[0].winner]["band_lo"] == 22.0
    assert days[0].market is None and days[0].live is not None


def test_a_full_night_fits_in_the_pipeline():
    """16 cities x 60 days x 3 models. Measured 23 Sep: 48 cities took 16.7 s,
    so a third of that must stay well under a minute."""
    rng = random.Random(7)
    days = []
    for c in range(16):
        days += _city(f"c{c}", 60, rng, {"a": (0.0, 0.5), "b": (0.8, 0.7), "c": (-0.5, 0.9)})
    t0 = time.time()
    ht.run_lane(days, "asof")
    assert time.time() - t0 < 60, "the tournament must not eat the daily job's minutes"


# ---------------------------------------------------------------------------
# WIDTH THAT GROWS WITH DISAGREEMENT (24 Sep): San Francisco's centre at
# 81.7 F with sigma ~1 C while NWS said 73.4 F.

def test_a_spread_width_adds_todays_disagreement_in_quadrature():
    import math
    import hit_tournament as ht
    resid = [1.0, -1.0, 1.0, -1.0, 1.0]            # rmse 1.0
    base = ht.sigma_of(resid, 0.0, 1.0)
    assert base == 1.0
    assert ht.sigma_of(resid, 0.0, "1.0+spread", spread=0.0) == 1.0, "no disagreement, no change"
    assert math.isclose(ht.sigma_of(resid, 0.0, "1.0+spread", spread=2.0), math.sqrt(5.0))
    # bounded: the spread term never exceeds the cap
    assert math.isclose(ht.sigma_of(resid, 0.0, "1.0+spread", spread=99.0),
                        math.sqrt(1.0 + ht.SPREAD_CAP_C ** 2))


def test_the_spread_is_the_disagreement_between_forecasts_and_needs_two():
    import hit_tournament as ht
    assert ht.spread_of({"nws": 23.0}) == 0.0
    assert ht.spread_of({"nws": 23.0, "open_meteo_forecast": 27.0}) == 2.0
    assert ht.spread_of({}) == 0.0


def test_spread_widths_are_offered_only_where_there_is_something_to_disagree():
    import hit_tournament as ht
    one = ht.recipes_for({"open_meteo_forecast"})
    two = ht.recipes_for({"open_meteo_forecast", "nws"})
    assert not any("+spread" in k for k in one)
    assert any("width=1.0+spread" in k for k in two) and any("width=1.25+spread" in k for k in two)
