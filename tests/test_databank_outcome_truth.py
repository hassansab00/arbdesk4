"""Outcome facts may be written only from independent final evidence."""
from pathlib import Path

import databank


ROOT = Path(__file__).resolve().parents[1]


def test_forecasts_do_not_freeze_from_an_unverified_running_max(monkeypatch):
    # Both seams: bank_forecasts pages weather_forecasts through rest_all,
    # while _already_banked is stubbed out the same way.
    def _rows(path,*args,**kwargs):
        return [{
            "forecast_id":"f1",
            "city_key":"london","model":"model","run_at":"2026-09-12T08:00:00Z",
            "for_date":"2026-09-12","lead_days":1,"forecast_max_c":21,
        }] if path=="weather_forecasts" else []
    monkeypatch.setattr(databank,"rest",_rows)
    monkeypatch.setattr(databank,"rest_all",_rows)
    assert databank.bank_forecasts({},7,False)==[]


def test_band_truth_comes_from_venue_not_temperature_math(monkeypatch):
    market="30000000-0000-0000-0000-000000000001"
    band="20000000-0000-0000-0000-000000000001"
    def rows(path,params=None):
        if path=="fact_band_outcome": return []
        if path=="markets": return [{"market_id":market,"city_key":"london","resolution_date":"2026-09-12"}]
        if path=="v_venue_market_resolution": return [{"market_id":market,"resolution_state":"confirmed"}]
        if path=="v_venue_band_resolution": return [{"band_id":band,"market_id":market,"settled_yes":True,
                                                        "resolution_state":"confirmed","confirmed_at":"now"}]
        if path=="bands": return [{"band_id":band,"market_id":market,"band_lo":20,"band_hi":21,
                                    "open_low":False,"open_high":False}]
        if path=="band_probabilities": return [{"band_id":band,"raw_prob":.2,"calibrated_prob":None,
                                                  "computed_at":"now"}]
        if path in ("v_opportunities","v_latest_edge"): return []
        raise AssertionError(path)
    monkeypatch.setattr(databank,"rest",rows)
    monkeypatch.setattr(databank,"rest_all",lambda path,params=None,**kw: rows(path,params))
    # 35 C is outside the 20-21 C band. The venue-confirmed winner must still
    # be used, making any source disagreement visible rather than rewritten.
    verified_weather={("london","2026-09-12"):{"max_c":35,"source":"authority","n_obs":None}}
    result=databank.bank_bands(verified_weather,7,False)
    assert len(result)==1
    assert result[0]["settled_yes"] is True
    assert result[0]["observed_max_c"]==35


def test_partial_venue_ladder_is_not_banked(monkeypatch):
    market="30000000-0000-0000-0000-000000000001"
    def rows(path,params=None):
        if path=="fact_band_outcome": return []
        if path=="markets": return [{"market_id":market,"city_key":"london","resolution_date":"2026-09-12"}]
        if path=="v_venue_market_resolution": return [{"market_id":market,"resolution_state":"partial"}]
        if path=="v_venue_band_resolution": return []
        if path=="bands": return []
        raise AssertionError(path)
    monkeypatch.setattr(databank,"rest",rows)
    monkeypatch.setattr(databank,"rest_all",lambda path,params=None,**kw: rows(path,params))
    assert databank.bank_bands({},7,False)==[]


def test_all_model_feedback_consumers_require_verified_scope():
    probability = (ROOT / "scripts/probability_engine.py").read_text()
    backtest = (ROOT / "scripts/backtest/runner.py").read_text()
    analytics = (ROOT / "web/app/analytics/page.tsx").read_text()
    for source in (probability, backtest, analytics):
        assert "verified_outcomes_v1" in source


def test_backtest_outcomes_are_final_evidence_not_running_maxima():
    source = (ROOT / "scripts/backtest/runner.py").read_text()
    observed = source.split("def _daily_max_observed", 1)[1].split("def run", 1)[0]
    assert 'rest_all("v_verified_weather_outcomes"' in observed
    assert 'rest_all("weather_observations"' not in observed


def test_legacy_artifacts_are_preserved_but_untrusted():
    migration = (ROOT / "supabase/migrations/20260913102000_phase2a_artifact_provenance.sql").read_text().lower()
    for table in (
        "derived_forecast_skill",
        "derived_forecast_skill_model",
        "derived_calibration_adjustment",
    ):
        assert f"alter table if exists public.{table}" in migration
        assert f"delete from public.{table}" not in migration
        assert f"truncate public.{table}" not in migration
    assert "evidence_scope = 'verified_outcomes_v1'" in migration
