"""The authorized primary replaces a complete distribution at supported local
horizons; stale inputs, another listing and missing lineage keep the baseline."""
import datetime as dt
from types import SimpleNamespace

import pytest
import predictive_primary as pp

UTC = dt.timezone.utc


def test_horizon_routing_is_city_local_and_retains_the_better_morning_path():
    now = dt.datetime(2026, 10, 17, 9, 36, tzinfo=UTC)
    assert pp.eligible("2026-10-17", "Asia/Riyadh", now)  # 12:36
    assert not pp.eligible("2026-10-17", "America/New_York", now)  # 05:36
    assert not pp.eligible("2026-10-18", "Asia/Riyadh", now)
    assert not pp.eligible("2026-10-16", "Asia/Riyadh", now)
    early = now - dt.timedelta(hours=2)
    assert pp.eligible("2026-10-17", "Asia/Riyadh", early, ["prepeak_2h"])
    assert not pp.eligible("2026-10-17", "Asia/Riyadh", early, ["morning", "prepeak_2h"])
    assert not pp.eligible("2026-10-17", None, now)


def test_fresh_reports_and_multiple_weather_models_are_required():
    row = dict(rmax_c=35, obs_age_min=45, fc_models_n=7, fch_n_known=24)
    assert pp.input_problem(row) is None
    assert pp.input_problem(dict(row, obs_age_min=91))
    assert pp.input_problem(dict(row, obs_age_min=-1))
    assert pp.input_problem(dict(row, fc_models_n=3))
    assert pp.input_problem(dict(row, fch_n_known=19))
    assert pp.input_problem(dict(row, rmax_c=None))
    for p in ([.5, .5], [.01, .99]):
        assert pp.valid_distribution(["a", "b"], p)
    for p in ([.4, .4], [float("nan"), .5], [-.1, 1.1], [.5]):
        assert not pp.valid_distribution(["a", "b"], p)


def test_one_complete_primary_replaces_the_ladder_without_old_calibration(monkeypatch):
    monkeypatch.setenv("PREDICTIVE_PRIMARY", pp.POLICY)
    monkeypatch.setattr("common.model_version_id", lambda *a, **k: "registered")
    baseline = ([dict(band_id="a", raw_prob=.9, calibrated_prob=.8, centre_c=34, sigma_c=1,
                      pricing_eligible=False, pricing_block_reason="no_verified_skill"),
                 dict(band_id="b", raw_prob=.1, calibrated_prob=.2, centre_c=34, sigma_c=1)],
                SimpleNamespace(label="NORMAL"), ["priced_from:nws:old", "calibrated:old", "station_width:old"])
    monkeypatch.setattr(pp, "_predictions", {("jeddah", "2026-10-17", frozenset(("a", "b"))):
        dict(version="wxrd3:v3:checked", as_of="2026-10-17T09:36:00+00:00", probs={"a": .25, "b": .75},
             floor_c=35, unit="C", floor_unit=35, input_sha256="input")})
    out, reg, why = pp.apply("jeddah", "2026-10-17", baseline)
    assert [r["calibrated_prob"] for r in out] == [.25, .75]
    assert [r["raw_prob"] for r in out] == [.25, .75]
    assert all(r["forecast_version"] == "registered" and r["calibration_version"] is None for r in out)
    assert all(r["centre_c"] is None and r["sigma_c"] is None for r in out), "no fabricated normal centre/width"
    assert out[0]["pricing_eligible"] is False and out[0]["pricing_block_reason"] == "no_verified_skill"
    assert baseline[0][0]["calibrated_prob"] == .8, "the input result is untouched"
    assert "priced_from:wxrd3:v3:checked" in why and not any(x.startswith("calibrated:") for x in why)
    assert pp.apply("other_city", "2026-10-17", baseline) is baseline
    assert pp.apply("jeddah", "2026-10-18", baseline) is baseline
    twin = ([dict(band_id="x", calibrated_prob=.5), dict(band_id="y", calibrated_prob=.5)], reg, [])
    assert pp.apply("jeddah", "2026-10-17", twin) is twin
    monkeypatch.setattr("common.model_version_id", lambda *a, **k: None)
    assert pp.apply("jeddah", "2026-10-17", baseline)[0] is baseline[0]


def test_a_refused_primary_keeps_the_working_baseline_and_marks_fallback(monkeypatch):
    monkeypatch.setenv("PREDICTIVE_PRIMARY", pp.POLICY)
    monkeypatch.setattr(pp, "_predictions", {})
    monkeypatch.setattr(pp, "_eligible", {("jeddah", "2026-10-17")})
    base = ([dict(band_id="a", calibrated_prob=1)], None, ["priced_from:nws"])
    got = pp.apply("jeddah", "2026-10-17", base)
    assert got[0] is base[0] and got[2][-1].startswith("primary_fallback:")
    monkeypatch.delenv("PREDICTIVE_PRIMARY")
    assert pp.apply("jeddah", "2026-10-17", base) is base


@pytest.mark.parametrize("unit,primary_degree,same_degree_c,new_degree_c", [
    ("C", 35, 35.4, 35.5),
    ("F", 95, 35.1, (95.5 - 32) * 5 / 9),
])
def test_a_newer_station_maximum_keeps_the_baseline_before_version_registration(
        monkeypatch, unit, primary_degree, same_degree_c, new_degree_c):
    monkeypatch.setenv("PREDICTIVE_PRIMARY", pp.POLICY)
    registrations = []
    monkeypatch.setattr("common.model_version_id", lambda *a, **k: registrations.append(a) or "registered")
    monkeypatch.setattr(pp, "_predictions", {("city", "2026-10-17", frozenset(("a",))):
        dict(version="wxrd3:v3:checked", as_of="2026-10-17T09:36:00+00:00", probs={"a": 1},
             floor_c=35, unit=unit, floor_unit=primary_degree, input_sha256="input")})
    base = ([dict(band_id="a", observed_floor_c=new_degree_c, calibrated_prob=.8,
                  pricing_eligible=False, pricing_block_reason="no_verified_skill")], None, ["priced_from:nws"])
    rows, reg, why = pp.apply("city", "2026-10-17", base)
    assert rows is base[0] and why[-1].endswith(":newer_station_maximum")
    assert not registrations, "a stale primary must not register or replace any band"
    base[0][0]["observed_floor_c"] = same_degree_c
    rows, reg, why = pp.apply("city", "2026-10-17", base)
    assert rows[0]["calibrated_prob"] == 1 and registrations
    assert rows[0]["pricing_eligible"] is False


def test_a_primary_cannot_price_if_its_exact_input_record_was_not_saved(monkeypatch):
    import sys
    import pathlib
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
    from wxpredict import common, live_reader as lr, wxrd_live as wl
    from wxpredict import build_table, capture_history, decision_inputs
    monkeypatch.setenv("PREDICTIVE_PRIMARY", pp.POLICY)
    now = dt.datetime(2026, 10, 17, 9, 36, tzinfo=UTC)
    event = dict(event_id="m", city="jeddah", date="2026-10-17", unit="C", band_ids=["a", "b"])
    vector = dict(event_id="m", city_key="jeddah", date="2026-10-17", decision_utc=int(now.timestamp()),
                  rmax_c=35, rmax_unit=35, obs_age_min=15, fc_models_n=7, fch_n_known=24,
                  winner="must be removed", label_unit=36)
    monkeypatch.setattr(wl, "load_model", lambda *a: object())
    monkeypatch.setattr(lr, "live_events", lambda *a: [event])
    monkeypatch.setattr(lr, "row_at", lambda *a, **k: vector)
    monkeypatch.setattr(capture_history, "read", lambda *a, **k: [])
    monkeypatch.setattr(build_table, "load_station_daily", lambda: ({}, {}))
    monkeypatch.setattr(build_table, "load_daily_forecasts", lambda: ({}, {}))
    monkeypatch.setattr(decision_inputs, "history_identity", lambda: ("old-commit", int(now.timestamp())-100))
    monkeypatch.setitem(sys.modules, "sdyes_forward", SimpleNamespace(
        model_probs=lambda *a: {("m", int(now.timestamp())): [.2, .8]}))
    writes = []
    def refuse(*a):
        raise RuntimeError("input record refused")
    fake = SimpleNamespace(rest_all=lambda *a, **k: [], rpc=refuse)
    monkeypatch.setattr(common, "ops", lambda: fake)
    markets = [dict(market_id="m", city_key="jeddah", resolution_date="2026-10-17")]
    cities = [dict(city_key="jeddah", timezone="Asia/Riyadh", icao="OEJN")]
    r = pp.prepare(markets, {"m": []}, cities, now)
    assert r["selected"] == 0 and "input record refused" in r["error"] and not pp._predictions
    fake.rpc = lambda name, body: writes.append(body) or 1
    r = pp.prepare(markets, {"m": []}, cities, now)
    assert r["selected"] == 1 and len(writes) == 1
    payload = writes[0]["p_rows"][0]["payload"]
    assert payload["row"]["winner"] is None and payload["row"]["label_unit"] is None
    assert payload["history_committed_at"] < now.timestamp()
    assert payload["row_sha256"] == next(iter(pp._predictions.values()))["input_sha256"]
    assert next(iter(pp._predictions.values()))["floor_unit"] == 35


def test_served_call_freezes_raw_baseline_calibration_and_exact_lineage(monkeypatch):
    monkeypatch.setenv('PREDICTIVE_PRIMARY',pp.POLICY)
    monkeypatch.setattr('common.model_version_id',lambda kind,*a,**k: kind+'-id')
    monkeypatch.setattr(pp,'_lineage',{})
    key=('city','2026-10-17',frozenset(['a','b']))
    state=dict(version='validated-fit',params=dict(temperature=1.05,tilt=.025))
    monkeypatch.setattr(pp,'_predictions',{key:dict(version='wxrd3:v3:checked',as_of='2026-10-17T12:36:00Z',
        probs={'a':.7,'b':.3},raw_probs={'a':.8,'b':.2},calibration=state,
        floor_c=20,unit='C',floor_unit=20,input_sha256='digest',input_key='packet',predictive_context={'band_ids':['a','b']})})
    baseline=([dict(band_id='a',calibrated_prob=.6),dict(band_id='b',calibrated_prob=.4)],None,['priced_from:baseline'])
    rows,_,_=pp.apply('city','2026-10-17',baseline)
    assert rows[0]['raw_prob']==.8 and rows[0]['calibrated_prob']==.7 and rows[0]['calibration_version']=='calibration-id'
    saved=pp.checkpoint_lineage('city','2026-10-17',{'a':.7,'b':.3})
    assert saved['raw_probs']=={'a':.8,'b':.2} and saved['baseline_probs']=={'a':.6,'b':.4} and saved['calibration']==state
    assert not pp.checkpoint_lineage('city','2026-10-17',{'a':.6,'b':.4})
