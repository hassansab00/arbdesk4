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

    def cycle(budget_seconds, days_back, max_new_evidence, unconfirmed_only=False):
        calls["cycle"] = (budget_seconds, days_back, max_new_evidence)
        calls["unconfirmed_only"] = unconfirmed_only
        return {"evidence_captured": 11, "candidates": 30, "unreached": 19, "skips": {}, "failed": 0}
    monkeypatch.setattr(settlement, "cycle", cycle)
    now = dt.datetime(2026, 9, 27, 9, 36, tzinfo=dt.timezone.utc)
    return calls, logged, now, now.timestamp() + deadline_left


def test_a_run_sweeps_two_days_and_banks(monkeypatch):
    calls, logged, now, deadline = _wire(monkeypatch, 60)
    d = cr.main(now=now, deadline=deadline)
    assert calls["cycle"][1:] == (2, cr.MAX_EVIDENCE) and 0 < calls["cycle"][0] <= cr.BUDGET_S
    assert calls["unconfirmed_only"] is True
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


def test_the_tick_runs_it_with_a_backstop_and_the_trades_do_not_wait_for_it():
    """confirm_recent keeps its 20 s backstop. The trade prints no longer run
    after it on what is left (27-28 Sep: under 5 s in 15 of 27 ticks); they start in
    the background before the checkpoints and are collected in the last step."""
    wf = (ROOT / ".github" / "workflows" / "tick.yml").read_text()
    assert "timeout 20 .venv/bin/python scripts/confirm_recent.py" in wf
    run = lambda script: wf.index(f".venv/bin/python scripts/{script}")
    assert run("ingest_trades.py") < run("tick.py") < run("confirm_recent.py")
    assert wf.rindex("trades.rc") > run("confirm_recent.py")


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


def test_the_tick_reads_only_markets_not_yet_confirmed(monkeypatch):
    """27 Sep 07:36Z and 08:36Z: ~30 reads over all 1,056 bands of two days
    spent the 12 s budget before the venue was asked anything (candidates
    177, unreached 177). The tick asks about unconfirmed markets alone; the
    daily sweep keeps every market."""
    seen = []

    def rest_all(path, params=None, **k):
        seen.append((path, list(params.items()) if isinstance(params, dict) else list(params)))
        return []
    monkeypatch.setattr(settlement, "rest_all", rest_all)
    settlement._candidate_bands([], days_back=2, unconfirmed_only=True)
    settlement._candidate_bands([], days_back=2)
    tick, daily = [p for path, p in seen if path == "markets"]
    assert ("resolution_verified_at", "is.null") in tick
    assert not any(k == "resolution_verified_at" for k, _ in daily)


def test_the_sweep_says_how_long_it_read_before_asking(monkeypatch):
    import paper_worker
    monkeypatch.setattr(settlement, "rest_all", lambda path, params=None, **kw: [])
    monkeypatch.setattr(settlement, "_candidate_bands", lambda *a, **kw: [])
    monkeypatch.setattr(settlement, "log_run", lambda *a: None)
    monkeypatch.setattr(paper_worker, "public_json", lambda url, params: [])
    d = settlement.cycle(budget_seconds=5, days_back=2, unconfirmed_only=True)
    assert d["scope"] == "unconfirmed" and d["prep_s"] >= 0
