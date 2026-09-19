"""Zero fitted models, for a week, with every run green.

Two independent blocks, both measured on the live database on 2026-09-19:

  WRONG SOURCE. The trainer read v_city_day_features, which is computed from
  raw weather_observations - and sql/ad4_29_retention prunes those. The view
  held 4,745 rows across 53 cities, about 89 days each, under MIN_DAYS=120.
  Every city was skipped. derived_city_day_features is the durable cache that
  exists precisely to survive that pruning and held 22,197 rows over the same
  cities, 2025-07-21 to 2026-09-19, up to 421 days for one city. The trainer
  never read it.

  CLOUD WAS MANDATORY. cloud_mean sat in BASE_FEATURES, and usable() rejects a
  day missing any base feature. Cities with 120 cloud-complete days: 0.
  Without cloud: 52. 959 usable city-days against 21,631.

Either one alone is enough to fit nothing. Together they made a weekly job
that read a populated table, fitted no city, wrote no model, exited 0 and
reported success - which is why it went a week without anyone noticing.

These tests drive main() rather than reading the source, because what failed
was not a line of code but the combination of a read, a filter and an exit
code, and only running all three together shows it.
"""

import math
import random
import sys

import pytest

import weather_model as wm


def days(n, *, cloud="all", cloud_effect=-1.1, seed=5, city="t"):
    """Days whose maximum follows a known rule, with a cloud feed of a chosen
    completeness - because completeness is the whole question here.

      all   every day has a cloud reading. Ordinary forward selection keeps it
            and no challenger is needed.
      most  one day in seven is missing it. select_features refuses a feature
            absent from any training day, so this is the case the challenger
            exists for: enough cloud days to fit on, not enough to fit with.
      rare  only one day in seven HAS it, which is roughly the real feed - too
            few to fit a cloud model on at all.
    """
    rng = random.Random(seed)
    rows, prev = [], None
    for i in range(n):
        oktas = rng.uniform(0, 8)
        dry = rng.uniform(0, 18)
        morning = 12 + 4 * math.sin(i / 30.0)
        mx = morning + 10 + cloud_effect * oktas + 0.35 * dry + rng.gauss(0, 0.4)
        if cloud == "all":
            reported = oktas
        elif cloud == "most":
            reported = None if i % 7 == 0 else oktas
        else:
            reported = oktas if i % 7 == 0 else None
        rows.append({
            "city_key": city,
            "obs_date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "max_c": mx, "n_obs": 24, "prev_max_c": prev if prev is not None else mx,
            "morning_temp_c": morning, "dewpoint_depression_c": dry,
            "cloud_mean": reported,
            "wind_mean": 4.0 + rng.uniform(-1, 1),
            "precip_total": 0.0,
        })
        prev = mx
    return rows


def _run(monkeypatch, cache_rows, *, argv=("weather_model",), fresh=None):
    """main() against a scripted cache. Returns (exit_code, asked, logged)."""
    asked, logged, written = [], {}, {}

    def fake_rest_all(path, params=None, **kw):
        asked.append(path)
        if path == "derived_city_day_features":
            return cache_rows
        if path == "v_city_day_features":
            return fresh or []
        return []                                  # no forecast days

    monkeypatch.setattr(wm, "rest_all", fake_rest_all)
    monkeypatch.setattr(wm, "log_run",
                        lambda job, status, n, detail=None: logged.update(
                            status=status, n=n, detail=detail or {}))
    monkeypatch.setattr(wm, "write_rows",
                        lambda table, rows, on: written.setdefault(table, rows) and 0
                        or len(rows))
    monkeypatch.setattr(sys, "argv", list(argv))
    return wm.main(), asked, logged, written


# ---------------------------------------------------------------------------
# the source it reads
# ---------------------------------------------------------------------------
def test_the_fit_reads_the_durable_cache_not_the_pruned_view(monkeypatch):
    code, asked, _, written = _run(monkeypatch, days(300))
    assert asked[0] == "derived_city_day_features", (
        "v_city_day_features is computed from raw observations, which retention "
        "prunes to about 90 days - under MIN_DAYS, so every city is skipped")
    assert code == 0
    assert written["derived_weather_model"], "a 300-day city must produce a model"


def test_the_anchor_days_are_topped_up_from_the_live_view(monkeypatch):
    """The history does not need to be fresh; the ANCHOR does.

    predict_forward walks back at most MAX_ANCHOR_AGE_DAYS from the first
    forecast day looking for a real observed maximum. The cache is refilled by
    a scheduled job, so if that job is a day late every forward prediction
    would stop - silently, because a fit with no forecast days still writes a
    model and still exits 0.
    """
    _, asked, _, _ = _run(monkeypatch, days(300))
    assert "v_city_day_features" in asked, (
        "nothing keeps the anchor current if the cache is the only source")


def test_a_topped_up_day_cannot_sneak_into_the_fit(monkeypatch):
    """Those rows carry a maximum and nothing else. usable() has to keep them
    out of the fit while predict_forward can still anchor on them."""
    bare = [{"city_key": "t", "obs_date": "2026-99-01", "max_c": 30.0, "n_obs": 24}]
    _, _, _, written = _run(monkeypatch, days(300), fresh={"t": bare})
    assert written["derived_weather_model"][0]["n_days"] == 300, (
        "a row with no features was fitted as though it had them")


# ---------------------------------------------------------------------------
# the exit code
# ---------------------------------------------------------------------------
def test_a_populated_cache_that_fits_nothing_is_a_failed_run(monkeypatch):
    code, _, logged, written = _run(monkeypatch, days(40))
    assert code == 1, (
        "reading 40 city-days and fitting nothing exited 0 for a week while the "
        "database held no model at all")
    assert not written
    assert logged["status"] == "attention"
    assert logged["detail"]["rows"] == 40
    assert logged["detail"]["cities_clearing_min_days"] == 0


def test_an_empty_cache_also_fails_but_says_something_different(monkeypatch, capsys):
    code, _, _, _ = _run(monkeypatch, [])
    assert code == 1
    err = capsys.readouterr().err
    assert "Derived Recompute" in err, (
        "an empty cache and a cache that cannot be fitted need different fixes")


def test_a_dry_run_reports_the_same_verdict(monkeypatch):
    """--dry-run means do not write, not do not judge."""
    code, _, _, _ = _run(monkeypatch, days(40), argv=("weather_model", "--dry-run"))
    assert code == 1
    assert _run(monkeypatch, days(300), argv=("weather_model", "--dry-run"))[0] == 0


# ---------------------------------------------------------------------------
# the preflight
# ---------------------------------------------------------------------------
def test_the_preflight_distinguishes_the_four_causes(monkeypatch, capsys):
    _run(monkeypatch, days(40))
    out = capsys.readouterr().out
    assert "read 40 city-day(s) across 1 city/cities" in out   # was anything read
    assert "2026-01-01" in out and "2026-02-12" in out          # what date range
    assert "usable days per city" in out                       # how many survive
    assert "clear MIN_DAYS" in out                             # and how many fit


def test_the_preflight_names_a_feature_that_is_missing_everywhere(monkeypatch, capsys):
    rows = days(300)
    for r in rows:
        r["cloud_mean"] = None
    _run(monkeypatch, rows)
    assert "cloud_mean 300 (100%)" in capsys.readouterr().out, (
        "a feature absent from every row is the difference between 'too few "
        "days' and 'one column is never collected'")


# ---------------------------------------------------------------------------
# cloud, which is real and patchy
# ---------------------------------------------------------------------------
def test_cloud_is_not_mandatory_so_a_patchy_feed_still_fits(monkeypatch):
    code, _, _, written = _run(monkeypatch, days(300, cloud="rare"))
    assert code == 0, (
        "cloud reaches one day in seven here, which is roughly the real feed. "
        "Mandatory, it fits nothing; optional, it fits the other six")
    assert written["derived_weather_model"][0]["n_days"] == 300


def test_cloud_is_never_imputed(monkeypatch):
    """A mean okta filled in for a missing one is an invention with a
    coefficient on it."""
    rows = days(300, cloud="rare")
    _, _, _, written = _run(monkeypatch, rows)
    coef = written["derived_weather_model"][0]["coefficients"]
    assert "cloud_mean" not in coef


def test_a_complete_cloud_feed_needs_no_challenger_at_all():
    """Ordinary forward selection already keeps a feature present on every
    training day, so the challenger correctly finds nothing left to gain. It is
    worth pinning: a challenger that "wins" here would be scoring the same
    model twice and calling the difference skill."""
    rows = days(300, cloud="all")
    base, _ = wm.fit_city(rows, wm.BASE_FEATURES)
    assert "cloud_mean" in base["features"]
    c = wm.cloud_challenger(rows, base)
    assert c["qualified"] and not c["better"]
    assert abs(c["gain_c"]) < wm.MIN_MATERIAL_GAIN_C


def test_the_cloud_challenger_scores_both_models_on_the_same_days():
    """The case it exists for: enough cloud days to FIT on, not enough to be
    kept by selection, which refuses a feature missing from any training day."""
    rows = days(300, cloud="most")
    base, _ = wm.fit_city(rows, wm.BASE_FEATURES)
    assert "cloud_mean" not in base["features"], (
        "selection kept a feature that is absent from a seventh of the days")

    c = wm.cloud_challenger(rows, base)
    assert c["qualified"], c
    assert c["n_scored"] <= len(base["holdout_dates"]), (
        "the challenger was scored on days the shipped model was not")
    assert c["n_scored"] >= 20
    assert c["gain_c"] == pytest.approx(c["base_mae_c"] - c["cloud_mae_c"], abs=1e-4)
    assert c["better"], (
        "these days were generated with a -1.1C per okta cloud effect, so a "
        "model that can see cloud must beat one that cannot")


def test_the_cloud_challenger_says_why_it_did_not_run():
    """Silence here reads as 'cloud does not help', which is the opposite of
    what a missing feed means."""
    rows = days(300, cloud="rare")
    base, _ = wm.fit_city(rows, wm.BASE_FEATURES)
    c = wm.cloud_challenger(rows, base)
    assert c["qualified"] is False
    assert c["n_cloud_days"] < wm.MIN_CLOUD_DAYS
    assert str(wm.MIN_CLOUD_DAYS) in c["verdict"]


def test_the_challenger_is_compared_against_the_model_that_actually_shipped():
    """Not against a re-fit of the base features on the cloud-complete subset.
    The question is whether cloud beats WHAT IS RUNNING."""
    rows = days(300)
    base, _ = wm.fit_city(rows, wm.BASE_FEATURES)
    c = wm.cloud_challenger(rows, base)
    src = open(wm.__file__).read()
    assert 'base_fit["coefficients"]' in src and 'base_fit["features"]' in src
    assert c["base_mae_c"] > 0
