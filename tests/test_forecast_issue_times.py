"""When was a forecast issued? (plan v2 P2.6)

weather_forecasts.run_at is NWS's issuance, Open-Meteo's fetch time, or a
synthetic midnight UTC, depending on the source. v_forecast_issued turns that
into one honest issued_at. These tests pin who reads it and how.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import measure_skill  # noqa: E402
import regime  # noqa: E402

MIGRATION = (ROOT / "supabase/migrations/20260923160000_when_was_the_forecast_issued.sql").read_text()


def test_each_source_says_where_its_issue_time_came_from():
    for source, kind in [("api.weather.gov", "provider_update_time"),
                         ("open-meteo", "ingest_time"),
                         ("open-meteo-previous-runs", "ingest_time_true_issue_unverified")]:
        assert f"when '{source}'" in MIGRATION and f"'{kind}'" in MIGRATION
    assert "when 'open-meteo-previous-runs' then f.observed_at" in MIGRATION, (
        "the backfill's run_at is a synthetic midnight; until P7.2 proves which run each "
        "value came from, it is known only from when it was ingested")


def _capture(monkeypatch, attr):
    """The reads regime makes through `attr`, less weather_history's look at
    the newest prune (ingest_log; none here, so the read is the database's)."""
    seen = []

    def fake(path, params, **kw):
        if path != "ingest_log":
            seen.append((path, dict(params)))
        return []
    monkeypatch.setattr(regime, "rest", fake)
    if attr != "rest":
        monkeypatch.setattr(regime, attr, fake)
    return seen


def test_an_ingest_bound_does_not_invent_an_issue_date():
    """Rehearsed live 23 Sep: derived from the ingest bound, same_day_issue
    was true on all 32,830 backfill rows, and day-ahead skill would have
    dropped every one of them."""
    assert "case when i.true_time_known then" in MIGRATION
    assert "f.source is distinct from 'open-meteo-previous-runs' as true_time_known" in MIGRATION


def test_a_backtest_reads_what_was_issued_by_the_decision(monkeypatch):
    seen = _capture(monkeypatch, "rest")
    regime._forecasts_for_date("nyc", "2026-09-13", as_of=__import__("datetime").datetime(2026, 9, 12, 12))
    path, params = seen[0]
    assert path == "v_forecast_issued"
    assert params["issued_at"].startswith("lte.2026-09-12T12:00")
    assert "run_at" not in params


def test_live_regime_reads_are_unchanged(monkeypatch):
    seen = _capture(monkeypatch, "rest")
    regime._forecasts_for_date("nyc", "2026-09-13")
    assert seen[0][0] == "weather_forecasts"


def test_the_disagreement_history_is_cut_on_issued_at_too(monkeypatch):
    import datetime as dt
    seen = _capture(monkeypatch, "rest_all")
    regime._history_disagreement("nyc", dt.date(2026, 9, 13), as_of=dt.datetime(2026, 9, 12, 12))
    path, params = seen[0]
    assert path == "v_forecast_issued" and "issued_at" in params and "run_at" not in params


def test_day_ahead_skill_drops_a_row_issued_on_the_day_it_forecasts():
    rows = [
        {"lead_days": 1, "same_day_issue": False, "forecast_max_c": 20},
        {"lead_days": 1, "same_day_issue": True, "forecast_max_c": 21},   # knew the morning
        {"lead_days": 0, "same_day_issue": True, "forecast_max_c": 22},   # lead 0 is same-day
        {"lead_days": 2, "same_day_issue": None, "forecast_max_c": 23},   # backfill: unknown, kept
    ]
    kept = measure_skill.day_ahead_only(rows)
    assert [r["forecast_max_c"] for r in kept] == [20, 22, 23]


def test_skill_reads_the_view_that_knows_the_issue_date():
    src = (ROOT / "scripts/measure_skill.py").read_text()
    assert 'weather_history.read("v_forecast_issued"' in src
    assert "one_row_per_run_key(day_ahead_only(fc))" in src
