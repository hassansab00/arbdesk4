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

AND THE FORECAST HALF WAS ONLY HALF FIXED. Separating a pause from a failure
left a second conflation underneath it: `fetch` returned None both when the
source answered with a 400 and when the request never completed, and the
caller counted both as a "missing chunk". So the job still went red every day
- 16 to 20 Sep - and still said the gap "will not close by retrying blindly",
of two read timeouts, while 47 of 49 cities took all 77 rows from the same
host in the same window.

  REFUSED    the source answered and declined. Asking again gets the same
             400. A real gap, and it still fails the job.
  UNREACHED  the request never completed - timeout, reset, 5xx. Nothing was
             learned about the data. The chunk stays uncovered, the next run
             asks for it first, and a source that is genuinely down still
             fails on zero dates completed.
"""

import sys
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import forecast_backfill_job as job  # noqa: E402
import ingest_forecasts as ingest  # noqa: E402


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


def test_source_refusals_still_fail(monkeypatch):
    """A source that answered and declined a window is a real gap and will not
    close by retrying blindly. missing_chunks now means refusals only, and
    nothing below softens what they cost."""
    with pytest.raises(RuntimeError, match="refused chunk"):
        _run_backfill({"incomplete": True, "completed_dates": 5,
                       "missing_chunks": 3, "rows_offered": 900},
                      monkeypatch=monkeypatch)


def test_a_complete_backfill_returns_quietly(monkeypatch):
    assert _run_backfill({"incomplete": False, "completed_dates": 30,
                          "missing_chunks": 0, "rows_offered": 9000},
                         monkeypatch=monkeypatch) is None


# --- a timeout is not a gap ---------------------------------------------

class _Response:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _fetch(monkeypatch, responder):
    import datetime as dt

    monkeypatch.setattr(ingest.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ingest.requests, "get", responder)
    day = dt.date(2026, 9, 10)
    return ingest.fetch(1.0, 2.0, day, day, "seattle 2026-09-10")


def test_a_read_timeout_is_unreached_not_refused(monkeypatch):
    """The literal 20 Sep failure: seattle and toronto, same host, same window."""
    def timeout(*_a, **_kw):
        raise ingest.requests.exceptions.ReadTimeout(
            "HTTPSConnectionPool(host='previous-runs-api.open-meteo.com', port=443): Read timed out."
        )

    js, outcome = _fetch(monkeypatch, timeout)
    assert js is None
    assert outcome == "unreached"


def test_a_four_hundred_is_refused(monkeypatch):
    """The source answered and declined. Asking again gets the same answer."""
    js, outcome = _fetch(
        monkeypatch, lambda *_a, **_kw: _Response(400, text="data not available for this date")
    )
    assert js is None
    assert outcome == "refused"


def test_a_server_error_is_unreached(monkeypatch):
    """A 502 says nothing about whether the data exists."""
    js, outcome = _fetch(monkeypatch, lambda *_a, **_kw: _Response(502))
    assert (js, outcome) == (None, "unreached")


def test_a_good_answer_is_ok(monkeypatch):
    js, outcome = _fetch(monkeypatch, lambda *_a, **_kw: _Response(200, {"hourly": {"time": []}}))
    assert outcome == "ok"
    assert js == {"hourly": {"time": []}}


def test_one_retry_happens_before_giving_up(monkeypatch):
    """TRIES attempts, not one. A single blip must not be recorded at all."""
    calls = {"n": 0}

    def flaky(*_a, **_kw):
        calls["n"] += 1
        if calls["n"] < ingest.TRIES:
            raise ingest.requests.exceptions.ReadTimeout("blip")
        return _Response(200, {"hourly": {"time": ["2026-09-10T00:00"]}})

    js, outcome = _fetch(monkeypatch, flaky)
    assert outcome == "ok"
    assert calls["n"] == ingest.TRIES


# --------------------------------------------------------------------------
# What the wrapper does with those counts.
# --------------------------------------------------------------------------

def _run_wrapper(monkeypatch, tmp_path, result, depth=0, budget=75, auto="true",
                 dispatched=None):
    import json

    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("BACKFILL_DEPTH", str(depth))
    monkeypatch.setenv("BACKFILL_BUDGET", str(budget))
    monkeypatch.setenv("BACKFILL_AUTO", auto)
    monkeypatch.setenv("BACKFILL_START", "")
    monkeypatch.setenv("BACKFILL_END", "")
    monkeypatch.setenv("GITHUB_REPOSITORY", "hassansab00/arbdesk4")
    monkeypatch.setenv("GITHUB_REF_NAME", "main")
    dispatched = [] if dispatched is None else dispatched

    def fake_run(args, env=None, **kw):
        if args[:2] == ["gh", "workflow"]:
            dispatched.append(args)
            return None
        # Stand in for scripts/ingest_forecasts.py: the child is what writes
        # the result file, at the path the wrapper hands it.
        Path(env["FORECAST_RESULT_PATH"]).write_text(json.dumps(result))
        return None

    monkeypatch.setattr(job.subprocess, "run", fake_run)
    job.main()
    return dispatched


def test_unreached_chunks_with_progress_do_not_fail_the_job(monkeypatch, tmp_path, capsys):
    """The 20 Sep run exactly: 53 dates completed, 2 chunks unreached, deadline
    reached. That is a bounded job ending normally."""
    _run_wrapper(monkeypatch, tmp_path, {
        "incomplete": True, "rows_offered": 3157, "missing_chunks": 0,
        "unreached_chunks": 2, "completed_dates": 53,
    }, depth=2)
    out = capsys.readouterr().out
    assert "Paused with progress" in out
    assert "2 chunk(s) unreached" in out, "the backlog must stay visible, not just unfatal"


def test_a_refusal_still_fails_the_job(monkeypatch, tmp_path):
    """A source that declined a window will decline it again. That is worth a
    red run, and nothing here softens it."""
    with pytest.raises(RuntimeError, match="refused chunk"):
        _run_wrapper(monkeypatch, tmp_path, {
            "incomplete": True, "rows_offered": 100, "missing_chunks": 2,
            "unreached_chunks": 0, "completed_dates": 53,
        }, depth=2)


def test_a_source_that_is_actually_down_still_fails(monkeypatch, tmp_path):
    """Every chunk unreached and nothing completed. This is the measurement
    that catches a real outage, and it is why unreached need not raise."""
    with pytest.raises(RuntimeError, match="no progress"):
        _run_wrapper(monkeypatch, tmp_path, {
            "incomplete": True, "rows_offered": 0, "missing_chunks": 0,
            "unreached_chunks": 49, "completed_dates": 0,
        }, depth=2)


def test_unreached_chunks_do_not_stop_a_budgeted_continuation(monkeypatch, tmp_path):
    """Six cities were still uncovered on 20 Sep with budget left. Two timeouts
    elsewhere are no reason to leave them - the continuation is the retry."""
    dispatched = _run_wrapper(monkeypatch, tmp_path, {
        "incomplete": True, "rows_offered": 3157, "missing_chunks": 0,
        "unreached_chunks": 2, "completed_dates": 53,
    }, depth=0)
    assert dispatched, "no continuation was requested although budget remained"
    assert "forecasts.yml" in dispatched[0]


def test_a_refusal_does_stop_a_continuation(monkeypatch, tmp_path):
    """Spending more job minutes against a source that is declining is the
    waste the chain guard exists to prevent."""
    dispatched = []
    with pytest.raises(RuntimeError, match="refused chunk"):
        _run_wrapper(monkeypatch, tmp_path, {
            "incomplete": True, "rows_offered": 10, "missing_chunks": 1,
            "unreached_chunks": 0, "completed_dates": 5,
        }, depth=0, dispatched=dispatched)
    assert dispatched == [], "budget was spent chaining into a refusing source"


def test_the_ingest_reports_both_counts_separately(monkeypatch, tmp_path):
    """The result file is the wrapper's only input. If the ingest stopped
    emitting unreached_chunks the split would silently collapse."""
    source = Path(ingest.__file__).read_text(encoding="utf-8")
    assert "'unreached_chunks': unreached_chunks" in source
    assert "'missing_chunks': missing_chunks" in source
    assert "misses" not in source, "the pooled counter is gone, not renamed around"
