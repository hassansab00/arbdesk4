import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import probability_engine as pe
from verify_proprietary_export import canonical_bytes, verify


ROOT = Path(__file__).resolve().parents[1]


def _read(path):
    return (ROOT / path).read_text(encoding="utf-8")


def test_canonical_contract_migration_preserves_source_rows():
    sql = _read("supabase/migrations/20260912230000_phase1_canonical_contracts.sql").lower()
    assert "insert into public.proprietary_data_corrections" in sql
    assert "v_canonical_markets" in sql and "v_canonical_bands" in sql
    for mutation in ("update public.markets", "update public.bands", "delete from public.markets",
                     "delete from public.bands", "truncate public.markets", "truncate public.bands"):
        assert mutation not in sql


def test_probability_engine_prices_lead_zero_with_explicit_conservative_proxy(monkeypatch):
    monkeypatch.setattr(pe, "_forecast_for", lambda *_: {
        "lead_days": 0, "forecast_max_c": 20.0, "model": "test-model",
        "run_at": "2026-09-12T12:00:00+00:00",
    })

    def skill(_city, lead, _model=None):
        if lead == 0:
            return None
        if lead == 1:
            return {"lead_days": 1, "mae_c": 2.0, "bias_c": 99.0, "n_days": 300}
        return None

    monkeypatch.setattr(pe, "_skill_for", skill)
    monkeypatch.setattr(pe.regime, "classify", lambda *_args, **_kwargs: SimpleNamespace(
        confidence=1.0, reasons=[], sigma_multiplier=1.0, label="NORMAL"))
    monkeypatch.setattr(pe, "_divergence_for", lambda *_: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_for", lambda *_: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_map", lambda: None)
    monkeypatch.setattr(pe, "model_version_id", lambda *_args, **_kwargs: None)

    bands = [
        {"band_id": "low", "band_lo": None, "band_hi": 20, "open_low": True, "open_high": False},
        {"band_id": "exact", "band_lo": 20, "band_hi": 21, "open_low": False, "open_high": False},
        {"band_id": "high", "band_lo": 21, "band_hi": None, "open_low": False, "open_high": True},
    ]
    rows, _reg, reasons = pe.process_city_day("london", "2026-09-12", "C", bands, {})
    assert len(rows) == 3
    assert abs(sum(row["calibrated_prob"] for row in rows) - 1.0) < 1e-5
    assert all(row["lead_days"] == 0 and row["skill_lead_days"] == 1 for row in rows)
    assert all(row["skill_proxy"] is True and row["bias_applied_c"] == 0.0 for row in rows)
    assert all(row["input_forecast_run"] == "2026-09-12T12:00:00+00:00" for row in rows)
    assert "skill_proxy:lead0_to_lead1" in reasons


def test_probability_band_scope_is_paginated(monkeypatch):
    calls = []

    def fake_rest_all(path, params, **kwargs):
        calls.append((path, dict(params), kwargs))
        return []

    monkeypatch.setattr(pe, "rest_all", fake_rest_all)
    pe._bands_for_markets([str(i) for i in range(101)])
    assert len(calls) == 2
    assert all(call[0] == "v_canonical_bands" for call in calls)
    assert all(call[2] == {"order": "band_id", "page_size": 500} for call in calls)


def test_unmeasured_city_gets_visible_but_nontradeable_cold_start_probabilities(monkeypatch):
    monkeypatch.setattr(pe, "_forecast_for", lambda *_: {
        "lead_days": 0, "forecast_max_c": 24.0, "model": "test-model",
        "run_at": "2026-09-13T03:00:00+00:00",
    })
    monkeypatch.setattr(pe, "_skill_for", lambda *_: None)
    monkeypatch.setattr(pe, "_global_skill", lambda lead: (
        {"lead_days": lead, "mae_c": 2.5, "bias_c": 0.0,
         "n_days": 0, "global_n_cities": 40} if lead == 1 else None
    ))
    monkeypatch.setattr(pe.regime, "classify", lambda *_args, **_kwargs: SimpleNamespace(
        confidence=1.0, reasons=[], sigma_multiplier=1.0, label="NORMAL"))
    monkeypatch.setattr(pe, "_divergence_for", lambda *_: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_for", lambda *_: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_map", lambda: None)
    monkeypatch.setattr(pe, "model_version_id", lambda *_args, **_kwargs: None)
    bands = [
        {"band_id": "low", "band_lo": None, "band_hi": 24, "open_low": True, "open_high": False},
        {"band_id": "high", "band_lo": 24, "band_hi": None, "open_low": False, "open_high": True},
    ]
    rows, _reg, reasons = pe.process_city_day("new_city", "2026-09-13", "C", bands, {})
    assert len(rows) == 2 and abs(sum(r["calibrated_prob"] for r in rows) - 1) < 1e-5
    assert all(r["skill_source"] == "global_lead_p75" for r in rows)
    assert all(r["pricing_eligible"] is False for r in rows)
    assert all(r["pricing_block_reason"] == "no_city_skill" for r in rows)
    assert any(reason.startswith("global_skill_proxy:") for reason in reasons)


def test_export_route_is_bounded_allowlisted_and_server_only():
    route = _read("web/app/api/proprietary-export/route.ts")
    client = _read("web/components/ProprietaryExport.tsx")
    catalogue = _read("web/lib/proprietaryExport.ts")
    assert "sameOrigin(request)" in route
    assert "MAX_ROWS = 50_000" in route and "MAX_DAYS = 31" in route
    assert "ARBDESK_PRIVATE_EXPORT_ENABLED" in route
    assert "SUPABASE_SERVICE_KEY" in route
    assert "process.env" not in client and "createClient" not in client
    assert "SUPABASE_SERVICE_KEY" not in catalogue and "createClient" not in catalogue
    assert "exportDataset(body.dataset)" in route
    assert "arbdesk_export_manifest" in route and 'digest("hex")' in route


def test_streamed_export_verifier_accepts_valid_file_and_rejects_tamper(tmp_path):
    rows = [
        {"type": "row", "relation": "research_captures", "data": {"capture_id": "a", "value": 1}},
        {"type": "row", "relation": "research_captures", "data": {"capture_id": "b", "value": 2}},
    ]
    digest = hashlib.sha256()
    for row in rows:
        encoded = canonical_bytes(row)
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    records = [
        {"type": "arbdesk_export_header", "version": 1, "dataset": "research_bundle"},
        *rows,
        {"type": "arbdesk_export_manifest", "version": 1, "dataset": "research_bundle",
         "row_count": 2, "expected_rows": 2, "sha256": digest.hexdigest(), "complete": True},
    ]
    path = tmp_path / "export.ndjson"
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in records), encoding="utf-8")
    assert verify(path)["row_count"] == 2

    text = path.read_text(encoding="utf-8").replace('"value":2', '"value":3')
    path.write_text(text, encoding="utf-8")
    try:
        verify(path)
    except ValueError as error:
        assert "SHA-256" in str(error)
    else:
        raise AssertionError("tampered export was accepted")
