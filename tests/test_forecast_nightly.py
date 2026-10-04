"""The night's forecast ingest keeps its second pass inside pipeline_daily (4 Oct).

forecasts.yml left the clock (plan v2 P6.1, 20261004170000). Its chain's second
link wrote 150-400 archive rows a night for cities whose first pass timed out;
scripts/forecast_nightly.py keeps that continuation inside the daily step,
bounded as the chain was, without fetching all 48 current runs again.
"""
import importlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import forecast_nightly as fn


def _result(**kw):
    r = {"incomplete": False, "rows_offered": 0, "missing_chunks": 0, "unreached_chunks": 0,
         "completed_dates": 0, "current_cities_missing": []}
    r.update(kw)
    return r


class Runs:
    """A fake subprocess.run: each pass writes the next scripted result."""

    def __init__(self, results, codes=None):
        self.results, self.codes, self.envs = list(results), list(codes or []), []

    def __call__(self, args, env=None, timeout=None):
        assert args[1:] == ["scripts/ingest_forecasts.py"]
        self.envs.append(dict(env, _timeout=timeout))
        code = self.codes.pop(0) if self.codes else 0
        if code == "timeout":
            raise subprocess.TimeoutExpired(args, timeout)
        if self.results:
            with open(env["FORECAST_RESULT_PATH"], "w") as f:
                json.dump(self.results.pop(0), f)
        return SimpleNamespace(returncode=code)


@pytest.fixture(autouse=True)
def temp(monkeypatch, tmp_path):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))


def test_a_timed_out_city_is_asked_again_with_only_the_missing_current_runs():
    runs = Runs([_result(incomplete=True, unreached_chunks=3, completed_dates=45,
                         current_cities_missing=["busan", "houston"]),
                 _result(completed_dates=3)])
    assert fn.main(run=runs) == 0
    first, second = runs.envs
    assert "FORECAST_CURRENT_CITIES" not in first, "the first pass fetches every city's current run"
    assert first["FORECAST_DEADLINE_MINUTES"] == "20" and first["_timeout"] == 23 * 60
    assert second["FORECAST_CURRENT_CITIES"] == "busan,houston"
    assert second["FORECAST_DEADLINE_MINUTES"] == "6" and second["_timeout"] == 10 * 60


def test_no_current_run_is_fetched_again_when_none_was_missed():
    runs = Runs([_result(incomplete=True, unreached_chunks=1, completed_dates=47), _result(completed_dates=1)])
    fn.main(run=runs)
    assert runs.envs[1]["FORECAST_CURRENT_CITIES"] == ""


def test_a_complete_first_pass_is_the_only_pass():
    runs = Runs([_result(completed_dates=48, current_cities_missing=["busan"])])
    assert fn.main(run=runs) == 0 and len(runs.envs) == 1


@pytest.mark.parametrize("result", [
    _result(incomplete=True, missing_chunks=1, completed_dates=40),   # refused: asking again won't close it
    _result(incomplete=True, unreached_chunks=2, completed_dates=0),  # no progress
])
def test_it_stops_and_goes_red_where_the_chain_did(result):
    """Codex on #302: forecast_backfill_job.py raised for a refusal and for no
    progress, so the night's run went red; the daily step must too."""
    runs = Runs([result, _result()])
    assert fn.main(run=runs) == 1
    assert len(runs.envs) == 1


def test_a_continuation_that_makes_no_progress_goes_red():
    runs = Runs([_result(incomplete=True, unreached_chunks=3, completed_dates=40),
                 _result(incomplete=True, unreached_chunks=3, completed_dates=0)])
    assert fn.main(run=runs) == 1 and len(runs.envs) == 2


def test_a_refusal_in_a_later_pass_goes_red():
    runs = Runs([_result(incomplete=True, unreached_chunks=3, completed_dates=40),
                 _result(incomplete=True, missing_chunks=1, completed_dates=2)])
    assert fn.main(run=runs) == 1


def test_at_most_three_passes_and_a_pause_with_progress_is_not_a_failure():
    stuck = _result(incomplete=True, unreached_chunks=1, completed_dates=1)
    runs = Runs([stuck] * 5)
    assert fn.main(run=runs) == 0
    assert len(runs.envs) == fn.MAX_PASSES == 3


def test_no_pass_starts_after_26_minutes():
    ticks = iter([0, 27 * 60, 27 * 60])
    runs = Runs([_result(incomplete=True, unreached_chunks=1, completed_dates=40), _result()])
    fn.main(run=runs, clock=lambda: next(ticks))
    assert len(runs.envs) == 1


def test_a_continuation_that_crashes_or_times_out_goes_red():
    for code in ("timeout", 1):
        runs = Runs([_result(incomplete=True, unreached_chunks=1, completed_dates=40)], codes=[0, code])
        assert fn.main(run=runs) == 1 and len(runs.envs) == 2


def test_a_failed_first_pass_fails_the_step():
    assert fn.main(run=Runs([], codes=[1])) == 1
    assert fn.main(run=Runs([], codes=["timeout"])) == 1


def test_should_continue_is_the_backfill_job_s_rule():
    assert fn.should_continue(_result(incomplete=True, unreached_chunks=1, completed_dates=1))
    assert not fn.should_continue(_result(incomplete=False, completed_dates=10))
    assert not fn.should_continue(_result(incomplete=True, missing_chunks=1, completed_dates=10))
    assert not fn.should_continue(_result(incomplete=True, unreached_chunks=1, completed_dates=0))


def test_the_ingest_reads_the_cities_whose_current_run_to_fetch(monkeypatch):
    import ingest_forecasts
    try:
        monkeypatch.delenv("FORECAST_CURRENT_CITIES", raising=False)
        assert importlib.reload(ingest_forecasts).CURRENT_CITIES is None
        monkeypatch.setenv("FORECAST_CURRENT_CITIES", "")
        assert importlib.reload(ingest_forecasts).CURRENT_CITIES == set()
        monkeypatch.setenv("FORECAST_CURRENT_CITIES", "busan,houston")
        assert importlib.reload(ingest_forecasts).CURRENT_CITIES == {"busan", "houston"}
    finally:
        monkeypatch.delenv("FORECAST_CURRENT_CITIES", raising=False)
        importlib.reload(ingest_forecasts)
    src = open(ingest_forecasts.__file__).read()
    assert 'todo = [c for c in all_cities if CURRENT_CITIES is None or c["city_key"] in CURRENT_CITIES]' in src
    assert "'current_cities_missing': sorted(set(current_missing))}" in src
