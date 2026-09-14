"""Which forecast gets priced, and whose skill measures it.

Two defects the audit named, both of them about IDENTITY:

  - the engine ordered forecasts by lead_days alone, so among the several
    runs that normally share a lead the row it priced was whatever PostgREST
    returned first;
  - skill was scored by (city, lead) with the model discarded, so every
    model's error landed in one number.

These tests pin the fixes and, just as importantly, pin the fallback: on a
desk where only one model has history, nothing may go dark.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))


# ---------------------------------------------------------------------------
# Deterministic selection
# ---------------------------------------------------------------------------
def _params_of(monkeypatch, module, **kwargs):
    """Run _forecast_for and hand back the query it actually sent."""
    seen = {}

    def fake_rest(path, params):
        seen["path"] = path
        seen["params"] = dict(params)
        seen["raw"] = list(params)
        return []

    monkeypatch.setattr(module, "rest", fake_rest)
    module._forecast_for("nyc", "2026-09-13", **kwargs)
    return seen


def test_forecast_pick_orders_by_lead_then_newest_run(monkeypatch):
    import probability_engine as pe

    seen = _params_of(monkeypatch, pe)
    assert seen["params"]["order"] == "lead_days.asc,run_at.desc", (
        "shortest lead is not enough - several runs share a lead, and without "
        "run_at.desc the row priced is arbitrary"
    )
    assert seen["params"]["limit"] == "1"


def test_forecast_pick_without_as_of_does_not_filter_run_at(monkeypatch):
    import probability_engine as pe

    seen = _params_of(monkeypatch, pe)
    assert "run_at" not in seen["params"], (
        "live pricing must see the newest run there is"
    )


def test_forecast_pick_with_as_of_excludes_later_runs(monkeypatch):
    import probability_engine as pe

    seen = _params_of(monkeypatch, pe, as_of="2026-09-07T00:00:00Z")
    assert seen["params"]["run_at"] == "lte.2026-09-07T00:00:00Z", (
        "a backtest must not be able to price against a forecast that did "
        "not exist at the decision time"
    )


def test_denver_case_newest_run_wins(monkeypatch):
    """The real rows that motivated this, in the order the old code saw them.

    A fake PostgREST that honours order= the way the real one does would be a
    reimplementation of PostgREST; instead assert the contract that makes the
    right row win - newest run first among rows tied at the shortest lead.
    """
    import probability_engine as pe

    rows = [
        {"for_date": "2026-09-13", "lead_days": 7, "forecast_max_c": 11.2,
         "model": "open_meteo_forecast", "run_at": "2026-09-06T15:21:27Z"},
        {"for_date": "2026-09-13", "lead_days": 7, "forecast_max_c": 30.3,
         "model": "open_meteo_forecast", "run_at": "2026-09-06T21:00:43Z"},
        {"for_date": "2026-09-13", "lead_days": 7, "forecast_max_c": 26.2,
         "model": "open_meteo_forecast", "run_at": "2026-09-07T03:00:44Z"},
    ]

    def fake_rest(path, params):
        p = dict(params)
        key, direction = p["order"].split(",")[-1].split(".")
        ordered = sorted(rows, key=lambda r: r[key], reverse=(direction == "desc"))
        if "run_at" in p:
            cut = p["run_at"].split("lte.", 1)[1]
            ordered = [r for r in ordered if r["run_at"] <= cut]
        return ordered[: int(p["limit"])]

    monkeypatch.setattr(pe, "rest", fake_rest)

    got = pe._forecast_for("denver", "2026-09-13")
    assert got["forecast_max_c"] == 26.2, "the newest run must be the one priced"

    # As of just after the second run, the third has not happened yet.
    got = pe._forecast_for("denver", "2026-09-13", as_of="2026-09-06T22:00:00Z")
    assert got["forecast_max_c"] == 30.3
    assert got["forecast_max_c"] != 11.2, "never the stalest run"


# ---------------------------------------------------------------------------
# The ensemble rule
# ---------------------------------------------------------------------------
def _skill_engine(monkeypatch, pooled, per_model):
    import probability_engine as pe

    pe._skill_cache.clear()

    def fake_rest(path, params):
        p = dict(params)
        if path == "derived_forecast_skill":
            return [pooled] if pooled else []
        if path == "derived_forecast_skill_model":
            want = p["model"].split("eq.", 1)[1]
            return [per_model] if per_model and per_model["model"] == want else []
        raise AssertionError(f"unexpected read of {path}")

    monkeypatch.setattr(pe, "rest", fake_rest)
    return pe


def test_trusted_model_row_is_preferred_over_the_blend(monkeypatch):
    pooled = {"lead_days": 1, "n_days": 900, "mae_c": 1.4, "bias_c": 0.2}
    per_model = {"model": "nws", "lead_days": 1, "n_days": 400,
                 "mae_c": 0.8, "bias_c": -0.1}
    pe = _skill_engine(monkeypatch, pooled, per_model)

    got = pe._skill_for("nyc", 1, "nws")
    assert got["mae_c"] == 0.8, (
        "once a model has its own trusted sample, its own error is the "
        "correct grain"
    )


def test_thin_model_row_falls_back_to_the_blend(monkeypatch):
    """nws has 84 rows and starts 2026-09-06. It must not price on that."""
    pooled = {"lead_days": 1, "n_days": 900, "mae_c": 1.4, "bias_c": 0.2}
    per_model = {"model": "nws", "lead_days": 1, "n_days": 3,
                 "mae_c": 0.1, "bias_c": 0.0}
    pe = _skill_engine(monkeypatch, pooled, per_model)

    got = pe._skill_for("nyc", 1, "nws")
    assert got["mae_c"] == 1.4, (
        "three days of history producing mae 0.1 is not skill, it is noise - "
        "and pricing on it would make the desk wildly overconfident"
    )


def test_missing_model_row_falls_back_rather_than_going_dark(monkeypatch):
    """No per-model row at all must behave exactly as it did before ad4_49."""
    pooled = {"lead_days": 1, "n_days": 900, "mae_c": 1.4, "bias_c": 0.2}
    pe = _skill_engine(monkeypatch, pooled, None)

    got = pe._skill_for("nyc", 1, "open_meteo_forecast")
    assert got is not None and got["mae_c"] == 1.4, (
        "no skill row floors confidence at 0.1 - the fallback exists so that "
        "adding a second model never takes cities dark"
    )


def test_absent_model_table_is_not_an_error(monkeypatch):
    """Running the engine before sql/ad4_49 is installed must still work."""
    import probability_engine as pe

    pe._skill_cache.clear()
    pooled = {"lead_days": 1, "n_days": 900, "mae_c": 1.4, "bias_c": 0.2}

    def fake_rest(path, params):
        if path == "derived_forecast_skill_model":
            raise RuntimeError("404: relation does not exist")
        return [pooled]

    monkeypatch.setattr(pe, "rest", fake_rest)
    assert pe._skill_for("nyc", 1, "nws")["mae_c"] == 1.4


def test_no_model_argument_reads_only_the_pooled_table(monkeypatch):
    """The old call signature must keep its old meaning."""
    import probability_engine as pe

    pe._skill_cache.clear()
    touched = []

    def fake_rest(path, params):
        touched.append(path)
        return [{"lead_days": 1, "n_days": 900, "mae_c": 1.4, "bias_c": 0.2}]

    monkeypatch.setattr(pe, "rest", fake_rest)
    pe._skill_for("nyc", 1)
    assert touched == ["derived_forecast_skill"]


def test_skill_asks_for_verified_evidence_first(monkeypatch):
    """Verified skill is the FIRST thing asked for, and when it exists it is
    the only thing read - no second, looser query goes out behind it."""
    import probability_engine as pe

    pe._skill_cache.clear()
    calls = []

    def fake_rest(path, params):
        calls.append((path, dict(params)))
        return [{"lead_days": 1, "n_days": 900, "mae_c": 1.4, "bias_c": 0.2,
                 "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE}]

    monkeypatch.setattr(pe, "rest", fake_rest)
    row = pe._skill_for("nyc", 1, "nws")
    expected = f"eq.{pe.VERIFIED_EVIDENCE_SCOPE}"
    assert calls[0][1]["evidence_scope"] == expected
    assert row["verified"] is True
    assert all(p.get("evidence_scope") == expected for _, p in calls), (
        "a verified row was available; nothing should have queried without the scope")


def test_measured_skill_is_used_when_nothing_is_verified_yet(monkeypatch):
    """THE BUG THIS ENCODES.

    Phase 2A filtered every skill read by `evidence_scope = verified_outcomes_v1`.
    weather_resolution_evidence has never been captured, so that column is NULL
    on all 5,187 measured rows - and `null = anything` is never true. Every
    read returned nothing, every city fell through to COLD_START_MAE_C, and the
    desk priced 48 cities at sigma 5.0-15.0 C while holding a measured 1.47 C.
    London read "most likely 27C or higher at 32%" against a market at 1c.

    A measurement the desk actually made must beat a constant somebody chose.
    What verification gates is whether the price may be TRADED - which the
    companion assertion below pins down - not how wide the distribution is.
    """
    import probability_engine as pe

    pe._skill_cache.clear()
    calls = []

    def fake_rest(path, params):
        calls.append((path, dict(params)))
        if "evidence_scope" in dict(params):
            return []                       # nothing corroborated yet
        return [{"lead_days": 1, "n_days": 417, "mae_c": 1.47, "bias_c": 0.2,
                 "evidence_scope": None}]

    monkeypatch.setattr(pe, "rest", fake_rest)
    row = pe._skill_for("nyc", 1, "nws")
    assert row is not None, "measured skill was discarded in favour of a constant"
    assert row["mae_c"] == 1.47
    assert row["verified"] is False, "it must be marked so the ticket can be blocked"
    assert row["mae_c"] < pe.COLD_START_MAE_C


def test_unverified_skill_is_never_tradeable():
    """The half of Phase 2A that must survive the fix above: an uncorroborated
    width may price the board, and may never carry an order."""
    import inspect
    import probability_engine as pe

    body = inspect.getsource(pe.process_city_day)
    stripped = "\n".join(l for l in body.splitlines()
                          if not l.lstrip().startswith("#"))
    assert 'skill_verified is False' in stripped and 'pricing_eligible = False' in stripped, (
        "process_city_day must block pricing when the skill it used is unverified")


# ---------------------------------------------------------------------------
# Scoring: one row per run key, and per-model grain
# ---------------------------------------------------------------------------
def test_reruns_of_the_same_key_collapse_to_the_newest():
    import measure_skill as ms

    rows = [
        {"model": "m", "for_date": "2026-09-13", "lead_days": 7,
         "forecast_max_c": 11.2, "run_at": "2026-09-06T15:21:27Z"},
        {"model": "m", "for_date": "2026-09-13", "lead_days": 7,
         "forecast_max_c": 30.3, "run_at": "2026-09-06T21:00:43Z"},
        {"model": "m", "for_date": "2026-09-13", "lead_days": 7,
         "forecast_max_c": 26.2, "run_at": "2026-09-07T03:00:44Z"},
    ]
    out = ms.one_row_per_run_key(rows)
    assert len(out) == 1, "one forecast day at one lead is one observation"
    assert out[0]["forecast_max_c"] == 26.2


def test_different_models_are_not_collapsed_into_each_other():
    import measure_skill as ms

    rows = [
        {"model": "nws", "for_date": "2026-09-10", "lead_days": 4,
         "forecast_max_c": 32.0, "run_at": "2026-09-06T06:00:00Z"},
        {"model": "open_meteo_forecast", "for_date": "2026-09-10",
         "lead_days": 4, "forecast_max_c": 35.0, "run_at": "2026-09-06T06:00:00Z"},
    ]
    out = ms.one_row_per_run_key(rows)
    assert len(out) == 2, "two models forecasting the same day are two forecasts"


def test_skill_stats_refuses_a_sample_too_thin_to_mean_anything():
    import measure_skill as ms

    assert ms.skill_stats([0.1] * (ms.MIN_SAMPLE - 1), 1.0) is None
    assert ms.skill_stats([0.1] * ms.MIN_SAMPLE, 1.0) is not None


def test_skill_stats_arithmetic():
    import measure_skill as ms

    errs = [2.0, -2.0] * 6          # mae 2, bias 0, nothing within one band
    s = ms.skill_stats(errs, 1.0)
    assert s["n_days"] == 12
    assert s["mae_c"] == 2.0
    assert s["bias_c"] == 0.0
    assert s["mae_bands"] == 2.0
    assert s["pct_within_one_band"] == 0.0

    errs = [0.5, -0.5] * 6          # bias 0, all inside a 1 C band
    s = ms.skill_stats(errs, 1.0)
    assert s["mae_c"] == 0.5
    assert s["pct_within_one_band"] == 1.0


def test_fahrenheit_cities_score_against_their_own_band_width():
    """A 2 F band is 1.11 C. mae_bands must use it, not 1 C."""
    import measure_skill as ms

    band_f = 2.0 * 5.0 / 9.0
    s = ms.skill_stats([1.0] * 20, band_f)
    assert s["band_width_c"] == round(band_f, 3)
    assert s["mae_bands"] == round(1.0 / band_f, 3)
    assert s["pct_within_one_band"] == 1.0


@pytest.mark.parametrize("path", ["scripts/probability_engine.py"])
def test_engine_never_orders_forecasts_by_lead_alone(path):
    """Regression guard for the exact string that caused this."""
    import io
    import tokenize

    root = os.path.join(os.path.dirname(__file__), "..")
    with open(os.path.join(root, path), encoding="utf-8") as fh:
        src = fh.read()

    code_lines = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in (tokenize.COMMENT, tokenize.STRING, tokenize.NL):
            continue
        code_lines.add(tok.start[0])

    for i, line in enumerate(src.splitlines(), start=1):
        if i in code_lines and '"lead_days.asc"' in line.replace("'", '"'):
            raise AssertionError(
                f"{path}:{i} orders forecasts by lead_days alone; rows tied at "
                "the shortest lead are then picked arbitrarily"
            )


# ---------------------------------------------------------------------------
# A MULTIPLIER SUBSTITUTES FOR A MEASUREMENT; IT DOES NOT SUPPLEMENT ONE.
#
# sigma was mae_c * 1.2533 * regime * calibration * divergence. Regime reaches
# 2.00 (BLOCKED) and divergence reaches 2.00, so a city with measured skill had
# its own measurement inflated up to fourfold:
#
#   milan    mae 0.75 -> 0.94 sigma, published 2.91   (3.10x)
#   seattle  mae 2.39 -> 2.99 sigma, published 8.97   (3.00x)
#   nyc      mae 1.28 -> 1.61 sigma, published 4.22   (2.63x)
#
# which put nyc's modal bucket at "65F or below" on a 23.2C (73.8F) forecast.
# The tell was that TOMORROW read correctly everywhere - one model quoted, so
# no divergence factor - while today, the better-informed day, did not.
#
# mae_c is the mean |forecast - actual| over this city's own settled days. Those
# days already contain the uncertain regimes and the disagreements. Widening a
# measurement because conditions are uncertain asserts it was taken elsewhere.
# ---------------------------------------------------------------------------
def _sigma_source():
    import inspect
    import probability_engine as pe
    body = inspect.getsource(pe.process_city_day)
    return "\n".join(l for l in body.splitlines() if not l.lstrip().startswith("#"))


def test_measured_width_is_not_multiplied_by_regime_or_divergence():
    src = _sigma_source()
    assert 'width_is_measured = skill_source in ("city_lead", "city_lead_proxy")' in src
    assert "regime_sigma_mult = 1.0 if width_is_measured else reg.sigma_multiplier" in src
    assert "div_sigma_mult = 1.0 if width_is_measured else div_mult" in src


def test_cold_start_still_widens_because_nothing_was_measured():
    """The multipliers are correct where they came from: on a cold start mae_c
    is COLD_START_MAE_C, a constant nobody measured, and regime is the only
    information there is. Removing them THERE would be the opposite bug."""
    src = _sigma_source()
    assert "else reg.sigma_multiplier" in src and "else div_mult" in src, (
        "global_lead_p75 and fixed_cold_start must keep their widening")


def test_calibration_always_applies_measured_or_not():
    """ad4_45 fits calibration FROM settled outcomes, by asking whether the
    stated sigma matched reality. That corrects the measurement rather than
    second-guessing it, so it is never switched off."""
    src = _sigma_source()
    assert "sigma = sigma_historical * cal_mult * div_sigma_mult" in src
    assert "cal_mult = 1.0 if width_is_measured" not in src


def test_todays_disagreement_still_lowers_confidence():
    """The today-specific signal is moved, not discarded. 'The models disagree
    unusually today' is a reason to trust the number less, not to publish a
    different one."""
    src = _sigma_source()
    assert "confidence *= min(1.0, 1.0 / div_mult)" in src
