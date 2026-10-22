import datetime as dt
from pathlib import Path

import pytest
import settlement_followup as followup


def wire(monkeypatch, pending):
    calls, logs = [], []
    def read(table, params, **kwargs):
        assert table == "v_outcome_pipeline"
        assert ("state", "in.(confirmed_not_banked,venue_resolved_proof_incomplete)") in params
        assert ("resolution_date", "gte.2026-10-19") in params
        return pending
    monkeypatch.setattr(followup, "rest_all", read)
    monkeypatch.setattr(followup, "log_run", lambda *args: logs.append(args))
    monkeypatch.setattr(followup, "bank_bands_only", lambda args:
        calls.append(("bank", args)) or {"bands": 11, "checkpoints": 0})
    monkeypatch.setattr(followup, "rpc", lambda *args, **kwargs:
        calls.append(("rpc", args, kwargs)) or {"status": "queued"})
    return calls, logs


def test_no_confirmed_backlog_does_not_bank_or_request_refresh(monkeypatch):
    calls, logs = wire(monkeypatch, [])
    detail = followup.main(dt.date(2026, 10, 22))
    assert calls == [] and detail["refresh_requested"] is False
    assert logs[-1][:3] == (followup.JOB, "ok", 0)


def test_confirmed_ladder_uses_existing_bank_then_requests_refresh(monkeypatch):
    calls, logs = wire(monkeypatch, [{"market_id": "confirmed"}])
    detail = followup.main(dt.date(2026, 10, 22))
    assert [c[0] for c in calls] == ["bank", "rpc"]
    assert vars(calls[0][1]) == {"days": 3, "proof_days": 3, "force": False}
    assert calls[1][1][0] == "request_page_cache_refresh"
    assert detail["refresh_requested"] and detail["refresh_status"] == "queued"
    assert logs[-1][1] == "ok"  # receipt remains explicitly queued


def test_another_writer_banking_first_needs_no_duplicate_refresh(monkeypatch):
    calls, _ = wire(monkeypatch, [{"market_id": "confirmed"}])
    monkeypatch.setattr(followup, "bank_bands_only", lambda args: {"bands": 0})
    assert followup.main(dt.date(2026, 10, 22))["refresh_requested"] is False
    assert calls == []


def test_rejected_refresh_is_a_failure_and_keeps_the_banked_rows(monkeypatch):
    _, logs = wire(monkeypatch, [{"market_id": "confirmed"}])
    monkeypatch.setattr(followup, "rpc", lambda *args, **kwargs: {"status": "error"})
    with pytest.raises(RuntimeError, match="not accepted"):
        followup.main(dt.date(2026, 10, 22))
    assert logs[-1][1] == "error"


def test_hourly_followup_is_after_collected_confirmations_and_never_on_rehearsal():
    import yaml
    root = Path(__file__).resolve().parents[1]
    wf = yaml.safe_load((root / ".github/workflows/tick.yml").read_text())
    steps = wf["jobs"]["tick"]["steps"]
    i = next(i for i, step in enumerate(steps) if step.get("run") ==
             ".venv/bin/python scripts/settlement_followup.py")
    assert i > next(i for i, step in enumerate(steps) if step.get("name") ==
                    "Venue confirmations, collected (P4.7)")
    assert "dry_run != 'true'" in steps[i]["if"]
    assert steps[i]["timeout-minutes"] == 2
