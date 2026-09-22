"""A backtest that runs out of time has to say so, not sit there saying `running`.

pipeline_daily failed every night on its "Queued backtests" step:

    The action 'Queued backtests' has timed out after 30 minutes

and left run 5f7bc321 with status='running', started_at 2026-09-22 09:26 and
no finished_at. poll_and_run_queued() selects status='queued' only, so that
row would never be retried and never be visible as a failure - it simply
claimed to be working for ever, while the step burned 30 metered Actions
minutes a day producing nothing.

THREE FAULTS, and all three are needed.

WHY IT WAS SLOW. The book read ran one request per BAND:

    for b in bands:
        rest("book_snapshots", band_id=eq.<one>, order=observed_at.desc, limit 1)

For the queued run - 9 strategies, 30 days, 48 cities - that is about 15,800
sequential HTTPS round-trips, which at ~100ms each is 26 minutes before a
single trade is simulated. It was not careless: it replaced a single read that
pulled EVERY snapshot up to the decision instant, hit PostgREST's 1,000-row
cap, and left the bands that sorted last with no book - one reason a backtest
over 739 city-days once placed zero trades. book_as_of() does
`distinct on (band_id)` in the database, which is complete AND bounded, and
PostgREST cannot express it. One request per ladder instead of eleven.

WHY IT LIED. A process killed by the step timeout cannot update its own row.
So the runner now carries a budget a little under the step's, stops itself,
and records the reason - and any row still `running` long past any plausible
budget is reclaimed by the next run.

WHY IT IS NOT RE-QUEUED AUTOMATICALLY. The run that stalled is the run that
would stall again. A queue that retries the same oversized job every night is
how a 30-minute step becomes a permanent 30-minute step. Reclaiming tells the
truth; re-queueing is a decision with a narrower scope attached.
"""

import datetime as dt
import pathlib
import re

import pytest

from backtest import runner


ROOT = pathlib.Path(__file__).resolve().parents[1]
DAILY = ROOT / ".github/workflows/pipeline_daily.yml"
MIG = (ROOT / "supabase/migrations"
            / "20260922200000_one_request_per_ladder_not_one_per_band.sql")


def _step_timeout_minutes():
    y = DAILY.read_text()
    block = y[y.index("name: Queued backtests"):]
    m = re.search(r"timeout-minutes:\s*(\d+)", block)
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------
# 1. The budget has to be under the axe.
# --------------------------------------------------------------------------

def test_the_step_still_has_a_timeout():
    assert _step_timeout_minutes(), (
        "without a step timeout a stuck backtest holds the whole daily "
        "pipeline until GitHub's job limit"
    )


def test_the_runners_budget_is_under_the_steps():
    step = _step_timeout_minutes()
    assert runner.BUDGET_MINUTES < step, (
        f"budget {runner.BUDGET_MINUTES} >= step {step}: the runner would be "
        "killed before it could record why, which is the whole defect"
    )
    assert step - runner.BUDGET_MINUTES >= 2, (
        "leave room to write the failure row and finish the request"
    )


def test_a_stalled_run_is_older_than_any_real_one():
    assert runner.STALE_AFTER_MINUTES > runner.BUDGET_MINUTES, (
        "reclaiming a run that is merely slow would kill work in progress"
    )


# --------------------------------------------------------------------------
# 2. Reclaiming.
# --------------------------------------------------------------------------

def _fake_rest(rows):
    seen = {}

    def f(table, params=None, **kw):
        seen["params"] = dict(params or [])
        return rows
    return f, seen


def test_a_run_stuck_running_is_marked_failed(monkeypatch):
    patched = []
    rest_fn, seen = _fake_rest([{"run_id": "abc", "started_at": "2026-09-22T09:26:00Z"}])
    monkeypatch.setattr(runner, "rest", rest_fn)
    monkeypatch.setattr(runner, "_patch", lambda t, m, v: patched.append((t, m, v)))

    assert runner.reclaim_stalled() == 1
    table, match, values = patched[0]
    assert table == "backtest_runs" and match == {"run_id": "eq.abc"}
    assert values["status"] == "failed"
    assert values["finished_at"]
    assert "killed" in values["error"], "the row must say what happened to it"


def test_it_asks_only_for_runs_that_have_been_running_a_while(monkeypatch):
    rest_fn, seen = _fake_rest([])
    monkeypatch.setattr(runner, "rest", rest_fn)
    monkeypatch.setattr(runner, "_patch", lambda *a: None)
    runner.reclaim_stalled(stale_after_minutes=90)
    p = seen["params"]
    assert p["status"] == "eq.running"
    assert p["started_at"].startswith("lt."), (
        "without the age filter this reclaims the run that is currently working"
    )
    cutoff = dt.datetime.fromisoformat(p["started_at"][3:])
    age = (dt.datetime.now(dt.timezone.utc) - cutoff).total_seconds() / 60.0
    assert 85 < age < 95


def test_reclaiming_does_not_requeue(monkeypatch):
    patched = []
    rest_fn, _ = _fake_rest([{"run_id": "abc", "started_at": "2026-09-22T09:26:00Z"}])
    monkeypatch.setattr(runner, "rest", rest_fn)
    monkeypatch.setattr(runner, "_patch", lambda t, m, v: patched.append(v))
    runner.reclaim_stalled()
    assert all(v["status"] != "queued" for v in patched), (
        "the run that stalled is the run that would stall again - retrying it "
        "nightly is how a 30-minute step becomes permanent"
    )


# --------------------------------------------------------------------------
# 3. The poll loop.
# --------------------------------------------------------------------------

def test_the_poll_reclaims_before_it_runs_anything(monkeypatch):
    order = []
    monkeypatch.setattr(runner, "reclaim_stalled", lambda *a, **k: order.append("reclaim") or 0)
    monkeypatch.setattr(runner, "rest", lambda *a, **k: [])
    runner.poll_and_run_queued()
    assert order == ["reclaim"], (
        "a stalled row left in place is a row nobody can see is broken"
    )


def test_a_spent_budget_leaves_the_rest_queued(monkeypatch):
    ran = []
    monkeypatch.setattr(runner, "reclaim_stalled", lambda *a, **k: 0)
    monkeypatch.setattr(runner, "rest", lambda *a, **k: [{"run_id": "a"}, {"run_id": "b"}])
    monkeypatch.setattr(runner, "run", lambda rid, deadline=None: ran.append(rid))
    runner.poll_and_run_queued(budget_minutes=0)
    assert ran == [], "with no budget left nothing should start"


def test_the_deadline_reaches_the_run(monkeypatch):
    got = {}
    monkeypatch.setattr(runner, "reclaim_stalled", lambda *a, **k: 0)
    monkeypatch.setattr(runner, "rest", lambda *a, **k: [{"run_id": "a"}])
    monkeypatch.setattr(runner, "run", lambda rid, deadline=None: got.update(d=deadline))
    runner.poll_and_run_queued(budget_minutes=5)
    assert got["d"] is not None, (
        "a run with no deadline is a run that gets killed mid-flight"
    )


def test_one_failing_run_does_not_stop_the_queue(monkeypatch):
    ran = []

    def boom(rid, deadline=None):
        ran.append(rid)
        if rid == "a":
            raise RuntimeError("nope")

    monkeypatch.setattr(runner, "reclaim_stalled", lambda *a, **k: 0)
    monkeypatch.setattr(runner, "rest", lambda *a, **k: [{"run_id": "a"}, {"run_id": "b"}])
    monkeypatch.setattr(runner, "run", boom)
    runner.poll_and_run_queued(budget_minutes=30)
    assert ran == ["a", "b"]


# --------------------------------------------------------------------------
# 4. One request per ladder.
# --------------------------------------------------------------------------

def test_the_book_is_read_for_the_whole_ladder_at_once():
    src = (ROOT / "scripts/backtest/runner.py").read_text()
    assert 'rpc("book_as_of"' in src
    assert '"p_band_ids": [b["band_id"] for b in bands]' in src, (
        "the point is the whole ladder in one call"
    )
    assert '("band_id", f"eq.{b[\'band_id\']}")' not in src, (
        "the per-band loop is what cost 26 minutes before a trade was simulated"
    )


def test_the_run_signature_carries_a_deadline():
    assert "deadline" in runner.run.__code__.co_varnames[:2]


def test_the_function_exists_and_is_granted():
    sql = MIG.read_text()
    assert "create or replace function public.book_as_of(p_band_ids uuid[], p_as_of timestamptz)" in sql
    assert "distinct on (b.band_id)" in sql, (
        "a plain filter hits PostgREST's 1,000-row cap and silently drops the "
        "bands that sort last - which is the bug the per-band loop was fixing"
    )
    assert "returns setof public.book_snapshots" in sql, (
        "a hand-written column list goes stale the next time a depth column "
        "is added, and the simulator reads the whole book"
    )
    assert "grant execute on function public.book_as_of" in sql


def test_the_function_migration_writes_nothing():
    sql = MIG.read_text().lower()
    for verb in ("insert into", "update ", "delete from", "drop table", "alter table"):
        assert verb not in sql, f"{verb} has no business in a read helper"
