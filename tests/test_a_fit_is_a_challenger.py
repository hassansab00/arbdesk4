"""A fitted model has to earn the right to move a price, per city and per lead.

derived_weather_model.beats_persistence was the only thing between a fit and a
traded number. It asks whether the model beats yesterday-equals-today on
held-out days of its OWN training window. Three things are wrong with that as
a gate:

  THE BENCHMARK. Nothing here prices off persistence. It prices off the public
  forecast, which is good - measured on this repo's cities, persistence MAE
  runs 1.2 to 2.5 C while the public forecast runs well under that. A model can
  beat persistence comfortably and be worse than the number it would replace.

  THE DAYS. Held-out days sit inside the training window, and weather is
  autocorrelated enough that the days on either side carry most of a held-out
  day's information. A forward day is one nobody had seen when the prediction
  was made.

  THE LEADS. A model sharp at lead 0 and useless on Friday averages to "fine".

scripts/model_promotion.py runs the right test and writes shadow | promoted |
rejected | stale. Only 'promoted' reaches the pricing engine, and it reaches it
through v_model_promoted - a view that shows nothing else - so no caller can
read a shadow row by accident.
"""

import datetime as dt
import random
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import model_promotion as mp
import probability_engine as pe

ROOT = Path(__file__).resolve().parents[1]


def series(n, *, model_err, public_err, pers_err, seed=3, start="2026-06-01"):
    """n consecutive days with a chosen error size for each of the three."""
    rng = random.Random(seed)
    d0 = dt.date.fromisoformat(start)
    out = []
    for i in range(n):
        observed = 20.0 + 5.0 * rng.random()
        out.append({
            "day": (d0 + dt.timedelta(days=i)).isoformat(),
            "observed": observed,
            "predicted": observed + rng.choice([-1, 1]) * model_err,
            "public": observed + rng.choice([-1, 1]) * public_err,
            "persistence": observed + rng.choice([-1, 1]) * pers_err,
        })
    return out


# ---------------------------------------------------------------------------
# the bootstrap
# ---------------------------------------------------------------------------
def test_the_interval_is_reproducible():
    """A promotion that flips between runs because the draws differed is not
    evidence, and an operator re-running this to check a surprising verdict has
    to get the same answer."""
    d = [0.1, -0.2, 0.4, 0.3, -0.1, 0.2, 0.5, 0.0, 0.3, 0.1] * 5
    assert mp.bootstrap_interval(d, draws=300) == mp.bootstrap_interval(d, draws=300)


def test_blocks_keep_a_warm_spell_from_counting_as_many_wins():
    """THE REASON THE BOOTSTRAP IS BLOCKED.

    A run of consecutive days the model called well is ONE piece of evidence
    about one weather pattern, not thirty. Resampling single days breaks the
    run apart and hands back an interval far too narrow - which is how a
    marginal model gets promoted. Blocks of consecutive dates keep the runs
    intact, so the interval is wider and the bar is honestly harder.
    """
    # strongly autocorrelated: ten days good, ten days bad, repeated
    d = ([0.6] * 10 + [-0.4] * 10) * 6
    lo_block, hi_block = mp.bootstrap_interval(d, draws=800)
    single = mp.bootstrap_interval(d, draws=800)   # same call...
    # ...and the same data resampled one day at a time, which is what a block
    # length of 1 is.
    saved = mp.block_len
    try:
        mp.block_len = lambda n: 1
        lo_one, hi_one = mp.bootstrap_interval(d, draws=800)
    finally:
        mp.block_len = saved
    assert (hi_block - lo_block) > (hi_one - lo_one), (
        "blocking made the interval NARROWER, which is backwards - the whole "
        "point is that autocorrelated days carry less information than their "
        "count suggests")
    assert single == (lo_block, hi_block)


def test_the_block_length_grows_with_the_sample():
    assert mp.block_len(27) == 3
    assert mp.block_len(1000) == 10
    assert mp.block_len(1) == 1


# ---------------------------------------------------------------------------
# the rule
# ---------------------------------------------------------------------------
def _verdict(rows, stale=(), dropped=()):
    scored = mp.score(rows, draws=400) if rows else None
    return mp.decide(scored, list(stale), list(dropped)), scored


def test_a_model_that_beats_persistence_and_loses_to_the_forecast_is_rejected():
    """The exact case the old gate let through."""
    rows = series(120, model_err=1.0, public_err=0.5, pers_err=2.0)
    (state, reasons), scored = _verdict(rows)
    assert scored["gain_vs_persistence_c"] > mp.MIN_GAIN_C
    assert scored["gain_vs_public_c"] < 0
    assert state == "rejected"
    held = {r["rule"]: r["held"] for r in reasons}
    assert held["beats_persistence_by_margin"] is True
    assert held["beats_public_forecast_by_margin"] is False


def test_a_model_that_beats_both_by_a_clear_margin_is_promoted():
    rows = series(120, model_err=0.4, public_err=1.2, pers_err=2.0)
    (state, reasons), scored = _verdict(rows)
    assert state == "promoted", [r for r in reasons if not r["held"]]
    assert scored["boot_lo_c"] > 0


def test_thirty_days_is_a_floor_no_margin_can_buy_past():
    """Twenty-nine days of a spectacular model is still twenty-nine days."""
    rows = series(29, model_err=0.1, public_err=3.0, pers_err=4.0)
    (state, reasons), scored = _verdict(rows)
    assert state == "shadow"
    assert scored["gain_vs_public_c"] > 2.0
    assert {r["rule"]: r["held"] for r in reasons}["enough_forward_days"] is False


def test_an_improvement_too_small_to_change_a_bucket_is_not_an_improvement():
    """Polymarket's buckets are 1 C wide. A centre that moves by less than a
    tenth of one cannot change which bucket a day lands in, so the gain is
    real and useless."""
    rows = series(120, model_err=1.00, public_err=1.05, pers_err=2.0)
    (state, _), scored = _verdict(rows)
    assert 0 < scored["gain_vs_public_c"] < mp.MIN_GAIN_C
    assert state == "rejected"


def test_the_reported_interval_is_the_one_that_nearly_failed():
    """Reporting the comfortable comparison would make a marginal promotion
    look safe."""
    rows = series(120, model_err=0.4, public_err=1.2, pers_err=6.0)
    _, scored = _verdict(rows)
    assert scored["gain_vs_persistence_c"] > scored["gain_vs_public_c"]
    # the binding one is the public comparison, so the interval must be its own
    assert scored["boot_hi_c"] < scored["gain_vs_persistence_c"]


# ---------------------------------------------------------------------------
# stale is not rejected
# ---------------------------------------------------------------------------
def test_a_stale_model_is_stale_and_never_rejected():
    """Rejected is a claim about evidence. A model nobody has measured
    recently has not been beaten - it has not been judged."""
    rows = series(120, model_err=0.4, public_err=1.2, pers_err=2.0)
    (state, reasons), _ = _verdict(rows, stale=[("fit_is_current", "the fit is 40 days old")])
    assert state == "stale"
    assert any(r["rule"] == "fit_is_current" and not r["held"] for r in reasons)


def test_a_missing_fit_is_stale():
    assert mp.stale_checks("london", [], None)[0][0] == "fit_exists"


def test_an_old_fit_is_stale():
    now = dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc)
    old = {"fitted_at": (now - dt.timedelta(days=mp.FIT_MAX_AGE_DAYS + 1)).isoformat()}
    rules = [r for r, _ in mp.stale_checks("london", [], old, now)]
    assert "fit_is_current" in rules


def test_predictions_that_stopped_arriving_are_stale():
    now = dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc)
    fit = {"fitted_at": now.isoformat(), "model_version": "v1"}
    rows = [{"model_version": "v1",
             "predicted_at": (now - dt.timedelta(days=mp.PREDICTION_MAX_AGE_DAYS + 1)).isoformat()}]
    rules = [r for r, _ in mp.stale_checks("london", rows, fit, now)]
    assert "predictions_are_current" in rules


def test_promoting_a_fit_that_is_not_the_one_running_is_refused():
    """Coefficients are refitted weekly. Forward evidence gathered against one
    set of them says nothing about another."""
    now = dt.datetime(2026, 9, 19, tzinfo=dt.timezone.utc)
    fit = {"fitted_at": now.isoformat(), "model_version": "london:max_c:bbbbbbbbbbbb"}
    rows = [{"model_version": "london:max_c:aaaaaaaaaaaa", "predicted_at": now.isoformat()}]
    rules = [r for r, _ in mp.stale_checks("london", rows, fit, now)]
    assert "scored_what_is_running" in rules


# ---------------------------------------------------------------------------
# what gets dropped, and said
# ---------------------------------------------------------------------------
def test_a_day_that_cannot_name_its_model_is_dropped_and_counted():
    preds = [{"city_key": "t", "for_date": "2026-09-01", "run_at": "2026-08-31T00:00:00+00:00",
              "lead_days": 1, "predicted_max_c": 20.0, "nws_max_c": 20.5,
              "prev_source": "observed", "model_version": None,
              "predicted_at": "2026-08-31T00:00:00+00:00"}]
    obs = [{"city_key": "t", "obs_date": "2026-08-31", "max_c": 19.0, "n_obs": 24},
           {"city_key": "t", "obs_date": "2026-09-01", "max_c": 20.2, "n_obs": 24}]
    groups, drops, _ = mp.assemble(preds, obs, [], today=dt.date(2026, 9, 10))
    assert groups[("t", 1)] == []
    assert drops[("t", 1)]["no_version"] == 1


def test_a_day_with_no_anchor_is_dropped_and_counted():
    preds = [{"city_key": "t", "for_date": "2026-09-01", "run_at": "2026-08-31T00:00:00+00:00",
              "lead_days": 1, "predicted_max_c": 20.0, "nws_max_c": 20.5,
              "prev_source": None, "model_version": "v1",
              "predicted_at": "2026-08-31T00:00:00+00:00"}]
    obs = [{"city_key": "t", "obs_date": "2026-08-31", "max_c": 19.0, "n_obs": 24},
           {"city_key": "t", "obs_date": "2026-09-01", "max_c": 20.2, "n_obs": 24}]
    _, drops, _ = mp.assemble(preds, obs, [], today=dt.date(2026, 9, 10))
    assert drops[("t", 1)]["no_anchor"] == 1


def test_the_newest_run_wins_for_a_given_city_day_and_lead():
    """The forward job writes a row per NWS run, so several share a lead.
    Pricing against whichever PostgREST returned first is not a choice."""
    base = {"city_key": "t", "for_date": "2026-09-01", "lead_days": 1, "nws_max_c": 20.5,
            "prev_source": "observed", "model_version": "v1",
            "predicted_at": "2026-08-31T00:00:00+00:00"}
    preds = [dict(base, run_at="2026-08-31T03:00:00+00:00", predicted_max_c=11.1),
             dict(base, run_at="2026-08-31T21:00:00+00:00", predicted_max_c=20.0)]
    obs = [{"city_key": "t", "obs_date": "2026-08-31", "max_c": 19.0, "n_obs": 24},
           {"city_key": "t", "obs_date": "2026-09-01", "max_c": 20.2, "n_obs": 24}]
    groups, _, _ = mp.assemble(preds, obs, [], today=dt.date(2026, 9, 10))
    assert [r["predicted"] for r in groups[("t", 1)]] == [20.0]


def test_leads_are_never_pooled():
    """A model sharp at lead 0 and useless on Friday averages to fine."""
    base = {"city_key": "t", "nws_max_c": 20.5, "prev_source": "observed",
            "model_version": "v1", "predicted_at": "2026-08-31T00:00:00+00:00"}
    preds = [dict(base, for_date="2026-09-01", run_at="2026-08-31T00:00:00+00:00",
                  lead_days=1, predicted_max_c=20.0),
             dict(base, for_date="2026-09-01", run_at="2026-08-26T00:00:00+00:00",
                  lead_days=6, predicted_max_c=30.0)]
    obs = [{"city_key": "t", "obs_date": "2026-08-31", "max_c": 19.0, "n_obs": 24},
           {"city_key": "t", "obs_date": "2026-09-01", "max_c": 20.2, "n_obs": 24}]
    groups, _, _ = mp.assemble(preds, obs, [], today=dt.date(2026, 9, 10))
    assert set(groups) == {("t", 1), ("t", 6)}


# ---------------------------------------------------------------------------
# and the gate the engine actually reads
# ---------------------------------------------------------------------------
def _price(monkeypatch, promoted=None, model_forecasts=None, lead=1):
    monkeypatch.setattr(pe, "_forecast_for", lambda *_: {
        "lead_days": lead, "forecast_max_c": 20.0, "model": "nws",
        "run_at": "2026-09-18T12:00:00+00:00"})
    monkeypatch.setattr(pe, "_skill_for", lambda *_a, **_k: {
        "lead_days": lead, "mae_c": 2.0, "bias_c": 0.5, "n_days": 300})
    monkeypatch.setattr(pe.regime, "classify", lambda *_a, **_k: SimpleNamespace(
        confidence=1.0, reasons=[], sigma_multiplier=1.0, label="NORMAL"))
    monkeypatch.setattr(pe, "_divergence_for", lambda *_: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_for", lambda *_: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_map", lambda: None)
    monkeypatch.setattr(pe, "model_version_id", lambda *_a, **_k: None)
    bands = [
        {"band_id": "low", "band_lo": None, "band_hi": 20, "open_low": True, "open_high": False},
        {"band_id": "mid", "band_lo": 20, "band_hi": 21, "open_low": False, "open_high": False},
        {"band_id": "high", "band_lo": 21, "band_hi": None, "open_low": False, "open_high": True},
    ]
    return pe.process_city_day("london", "2026-09-19", "C", bands, {}, None,
                               promoted, model_forecasts)


def _promo(lead=1):
    return {("london", lead): {"city_key": "london", "lead_days": lead,
                               "model_version": "london:max_c:abc123",
                               "model_mae_c": 0.8, "gain_vs_public_c": 0.42,
                               "n_days": 64}}


def _mfc(lead=1, predicted=26.0):
    return {("london", "2026-09-19"): {
        "city_key": "london", "for_date": "2026-09-19", "run_at": "2026-09-19T06:00:00+00:00",
        "lead_days": lead, "predicted_max_c": predicted,
        "model_version": "london:max_c:abc123"}}


def test_nothing_promoted_prices_exactly_as_before(monkeypatch):
    before = _price(monkeypatch)
    after = _price(monkeypatch, promoted={}, model_forecasts={})
    assert ([r["calibrated_prob"] for r in before[0]]
            == [r["calibrated_prob"] for r in after[0]])
    assert before[0][0]["forecast_max_c"] == 20.0


def test_a_promoted_model_supplies_the_centre_the_bias_and_the_width(monkeypatch):
    rows, _reg, reasons = _price(monkeypatch, _promo(), _mfc())
    assert rows[0]["forecast_max_c"] == 26.0, (
        "the price is still centred on the public forecast's 20.0 rather than "
        "the promoted model's own 26.0")
    assert rows[0]["bias_applied_c"] == 0.0, (
        "bias_c is measured on the PUBLIC forecast's errors; the model was fitted "
        "against observed maxima and is already de-biased")
    # sigma = mae * 1.2533, and mae is now the model's own forward error
    assert rows[0]["sigma_c"] == pytest.approx(0.8 * pe.MAE_TO_SIGMA, abs=1e-3)
    assert any("promoted_model:london:max_c:abc123:lead1" in r for r in reasons), reasons


def test_a_promotion_at_another_lead_does_not_price_this_one(monkeypatch):
    """The whole reason promotion is per lead."""
    rows, _, reasons = _price(monkeypatch, _promo(lead=1), _mfc(lead=4), lead=1)
    assert rows[0]["forecast_max_c"] == 20.0
    assert not any("promoted_model" in r for r in reasons)


def test_a_city_with_no_model_prediction_for_the_day_prices_from_the_forecast(monkeypatch):
    rows, _, reasons = _price(monkeypatch, _promo(), {})
    assert rows[0]["forecast_max_c"] == 20.0
    assert not any("promoted_model" in r for r in reasons)


def test_the_engine_reads_the_narrow_view_not_the_table():
    """derived_model_promotion holds shadow and rejected rows too. Reading it
    directly is one filter away from pricing on something unproven."""
    src = Path(pe.__file__).read_text()
    body = src[src.index("def _promoted_models"): src.index("def _model_forecasts")]
    # The docstring explains WHY it reads the view rather than the table, and
    # names the table to do so. Strip the prose or the explanation fails the
    # rule it is explaining - which has happened three times in this repo.
    code = "\n".join(l for l in body.splitlines()
                     if not l.strip().startswith("#"))
    code = code.split('"""')[0] + '"""'.join(code.split('"""')[2:])
    assert "v_model_promoted" in code
    assert "derived_model_promotion" not in code


def test_a_missing_promotion_view_must_not_stop_pricing(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError('relation "v_model_promoted" does not exist')
    monkeypatch.setattr(pe, "rest", boom)
    assert pe._promoted_models() == {}


# ---------------------------------------------------------------------------
# and the SQL gate
# ---------------------------------------------------------------------------
def test_a_disagreement_is_only_tradeable_once_the_model_is_promoted():
    sql = "\n".join(l for l in (ROOT / "sql" / "ad4_25_model_forecast.sql").read_text().splitlines()
                    if not l.strip().startswith("--"))
    gate = re.search(r"as tradeable_view", sql)
    assert gate, "tradeable_view is gone"
    before = sql[max(0, gate.start() - 400): gate.start()]
    assert "p.state = 'promoted'" in before, (
        "beats_persistence is measured on held-out days of the model's own "
        "training window; it is not the bar for moving a price")
