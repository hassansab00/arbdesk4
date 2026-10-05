"""The model inputs have their own freshness (audit P3, 5 Oct 2026).

The audit of 4 Oct: "Track ensemble/model input freshness separately from the
main forecast's freshness." supabase/migrations/20261005110000 judges each
clock on its own in v_city_forecast_inputs (tests/database/forecast-inputs.cjs
drives it on a real Postgres); scripts/station_correction.py records the runs
each corrected row combined. These hold the view to the code and the specs it
claims to follow, so neither can drift on its own."""
import os
import re

import ingest_forecasts
import station_correction

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIG = open(os.path.join(ROOT, "supabase", "migrations",
                        "20261005110000_the_model_inputs_have_their_own_freshness.sql")).read()


def _spec_hours(table):
    spec = open(os.path.join(ROOT, "sql", "ad4_39_freshness.sql")).read()
    m = re.search(rf"\('{table}',\s*'\w+',\s*(\d+),", spec)
    assert m, table
    return int(m.group(1))


def test_the_thresholds_are_the_freshness_spec_s():
    assert _spec_hours("weather_forecasts") == 8
    assert _spec_hours("weather_forecast_models") == 36
    assert "when now() - m.run_at > interval '8 hours' then 'stale'" in MIG
    assert "when now() - md.run_at > interval '36 hours' then 'stale'" in MIG


def test_the_model_inputs_are_the_runs_the_fit_reads():
    """The view's current run is the one the nightly ingest writes and the
    station correction combines - the same source, three places."""
    assert ingest_forecasts.CURRENT_SOURCE == station_correction.FORWARD_SOURCE == "open-meteo-models-current"
    assert MIG.count("m.source = 'open-meteo-models-current'") == 3


def test_the_main_forecast_is_the_one_the_engine_picks():
    src = open(os.path.join(ROOT, "scripts", "probability_engine.py")).read()
    assert '("order", "lead_days.asc,run_at.desc")' in src
    # Postgres sorts nulls last in ascending order by default; said out loud here
    assert "order by w.lead_days asc nulls last, w.run_at desc" in MIG


def test_the_corrected_row_records_what_it_combined():
    src = open(os.path.join(ROOT, "scripts", "station_correction.py")).read()
    assert '"inputs_oldest_run_at": oldest, "inputs_newest_run_at": newest,' in src
    assert "add column if not exists inputs_oldest_run_at timestamptz" in MIG
    assert "add column if not exists inputs_newest_run_at timestamptz" in MIG
