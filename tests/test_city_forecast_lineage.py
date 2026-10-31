"""Keep city-specific forecast inputs out of another city's version record."""
import datetime as dt
from types import SimpleNamespace

import common
import probability_engine as pe
from test_the_station_correction_prices import ROW, _with


PUBLIC = {"model": "open_meteo_forecast", "run_at": "2026-10-25T01:54:43.451+00:00"}
SHARED = {"version": "station-correction:shared+station-mos:shared",
          "n_sources": 7, "for_date": "2026-10-25"}


def provenance(city, centre, date="2026-10-25"):
    return pe.forecast_provenance(False, None, PUBLIC,
        station_row=dict(SHARED, city_key=city, for_date=date, combined_c=centre))


def test_shared_provider_run_cannot_reuse_another_citys_registered_centre(monkeypatch):
    # Reproduce the live 25 Oct collision: the version config was LA's
    # 25.204C, while Miami's rows carried 27.323C under that same UUID.
    monkeypatch.setattr(common, "_version_ids", {})
    monkeypatch.setattr(common, "rest", lambda *a, **k: [])
    monkeypatch.setattr(common, "_headers", lambda: {})
    monkeypatch.setattr(common, "_cfg", lambda: {"url": "https://example.invalid"})
    registered = []

    def post(url, **kw):
        import json
        registered.append(json.loads(kw["data"])[0])
        return SimpleNamespace(raise_for_status=lambda: None,
                               json=lambda: [{"version_id": str(len(registered))}])

    monkeypatch.setattr(common, "_post", post)
    la, la_cfg = provenance("los_angeles", 25.204)
    miami, miami_cfg = provenance("miami", 27.323)
    first = common.model_version_id("forecast", la, config=la_cfg)
    second = common.model_version_id("forecast", miami, config=miami_cfg)
    assert first != second
    assert [(r["config"]["city_key"], r["config"]["combined_c"]) for r in registered] == [
        ("los_angeles", 25.204), ("miami", 27.323)]
    assert common.model_version_id("forecast", miami, config=miami_cfg) == second
    assert len(registered) == 2


def test_target_date_and_changed_centre_are_part_of_the_input_identity():
    original, config = provenance("miami", 27.323)
    assert original == provenance("miami", 27.323)[0]
    assert original != provenance("miami", 27.323, "2026-10-26")[0]
    assert original != provenance("miami", 27.324)[0]
    assert config["target_date"] == "2026-10-25"
    assert original.startswith("station_correction:")


def test_actual_pricing_preserves_the_combination_without_changing_probabilities(monkeypatch):
    station = dict(ROW, sources={"model_a": 22.1, "model_b": 22.7},
                   p39_c=22.3, mos_c=22.5, mos_calibration={"applies": False},
                   mos_inputs={"models_mean": 22.4})
    original = pe.forecast_provenance
    rows, _, _ = _with(monkeypatch, row=station, lead=1)
    context = pe.forecast_context("london", "2026-09-19", rows)
    assert context["station_combination"]["sources"] == station["sources"]
    assert context["station_combination"]["p39_c"] == 22.3
    assert context["station_combination"]["mos_c"] == 22.5
    assert context["station_combination"]["mos_inputs"] == {"models_mean": 22.4}
    context["station_combination"]["sources"]["model_a"] = -100
    assert station["sources"]["model_a"] == 22.1

    # Replay the same pricing fixture with the old shared-label identity.
    def old_identity(*args, **kwargs):
        result = original(*args, **kwargs)
        label, config = result
        return label.split(":city-day-input:")[0], config

    monkeypatch.setattr(pe, "forecast_provenance", old_identity)
    baseline, _, _ = _with(monkeypatch, row=station, lead=1)
    fields = ("band_id", "raw_prob", "calibrated_prob", "centre_c", "sigma_c")
    assert [[r[k] for k in fields] for r in rows] == [[r[k] for k in fields] for r in baseline]


def test_station_loader_requests_source_inputs_for_the_frozen_audit(monkeypatch):
    seen = []
    stamp = dt.datetime.now(dt.timezone.utc).isoformat()

    def rest(path, params=None, **kwargs):
        if path == "settings":
            return [{"value": {"enabled": True}}]
        if path == "derived_corrected_forecast":
            seen.append(dict(params)["select"])
            return [dict(ROW, computed_at=stamp, inputs_oldest_run_at=stamp,
                         inputs_newest_run_at=stamp, sources={"model_a": 22.4})]
        return []

    monkeypatch.setattr(pe, "rest", rest)
    monkeypatch.setattr(pe, "_station_cache", None)
    monkeypatch.setattr(pe, "_station_width_cfg", None)
    row = pe._station_corrected_for("london", "2026-09-19", 1)
    assert "sources" in seen[0].split(",")
    assert row["sources"] == {"model_a": 22.4}
