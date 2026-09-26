"""US city-days confirmed and scored the same morning (plan v2.2 P4.7)."""
import datetime as dt
import pathlib

import confirm_recent as cr
import paper_settlement as settlement

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_only_the_hours_after_us_days_end():
    for h in (0, 3, 5, 18, 23):
        assert cr.main(now=dt.datetime(2026, 9, 27, h, 36, tzinfo=dt.timezone.utc)) == {}


def test_the_budget_is_cut_from_the_tick_deadline():
    assert cr.budget(None) == cr.BUDGET_S
    assert cr.budget(1000.0, now_epoch=990.0) == 2.0      # 10 left less 8 reserve
    assert cr.budget(1000.0, now_epoch=1000.0) == 0.0
    assert cr.budget(1000.0, now_epoch=900.0) == cr.BUDGET_S


def _wire(monkeypatch, deadline_left):
    calls, logged = {}, []
    import common
    monkeypatch.setattr(common, "rpc", lambda fn, args=None: calls.setdefault("rpc", []).append(fn) or 7)
    monkeypatch.setattr(common, "log_run",
                        lambda job, status, rows, detail: logged.append((job, status, rows, detail)))

    def cycle(budget_seconds, days_back, max_new_evidence):
        calls["cycle"] = (budget_seconds, days_back, max_new_evidence)
        return {"evidence_captured": 11, "candidates": 30, "unreached": 19, "skips": {}, "failed": 0}
    monkeypatch.setattr(settlement, "cycle", cycle)
    now = dt.datetime(2026, 9, 27, 9, 36, tzinfo=dt.timezone.utc)
    return calls, logged, now, now.timestamp() + deadline_left


def test_a_run_sweeps_two_days_and_banks(monkeypatch):
    calls, logged, now, deadline = _wire(monkeypatch, 60)
    d = cr.main(now=now, deadline=deadline)
    assert calls["cycle"][1:] == (2, cr.MAX_EVIDENCE) and 0 < calls["cycle"][0] <= cr.BUDGET_S
    assert calls["rpc"] == ["bank_checkpoint_outcomes"]
    assert d["banked_checkpoints"] == 7 and logged[0][:3] == ("P4.7_confirm_recent", "ok", 18)


def test_no_time_left_skips_rather_than_overrunning_the_tick(monkeypatch):
    calls, logged, now, deadline = _wire(monkeypatch, 9)
    d = cr.main(now=now, deadline=deadline)
    assert "cycle" not in calls and logged[0][1] == "skipped" and d["skipped"]


def test_the_venue_calls_are_short(monkeypatch):
    """paper_worker waits 20 s a call; inside the tick that would bill a second
    minute. confirm_recent swaps in a 5-second call for its sweep."""
    import paper_worker
    calls, logged, now, deadline = _wire(monkeypatch, 60)
    cr.main(now=now, deadline=deadline)
    assert paper_worker.public_json is cr.quick_json and cr.REQUEST_TIMEOUT_S == 5


def test_the_tick_runs_it_before_the_trades_with_a_backstop():
    wf = (ROOT / ".github" / "workflows" / "tick.yml").read_text()
    assert "timeout 20 .venv/bin/python scripts/confirm_recent.py" in wf
    assert wf.index("scripts/confirm_recent.py") < wf.index("scripts/ingest_trades.py")


def test_the_ledger_is_asked_only_about_the_candidates(monkeypatch):
    """It read all 20,062 verdicts per run; now only the candidates', 50 at a
    time, and the bands it drops are exactly the proven ones."""
    asked = []

    def rest_all(path, params=None, **k):
        assert path == "resolution_verdicts"
        ids = params["condition_id"][4:-1].split(",")
        asked.append(len(ids))
        return [{"condition_id": c} for c in ids if c.endswith("1")]
    monkeypatch.setattr(settlement, "rest_all", rest_all)
    conds = {f"0x{i:03d}" for i in range(120)}
    proven = settlement._proven(conds)
    assert asked == [50, 50, 20]
    assert proven == {c for c in conds if c.endswith("1")}
