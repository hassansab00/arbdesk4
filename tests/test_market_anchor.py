"""The market-anchored belief (plan v2 P5.3 as amended by Hassan on 27 Sep):
p = p_market + w (p_model - p_market), w learned under Rule 11."""
import random

import market_anchor as ma


def test_the_weight_moves_the_ladder_from_the_market_to_the_model():
    model = {"a": 0.6, "b": 0.3, "c": 0.1}
    market = {"a": 0.2, "b": 0.5, "c": 0.3}
    assert ma.anchor(model, market, 0) == market
    assert all(abs(ma.anchor(model, market, 1)[k] - model[k]) < 1e-12 for k in model)
    half = ma.anchor(model, market, 0.5)
    assert abs(sum(half.values()) - 1) < 1e-12 and abs(half["a"] - 0.4) < 1e-12
    assert ma.anchor(model, market, 7) == ma.anchor(model, market, 1)          # bounded


def test_the_market_is_the_books_mids_normalised():
    book = {"a": {"ask": 0.30, "bid": 0.20}, "b": {"ask": 0.60, "bid": 0.50}, "c": {"ask": 0.02}}
    p = ma.market_probs(book, ["a", "b", "c"])
    assert abs(sum(p.values()) - 1) < 1e-12 and p["b"] > p["a"] > p["c"]
    assert ma.market_probs(book, ["a", "b", "c", "d"]) is None


def test_the_scope_is_the_view_source_and_the_checkpoint_class():
    assert ma.scope("engine", "d1_eve") == "engine:pre_day"
    assert ma.scope("s10", "postpeak_1h") == "s10:post_peak"
    assert ma.weight(None, "engine:pre_day") == (0.0, "prior")


def _rows(days, model_better, scope="engine:midday", seed=1):
    rng = random.Random(seed)
    rows = []
    for d in range(days):
        date = f"2026-09-{d + 1:02d}"
        for _ in range(8):
            truth = {"a": 0.6, "b": 0.3, "c": 0.1}
            winner = rng.choices(list(truth), weights=list(truth.values()))[0]
            good, bad = truth, {"a": 0.2, "b": 0.3, "c": 0.5}
            model, market = (good, bad) if model_better else (bad, good)
            rows.append((date, scope, model, market, winner))
    return rows


def test_the_model_earns_weight_only_on_evidence_and_one_step_a_night():
    t = ma.fit(_rows(25, model_better=True), as_of="2026-09-26")
    assert t["weights"]["engine:midday"] == ma.MAX_STEP                   # the target is higher; one step
    t2 = ma.fit(_rows(25, model_better=True), as_of="2026-09-26", previous=t)
    assert t2["weights"]["engine:midday"] == 2 * ma.MAX_STEP
    assert t["version"].startswith("market-anchor:2026-09-26:") and t2["previous"] == t["version"]


def test_a_model_the_market_beats_stays_at_zero():
    t = ma.fit(_rows(25, model_better=False), as_of="2026-09-26")
    assert t["weights"]["engine:midday"] == 0.0


def test_too_few_days_keep_the_prior_and_the_future_is_never_read():
    t = ma.fit(_rows(10, model_better=True), as_of="2026-09-26")
    assert t["weights"]["engine:midday"] == 0.0 and "fewer than" in t["evidence"]["engine:midday"]["held"]
    t = ma.fit(_rows(25, model_better=True), as_of="2026-09-10")            # only 1-9 Sep count
    assert t["evidence"]["engine:midday"]["days"] == 9 and t["weights"]["engine:midday"] == 0.0


def test_a_weight_that_loses_its_evidence_steps_back_toward_the_market():
    prev = {"version": "market-anchor:x", "weights": {"engine:midday": 0.3}}
    t = ma.fit(_rows(25, model_better=False), as_of="2026-09-26", previous=prev)
    assert t["weights"]["engine:midday"] == 0.3 - ma.MAX_STEP


# --------------------------------------------------------------------------
# Cities (28 Sep, after P3.10 Q8): a city may earn its own weight, guarded by
# a family test and its own, both walking forward.
# --------------------------------------------------------------------------

def _ladder(rng, k=7):
    v = [rng.random() ** 2 + 0.02 for _ in range(k)]
    s = sum(v)
    return [x / s for x in v]


def _city_rows(days=45, cities=12, informed=(), per_day=2, seed=7, scope="engine:morning"):
    """The market is the truth blurred. In an informed city the model is the
    truth, lightly blurred: it knows what the price does not. Elsewhere the
    model is the price plus noise, so its best weight is 0."""
    rng = random.Random(seed)
    rows = []
    for d in range(days):
        date = f"2026-{10 + d // 28:02d}-{d % 28 + 1:02d}"
        for c in range(cities):
            for _ in range(per_day):
                bands = [f"b{i}" for i in range(7)]
                truth = _ladder(rng)
                winner = rng.choices(bands, weights=truth)[0]
                market = [0.6 * t + 0.4 * b for t, b in zip(truth, _ladder(rng))]
                blur = _ladder(rng)
                model = ([0.9 * t + 0.1 * b for t, b in zip(truth, blur)] if c in informed
                         else [0.5 * m + 0.5 * b for m, b in zip(market, blur)])
                rows.append((date, scope, dict(zip(bands, model)), dict(zip(bands, market)), winner, f"c{c:02d}"))
    return rows


def test_a_city_where_the_model_knows_more_earns_its_own_weight_and_no_other_does():
    t = ma.fit(_city_rows(informed={2, 7}), as_of="2026-12-31")
    ev = t["city_evidence"]["engine:morning"]
    assert ev["family"]["passed"] and ev["family"]["gain_lower90"] > 0
    assert t["weights"]["engine:morning"] == 0.0            # pooled over all cities, the model adds nothing
    assert t["city_weights"] == {"engine:morning": {"c02": ma.MAX_STEP, "c07": ma.MAX_STEP}}
    for c in ("c02", "c07"):
        assert ev["by_city"][c]["forward"]["passed"] and ev["by_city"][c]["target"] > ma.MAX_STEP
    assert not any(v["forward"]["passed"] for c, v in ev["by_city"].items() if c not in ("c02", "c07"))
    assert t["city_min_days"] == ma.CITY_MIN_DAYS and t["city_k"] == ma.CITY_K


def test_when_no_city_differs_no_city_moves():
    t = ma.fit(_city_rows(informed=()), as_of="2026-12-31")
    ev = t["city_evidence"]["engine:morning"]
    assert not ev["family"]["passed"] and t["city_weights"] == {}
    assert ev["eligible"] == 12 and all(v["target"] == 0.0 for v in ev["by_city"].values())


def test_a_city_climbs_one_step_a_night_and_walks_back_when_its_evidence_goes():
    rows = _city_rows(informed={2, 7})
    prev = None
    for _ in range(3):
        prev = ma.fit(rows, as_of="2026-12-31", previous=prev)
    assert prev["city_weights"]["engine:morning"]["c02"] == round(3 * ma.MAX_STEP, 4)
    # the same cities with the model's knowledge gone: back one step a night
    t = ma.fit(_city_rows(informed=()), as_of="2026-12-31", previous=prev)
    assert t["city_weights"]["engine:morning"]["c02"] == round(2 * ma.MAX_STEP, 4)
    assert t["previous"] == prev["version"]


def test_a_city_needs_its_own_days_and_twice_that_to_be_tested_forward():
    # 30 days: every city has an estimate, but only 10 days on which it could be scored forward
    t = ma.fit(_city_rows(days=30, informed={2, 7}), as_of="2026-12-31")
    ev = t["city_evidence"]["engine:morning"]
    assert ev["eligible"] == 12 and not ev["family"]["passed"] and "fewer than" in ev["family"]["held"]
    assert t["city_weights"] == {}
    t = ma.fit(_city_rows(days=15, informed={2, 7}), as_of="2026-12-31")
    assert t["city_evidence"]["engine:morning"]["eligible"] == 0


def test_the_cities_never_read_the_night_they_are_fitted_for():
    rows = _city_rows(informed={2, 7})
    cut = "2026-11-10"                                      # 38 days before it: too few to test forward
    t = ma.fit(rows, as_of=cut)
    assert t == ma.fit([r for r in rows if r[0] < cut], as_of=cut)
    assert t["city_weights"] == {}


def test_rows_without_a_city_are_the_scopes_alone():
    t = ma.fit(_rows(25, model_better=True), as_of="2026-09-26")
    assert t["city_weights"] == {} and t["city_evidence"]["engine:midday"]["cities"] == 0
    assert t["weights"]["engine:midday"] == ma.MAX_STEP


def test_a_decision_reads_its_citys_weight_where_it_has_one():
    table = {"version": "v1", "weights": {"engine:morning": 0.05},
             "city_weights": {"engine:morning": {"tel-aviv": 0.15}}}
    assert ma.weight_for(table, "engine:morning", "tel-aviv") == (0.15, "v1", "engine:morning@tel-aviv")
    assert ma.weight_for(table, "engine:morning", "london") == (0.05, "v1", "engine:morning")
    assert ma.weight_for(table, "engine:midday", "tel-aviv") == (0.0, "v1", "engine:midday")
    assert ma.weight_for(None, "engine:morning", "tel-aviv") == (0.0, "prior", "engine:morning")
