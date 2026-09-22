"""A run bigger than one step's budget has to survive the budget.

MEASURED 2026-09-22: no backtest reached `complete` after 14 September. The
budget was not the cause - what the runner did when it ran out was.

    run() walked every market from the first, held every trade in memory, and
    wrote nothing until the last one. On the deadline it raised, the row went
    `failed`, and the next attempt started again at the first market, over the
    same window, with the same budget.

A job needing 90 minutes could therefore never finish. Not slowly: never.
"Re-queue it with a narrower date range" was a person working around a harness
that could not resume, and the narrowed re-queue (079d4d10) was itself killed
mid-flight and sat `running` from 16:41 with nothing to show for it.

THE CURSOR IS A DATE, and that is what makes recovery exact rather than
approximate. backtest_trades carries resolution_date and not market_id, so a
cursor pointing mid-date could not be cleaned up without guessing which of
that date's cities had already been written. Advancing only at a date boundary
means everything after the cursor is, by construction, from a chunk that did
not finish - deletable and recomputable, never double-counted.

These tests drive run() against stubs and assert what reached the database.
"""

import datetime as dt
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "scripts"))

import regime                                   # noqa: E402
from backtest import runner                     # noqa: E402


PARAMS = {"start_date": "2026-09-01", "end_date": "2026-09-03",
          "starting_budget": 10000.0, "strategies": []}
DATES = ["2026-09-01", "2026-09-02", "2026-09-03"]


class _Desk:
    """Stands in for every read and write run() makes."""

    def __init__(self, progress=None, stored_trades=None):
        self.run_row = {"run_id": "r1", "params": PARAMS, "progress": progress}
        self.patches = []          # values dicts, in order
        self.inserts = []          # (table, rows)
        self.deletes = []          # (table, match)
        self.simulated = []        # dates handed to _simulate_date
        self.stored_trades = list(stored_trades or [])

    # -- reads ------------------------------------------------------------
    def rest(self, table, params=None, **kw):
        if table == "backtest_runs":
            return [self.run_row]
        if table == "cities":
            return [{"city_key": "alpha", "unit": "C", "icao": "AAAA",
                     "timezone": "UTC"}]
        if table == "v_canonical_markets":
            self.market_params = dict(params or [])
            return [{"market_id": f"m{i}", "city_key": "alpha",
                     "resolution_date": d, "unit": "C"}
                    for i, d in enumerate(DATES)]
        return []

    def rest_all(self, table, params=None, order=None, page_size=None):
        if table == "backtest_trades":
            return list(self.stored_trades)
        return []

    # -- writes -----------------------------------------------------------
    def insert(self, table, rows, chunk=500):
        self.inserts.append((table, list(rows)))
        return len(rows)

    def patch(self, table, match, values):
        self.patches.append(values)

    def delete(self, table, match):
        self.deletes.append((table, dict(match)))

    # -- the simulation itself -------------------------------------------
    def simulate_date(self, markets, *a, **kw):
        day = markets[0]["resolution_date"]
        self.simulated.append(day)
        return ([{"strategy_id": "s1", "city_key": "alpha", "resolution_date": day,
                  "net_pnl": 1.0, "gross_pnl": 1.0, "fee_paid": 0.0}],
                [{"strategy_id": "s1", "action": "ENTER"}],
                False)


def _wire(monkeypatch, desk, simulate=True):
    monkeypatch.setattr(runner, "rest", desk.rest)
    monkeypatch.setattr(runner, "rest_all", desk.rest_all)
    monkeypatch.setattr(runner, "insert", desk.insert)
    monkeypatch.setattr(runner, "_patch", desk.patch)
    monkeypatch.setattr(runner, "_delete", desk.delete)
    if simulate:
        monkeypatch.setattr(runner, "_simulate_date", desk.simulate_date)
    return desk


def _status(desk):
    return [p["status"] for p in desk.patches if "status" in p]


def _progress(desk):
    return [p["progress"] for p in desk.patches if "progress" in p]


# --------------------------------------------------------------------------
# Running out of time
# --------------------------------------------------------------------------
def test_out_of_time_goes_back_to_queued_rather_than_failed(monkeypatch):
    desk = _wire(monkeypatch, _Desk())
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    runner.run("r1", deadline=past)

    assert desk.simulated == [], "the deadline was already gone"
    assert _status(desk)[-1] == "queued", (
        "`failed` throws away banked work and tells the operator to go and "
        "fix something that is not broken")
    assert not any(t == "backtest_results" for t, _ in desk.inserts), (
        "a paused run must not publish metrics for a window it has not walked")


def test_the_pause_says_where_it_got_to_and_why(monkeypatch):
    desk = _wire(monkeypatch, _Desk())
    past = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=1)
    runner.run("r1", deadline=past)
    note = [p for p in desk.patches if "error" in p][-1]["error"]
    assert "out of time" in note and "continues from there" in note


def test_a_paused_run_banks_every_date_it_did_finish(monkeypatch):
    """Two dates fit, the third does not: the two are kept, the third is not."""
    desk = _Desk()
    _wire(monkeypatch, desk, simulate=False)
    calls = {"n": 0}

    def simulate(markets, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            return [], [], True          # the budget ran out inside date three
        return desk.simulate_date(markets, *a, **kw)

    monkeypatch.setattr(runner, "_simulate_date", simulate)
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))

    assert _status(desk)[-1] == "queued"
    written = [r["resolution_date"] for t, rows in desk.inserts
               if t == "backtest_trades" for r in rows]
    assert written == DATES[:2], "exactly the two finished dates are banked"
    assert _progress(desk)[-1]["completed_through"] == DATES[1], (
        "the cursor stops at the last whole date, so tomorrow starts at the third")


def test_a_completed_run_writes_results_once_and_says_complete(monkeypatch):
    trades = [{"strategy_id": "s1", "city_key": "alpha", "resolution_date": d,
               "net_pnl": 1.0, "gross_pnl": 1.0, "fee_paid": 0.0} for d in DATES]
    desk = _wire(monkeypatch, _Desk(stored_trades=trades))
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))

    assert desk.simulated == DATES, "every date in the window"
    assert _status(desk)[-1] == "complete"
    results = [rows for t, rows in desk.inserts if t == "backtest_results"]
    assert len(results) == 1
    scopes = {r["scope"] for r in results[0]}
    assert {"headline", "strategy", "city", "equity_curve"} <= scopes


def test_the_cursor_advances_one_date_at_a_time(monkeypatch):
    desk = _wire(monkeypatch, _Desk())
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    cursors = [p["completed_through"] for p in _progress(desk)]
    assert cursors == DATES, (
        "a cursor that jumps to the end cannot describe a partial run, and one "
        "that moves mid-date cannot be cleaned up")


# --------------------------------------------------------------------------
# Resuming
# --------------------------------------------------------------------------
def test_a_resume_starts_after_the_cursor(monkeypatch):
    desk = _wire(monkeypatch, _Desk(progress={"completed_through": "2026-09-01"}))
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    assert desk.simulated == ["2026-09-02", "2026-09-03"], (
        "re-walking a banked date double-counts its trades")


def test_a_resume_clears_whatever_the_killed_chunk_wrote(monkeypatch):
    desk = _wire(monkeypatch, _Desk(progress={"completed_through": "2026-09-01"}))
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    trade_deletes = [m for t, m in desk.deletes if t == "backtest_trades"]
    assert trade_deletes, "a chunk killed mid-write leaves rows past the cursor"
    assert trade_deletes[0] == {"run_id": "eq.r1", "resolution_date": "gt.2026-09-01"}


def test_a_resume_carries_the_portfolio_and_the_signal_counts(monkeypatch):
    desk = _wire(monkeypatch, _Desk(progress={
        "completed_through": "2026-09-01", "bankroll": 12345.0,
        "realized_pnl": 2345.0, "signal_counts": {"s1": {"fired": 7, "enter": 7, "exit": 0}}}))
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    final = _progress(desk)[-1]
    assert final["bankroll"] == 12345.0, "a fresh bankroll would restate the run"
    assert final["realized_pnl"] == 2345.0
    assert final["signal_counts"]["s1"]["fired"] == 9, (
        "7 banked plus one per remaining date - counts must accumulate, not reset")


def test_the_delete_is_always_scoped_to_this_run(monkeypatch):
    desk = _wire(monkeypatch, _Desk(progress={"completed_through": "2026-09-01"}))
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    for table, match in desk.deletes:
        assert match.get("run_id") == "eq.r1", (
            f"{table} delete without a run_id would destroy another saved run")


def test_the_metrics_are_computed_from_what_was_stored(monkeypatch):
    """No single process holds a resumed run's trades, so memory cannot score it."""
    stored = [{"strategy_id": "s1", "city_key": "alpha", "resolution_date": d,
               "net_pnl": 5.0, "gross_pnl": 5.0, "fee_paid": 0.0} for d in DATES] * 2
    desk = _wire(monkeypatch, _Desk(stored_trades=stored))
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    results = [rows for t, rows in desk.inserts if t == "backtest_results"][0]
    headline = next(r["data"] for r in results if r["scope"] == "headline")
    assert headline["n_trades"] == len(stored), (
        "scored from the six stored rows, not the three this process simulated")


# --------------------------------------------------------------------------
# The date walk itself
# --------------------------------------------------------------------------
def test_the_market_walk_asks_for_a_fixed_order(monkeypatch):
    desk = _wire(monkeypatch, _Desk())
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    order = desk.market_params.get("order", "")
    assert order.startswith("resolution_date.asc"), (
        "without an order the same window can walk markets differently on two "
        "nights, and a cursor then means two different things")
    assert "market_id" in order, "the order must be total, or ties float"


def test_a_date_abandoned_mid_way_writes_nothing(monkeypatch):
    """out_of_time discards the partial date rather than banking half of it."""
    desk = _Desk()
    _wire(monkeypatch, desk, simulate=False)
    monkeypatch.setattr(runner, "_simulate_date",
                        lambda markets, *a, **kw: ([{"net_pnl": 1.0}], [], True))
    runner.run("r1", deadline=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=2))
    assert not any(t == "backtest_trades" for t, _ in desk.inserts), (
        "half a date banked under a cursor that never moved is a double count "
        "on the next run")
    assert _status(desk)[-1] == "queued"


# --------------------------------------------------------------------------
# Reclaiming, now that a retry can continue
# --------------------------------------------------------------------------
def _reclaim(monkeypatch, row):
    patched = []
    monkeypatch.setattr(runner, "rest", lambda *a, **k: [row])
    monkeypatch.setattr(runner, "_patch", lambda t, m, v: patched.append(v))
    runner.reclaim_stalled()
    return patched[0]


def test_a_killed_run_that_banked_dates_is_requeued(monkeypatch):
    v = _reclaim(monkeypatch, {"run_id": "abc", "started_at": "2026-09-22T09:26:00Z",
                               "progress": {"completed_through": "2026-09-10"}})
    assert v["status"] == "queued", (
        "with a cursor, a retry continues - refusing to retry is how a long "
        "window never finishes")
    assert v["progress"]["reclaimed_at_cursor"] == "2026-09-10"


def test_a_killed_run_that_banked_nothing_is_failed(monkeypatch):
    v = _reclaim(monkeypatch, {"run_id": "abc", "started_at": "2026-09-22T09:26:00Z",
                               "progress": {}})
    assert v["status"] == "failed"
    assert "no complete date at all" in v["error"]


def test_a_run_that_stalls_twice_at_the_same_date_is_failed(monkeypatch):
    v = _reclaim(monkeypatch, {"run_id": "abc", "started_at": "2026-09-22T09:26:00Z",
                               "progress": {"completed_through": "2026-09-10",
                                            "reclaimed_at_cursor": "2026-09-10"}})
    assert v["status"] == "failed", (
        "two kills at the same date is a date that cannot be simulated, and "
        "retrying it nightly is the failure the old never-requeue rule existed "
        "to prevent")
    assert "two attempts" in v["error"]


# --------------------------------------------------------------------------
# The duplicate forecast read
# --------------------------------------------------------------------------
def test_classify_uses_the_rows_it_is_given(monkeypatch):
    """The runner reads this city-day's forecasts; classify re-read them.

    Two requests for one answer, and two answers a write landing between them
    could make disagree.
    """
    calls = []
    monkeypatch.setattr(regime, "_forecasts_for_date",
                        lambda *a, **k: calls.append(a) or [])
    rows = [{"for_date": "2026-09-01", "lead_days": 1, "forecast_max_c": 20.0,
             "model": "nws", "run_at": "2026-08-31T00:00:00+00:00"}]
    regime.classify("alpha", "2026-09-01", {}, as_of=None, forecast_rows=rows)
    assert calls == [], "it fetched again although it was handed the rows"
