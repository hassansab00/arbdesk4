"""US city-days confirmed and scored the same morning (plan v2.2 P4.7)."""
import datetime as dt
import pathlib

import confirm_recent as cr
import paper_settlement as settlement

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_it_runs_every_hour_now_that_eligibility_is_local():
    """30 Sep: a Tokyo day ends at 15:00Z and a London one at 23:00Z; the queue
    decides eligibility from each city's own clock, so every tick asks."""
    assert cr.HOURS_UTC == tuple(range(24))


def test_the_budget_is_cut_from_the_tick_deadline():
    assert cr.budget(None) == cr.BUDGET_S
    assert cr.budget(1000.0, now_epoch=990.0) == 2.0      # 10 left less 8 reserve
    assert cr.budget(1000.0, now_epoch=1000.0) == 0.0
    assert cr.budget(1000.0, now_epoch=900.0) == cr.BUDGET_S


def _wire(monkeypatch, deadline_left, sweep=None):
    calls, logged = {}, []
    import importlib
    common = importlib.import_module("common")
    confirm_queue = importlib.import_module("confirm_queue")
    monkeypatch.setattr(common, "rpc", lambda fn, args=None: calls.setdefault("rpc", []).append(fn) or 7)
    monkeypatch.setattr(common, "log_run",
                        lambda job, status, rows, detail: logged.append((job, status, rows, detail)))

    def run(budget_seconds, days_back, now, get, trigger, log):
        calls["run"] = (budget_seconds, days_back, trigger, log)
        calls["get"] = get
        return dict({"evidence_captured": 11, "due": 5, "asked": 4, "completed": 3,
                     "unreached": 0, "failed": 0, "pending_after": 2}, **(sweep or {}))
    monkeypatch.setattr(confirm_queue, "run", run)
    monkeypatch.setattr(settlement, "cycle", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("the tick no longer walks bands")))
    now = dt.datetime(2026, 9, 27, 9, 36, tzinfo=dt.timezone.utc)
    return calls, logged, now, now.timestamp() + deadline_left


def test_a_run_asks_the_queue_and_banks(monkeypatch):
    calls, logged, now, deadline = _wire(monkeypatch, 60)
    d = cr.main(now=now, deadline=deadline)
    budget, days_back, trigger, log = calls["run"]
    assert 0 < budget <= cr.BUDGET_S and days_back == cr.DAYS_BACK == 3
    assert trigger == "tick" and log is False, "the tick logs its own P4.7 row only"
    assert calls["rpc"] == ["bank_checkpoint_outcomes"]
    assert d["banked_checkpoints"] == 7 and logged[0][:3] == ("P4.7_confirm_recent", "ok", 18)
    assert logged[0][3]["pending_after"] == 2


def test_a_run_out_of_budget_with_ladders_due_is_partial(monkeypatch):
    calls, logged, now, deadline = _wire(monkeypatch, 60, sweep={"unreached": 3})
    cr.main(now=now, deadline=deadline)
    assert logged[0][1] == "partial"
    calls, logged, now, deadline = _wire(monkeypatch, 60, sweep={"failed": 1})
    cr.main(now=now, deadline=deadline)
    assert logged[0][1] == "attention"


def test_no_time_left_skips_rather_than_overrunning_the_tick(monkeypatch):
    calls, logged, now, deadline = _wire(monkeypatch, 9)
    d = cr.main(now=now, deadline=deadline)
    assert "run" not in calls and logged[0][1] == "skipped" and d["skipped"]


def test_the_venue_calls_are_short(monkeypatch):
    """paper_worker waits 20 s a call; inside the tick that would bill a second
    minute. confirm_recent hands the queue a 5-second call."""
    calls, logged, now, deadline = _wire(monkeypatch, 60)
    cr.main(now=now, deadline=deadline)
    assert calls["get"] is cr.quick_json and cr.REQUEST_TIMEOUT_S == 5


def test_the_tick_starts_it_beside_the_checkpoints_with_a_backstop():
    """30 Sep: run after the other steps it had 0.0 s at 10:36Z and skipped (10
    ticks in three days). It starts in the background before the checkpoints,
    like the trade prints, with its budget cut from the same deadline, and the
    last steps collect both."""
    wf = (ROOT / ".github" / "workflows" / "tick.yml").read_text()
    assert "timeout 40 .venv/bin/python scripts/confirm_recent.py" in wf
    run = lambda script: wf.index(f".venv/bin/python scripts/{script}")
    assert run("confirm_recent.py") < run("tick.py")
    assert run("ingest_trades.py") < run("tick.py")
    assert wf.index('"$RUNNER_TEMP/confirm.rc"') < run("tick.py")
    assert wf.rindex("confirm.rc") > run("tick.py"), "collected after the checkpoints"
    assert wf.rindex("trades.rc") > run("tick.py")
    import re
    deadline_s = int(re.search(r"date \+%s\) \+ (\d+) \)\)",
                               (ROOT / ".github" / "workflows" / "tick.yml").read_text()).group(1))
    assert cr.BUDGET_S + cr.RESERVE_S < deadline_s, "inside TICK_DEADLINE"


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


def test_the_background_confirmations_never_hold_or_fail_the_job(tmp_path):
    """Under the runner's own shell flags: the start step returns at once, and
    the collecting step waits for the run, prints its log and never fails the
    job (the run's own log row carries its status)."""
    import os
    import stat
    import subprocess
    import time
    import yaml
    steps = yaml.safe_load((ROOT / ".github" / "workflows" / "tick.yml").read_text())["jobs"]["tick"]["steps"]
    start = next(s for s in steps if s.get("id") == "confirm")["run"]
    collect = next(s for s in steps if s.get("name") == "Venue confirmations, collected (P4.7)")["run"]
    fake = tmp_path / ".venv" / "bin" / "python"
    fake.parent.mkdir(parents=True)
    fake.write_text("#!/bin/bash\nsleep 1\necho fake confirmations\nexit 3\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "RUNNER_TEMP": str(tmp_path)}
    t0 = time.monotonic()
    subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", start],
                   cwd=tmp_path, env=env, check=True, timeout=10)
    assert time.monotonic() - t0 < 0.9
    done = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", collect],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and "fake confirmations" in done.stdout
