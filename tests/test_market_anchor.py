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
