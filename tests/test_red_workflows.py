"""Two workflows went red every day for reasons that were not failures.

Both cost their full runtime first, which is the expensive kind of wrong: the
work completed and the job still reported failure, so a genuine outage would
have gone unnoticed among the noise.

  pipeline_intraday   paper_exits called capture_book, the venue returned 404
                      for a token whose market had resolved, nothing caught
                      it, and the last step of an eleven-minute pipeline
                      killed the run. Signals, proposals, fills and the
                      settlement sweep had all already succeeded.

  forecasts           forecast_backfill_job raised the same RuntimeError for
                      a clean budget pause as for a source outage, so a
                      backfill that saved its checkpoint and made real
                      progress exited 1.
"""

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


# --- a resolved market is an answer, not an error ------------------------

def _http(status):
    r = requests.Response()
    r.status_code = status
    return requests.HTTPError(f"{status} Client Error", response=r)


def test_a_delisted_token_is_recorded_not_raised():
    """The venue stops serving a token once its market resolves, and the exit
    cycle reaches those positions constantly - a position is only still open
    because settlement has not closed it yet."""
    import paper_exits as pe

    def boom(order):
        raise _http(404)

    book, reason = pe._book_or_reason(boom, {"token_id": "x"})
    assert book is None
    assert reason == "resolved", (
        "a 404 here means the market is gone and the position belongs to "
        "settlement - it is not a failure of the exit cycle")


def test_a_venue_outage_does_not_cost_the_other_positions_their_turn():
    import paper_exits as pe

    for status in (500, 502, 503):
        book, reason = pe._book_or_reason(lambda o: (_ for _ in ()).throw(_http(status)),
                                          {"token_id": "x"})
        assert book is None and reason.startswith("venue_http_"), reason

    book, reason = pe._book_or_reason(
        lambda o: (_ for _ in ()).throw(requests.ConnectionError()), {"token_id": "x"})
    assert book is None and reason


def test_an_identity_mismatch_is_still_refused():
    """capture_book raises ValueError when a token does not match its market.
    That must be recorded and skipped, never traded against."""
    import paper_exits as pe

    book, reason = pe._book_or_reason(
        lambda o: (_ for _ in ()).throw(ValueError("token_identity_mismatch")),
        {"token_id": "x"})
    assert book is None and reason == "valueerror"


def test_a_good_book_still_comes_straight_back():
    import paper_exits as pe
    book, reason = pe._book_or_reason(lambda o: {"bids": [{"price": ".4"}]}, {"token_id": "x"})
    assert reason is None and book["bids"]


def test_every_exit_from_the_cycle_records_the_run():
    """The budget exit returned without writing to ingest_log, so the desk
    page showed a stale Exits tile - the same bug paper_plans had."""
    import ast
    import inspect
    import textwrap

    import paper_exits as pe

    tree = ast.parse(textwrap.dedent(inspect.getsource(pe.cycle)))
    outer = tree.body[0]
    nested = {id(n) for fn in ast.walk(outer)
              if isinstance(fn, ast.FunctionDef) and fn is not outer
              for n in ast.walk(fn)}
    bad = [ast.unparse(n) for n in ast.walk(outer)
           if isinstance(n, ast.Return) and id(n) not in nested
           and not (isinstance(n.value, ast.Call)
                    and getattr(n.value.func, "id", None) == "done")]
    assert not bad, f"these paths leave cycle() without logging: {bad}"


# --- a pause is not a failure -------------------------------------------

def _run_backfill(result, depth=2, budget=75, monkeypatch=None):
    import forecast_backfill_job as job

    monkeypatch.setenv("BACKFILL_DEPTH", str(depth))
    monkeypatch.setenv("BACKFILL_BUDGET", str(budget))
    monkeypatch.setenv("BACKFILL_AUTO", "false")
    monkeypatch.setattr(job.subprocess, "run", lambda *a, **k: None)

    class FakePath:
        def unlink(self, missing_ok=False): pass
        def read_text(self): 
            import json
            return json.dumps(result)
        def __truediv__(self, other): return self
    monkeypatch.setattr(job, "Path", lambda *a, **k: FakePath())
    return job.main()


def test_a_budget_pause_with_progress_is_not_a_failure(monkeypatch):
    """It ran every day, cost its eighteen minutes, did real work and exited
    1. That is the normal end of a bounded job."""
    assert _run_backfill({"incomplete": True, "completed_dates": 12,
                          "missing_chunks": 0, "rows_offered": 4210},
                         monkeypatch=monkeypatch) is None


def test_a_stop_with_no_progress_still_fails(monkeypatch):
    with pytest.raises(RuntimeError, match="no progress"):
        _run_backfill({"incomplete": True, "completed_dates": 0,
                       "missing_chunks": 0, "rows_offered": 0},
                      monkeypatch=monkeypatch)


def test_source_gaps_still_fail(monkeypatch):
    """A source that did not return data it was asked for is a real gap and
    will not close by retrying blindly."""
    with pytest.raises(RuntimeError, match="missing chunk"):
        _run_backfill({"incomplete": True, "completed_dates": 5,
                       "missing_chunks": 3, "rows_offered": 900},
                      monkeypatch=monkeypatch)


def test_a_complete_backfill_returns_quietly(monkeypatch):
    assert _run_backfill({"incomplete": False, "completed_dates": 30,
                          "missing_chunks": 0, "rows_offered": 9000},
                         monkeypatch=monkeypatch) is None
