"""capacity.py must not report green while its derivations are failing.

The outage these cover ran from 2026-09-06 to 2026-09-13. Two faults, and the
second is only expensive because of the first:

  1. refresh_weather_peak() started failing 42P10 - ad4_50_index_dedupe had
     dropped the unique index its `on conflict (city_key, month)` needs.
  2. capacity.py caught that, printed one line to stderr, and logged the job
     as `ok`. Nothing anywhere said the table had stopped being written.

So derived_weather_peak went seven days stale while live_weather's
minutes_to_peak, v_city_stats, v_trade_timing and strategy s7 read it, and
the daily pipeline passed every morning.
"""
import pathlib
import re

import pytest
import requests

import capacity


class FakeResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


def http_error(status, body):
    return requests.HTTPError(f"HTTP {status}: {body}",
                              response=FakeResponse(status, body))


# --------------------------------------------------------------------------
# absent vs. broken - the whole distinction the job rests on
# --------------------------------------------------------------------------
def test_a_function_the_database_never_had_is_absent():
    body = ('{"code":"PGRST202","message":"Could not find the function '
            'public.refresh_weather_peak without parameters"}')
    assert capacity._absent(http_error(404, body)) is True


@pytest.mark.parametrize("status,body", [
    (500, "canceling statement due to statement timeout"),
    (500, '{"code":"42P10","message":"there is no unique or exclusion '
          'constraint matching the ON CONFLICT specification"}'),
    (403, '{"code":"42501","message":"permission denied"}'),
    (504, "gateway timeout"),
])
def test_a_function_that_exists_and_failed_is_not_absent(status, body):
    assert capacity._absent(http_error(status, body)) is False


def test_a_404_that_is_not_a_missing_function_is_not_absent():
    """404 alone is not proof - only PostgREST's own PGRST202 is."""
    assert capacity._absent(http_error(404, "nginx not found")) is False


def test_a_plain_exception_is_not_absent():
    assert capacity._absent(RuntimeError("boom")) is False


# --------------------------------------------------------------------------
# optional_rpc
# --------------------------------------------------------------------------
def test_a_missing_function_is_a_note_and_does_not_fail_the_job(monkeypatch):
    body = '{"code":"PGRST202","message":"Could not find the function"}'
    monkeypatch.setattr(capacity, "_call_rpc",
                        lambda fn: (_ for _ in ()).throw(http_error(404, body)))
    failures = []
    assert capacity.optional_rpc("refresh_weather_peak", "run ad4_37", failures) is None
    assert failures == [], "a database without the .sql file is not a failure"


def test_the_42p10_that_started_this_fails_the_job(monkeypatch):
    """The exact error refresh_weather_peak returned for seven days."""
    body = ('{"code":"42P10","message":"there is no unique or exclusion '
            'constraint matching the ON CONFLICT specification"}')
    monkeypatch.setattr(capacity, "_call_rpc",
                        lambda fn: (_ for _ in ()).throw(http_error(500, body)))
    failures = []
    assert capacity.optional_rpc("refresh_weather_peak", "run ad4_37", failures) is None
    assert len(failures) == 1
    assert "refresh_weather_peak" in failures[0]
    assert "42P10" in failures[0], "the reason must survive into the failure list"


def test_a_working_function_returns_its_result(monkeypatch):
    monkeypatch.setattr(capacity, "_call_rpc", lambda fn: 609)
    failures = []
    assert capacity.optional_rpc("refresh_weather_peak", "run ad4_37", failures) == 609
    assert failures == []


# --------------------------------------------------------------------------
# refresh_peaks - the per-city split
#
# The whole-archive refresh_weather_peak() sorts 519k rows and takes 5.0-17.4s
# for the same 609 rows. recompute_correlation was cancelled at ~15s, so
# calling it as one statement is a coin flip. Per city the slowest call is
# 229ms, and the output is identical - all 636 rows compared, none changed.
# --------------------------------------------------------------------------
CITIES = [{"city_key": "austin"}, {"city_key": "chicago"}, {"city_key": "miami"}]


@pytest.fixture
def peaks(monkeypatch):
    def go(per_city):
        calls = []

        def fake_rest(path, params=None):
            assert path == "cities"
            return CITIES

        def fake_rpc(fn, params=None, timeout=120):
            calls.append((fn, (params or {}).get("p_city")))
            outcome = per_city(fn, (params or {}).get("p_city"))
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(capacity, "rest", fake_rest)
        monkeypatch.setattr(capacity, "_call_rpc", fake_rpc)
        return calls
    return go


def test_every_city_is_refreshed_and_the_counts_add_up(peaks):
    calls = peaks(lambda fn, city: 12)
    failures = []
    assert capacity.refresh_peaks(failures) == 36
    assert [c[1] for c in calls] == ["austin", "chicago", "miami"]
    assert failures == []


def test_one_city_failing_does_not_abandon_the_others(peaks):
    def per_city(fn, city):
        return http_error(500, "timeout") if city == "chicago" else 12
    calls = peaks(per_city)
    failures = []
    total = capacity.refresh_peaks(failures)
    assert [c[1] for c in calls] == ["austin", "chicago", "miami"], \
        "miami must still be attempted after chicago failed"
    assert total == 24
    assert len(failures) == 1 and "chicago" in failures[0]


def test_a_database_without_ad4_56_falls_back_to_the_whole_archive(peaks):
    absent = http_error(404, '{"code":"PGRST202","message":"Could not find the function"}')

    def per_city(fn, city):
        return 609 if fn == "refresh_weather_peak" else absent
    calls = peaks(per_city)
    failures = []
    assert capacity.refresh_peaks(failures) == 609
    assert calls[-1][0] == "refresh_weather_peak"
    assert failures == [], "an older database is not a failure"


def test_the_first_city_failing_for_a_real_reason_is_not_a_fallback(peaks):
    """PGRST202 means 'no such function'. A 500 means the function broke."""
    calls = peaks(lambda fn, city: http_error(500, '{"code":"42P10"}'))
    failures = []
    assert capacity.refresh_peaks(failures) is None
    assert [c[0] for c in calls] == ["refresh_weather_peak_city"], \
        "must not silently fall back to the slow version on a real error"
    assert len(failures) == 1


def test_no_cities_is_not_an_error(monkeypatch):
    monkeypatch.setattr(capacity, "rest", lambda path, params=None: [])
    failures = []
    assert capacity.refresh_peaks(failures) == 0
    assert failures == []


# --------------------------------------------------------------------------
# main(): the exit code and the logged row
# --------------------------------------------------------------------------
@pytest.fixture
def run_main(monkeypatch):
    """Drive main() with a scripted RPC table; capture what it logs."""
    def go(rpcs, feature_cache=None):
        logged = {}

        def fake_rpc(fn, params=None, timeout=120):
            # main() reaches the peak refresh through refresh_peaks, which
            # calls it once per city. Tests still script it as one entry.
            outcome = rpcs["refresh_weather_peak"] if fn == "refresh_weather_peak_city" \
                else rpcs[fn]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def fake_log_run(job, status, rows, detail):
            logged.update(job=job, status=status, rows=rows, detail=detail)

        def fake_features():
            if isinstance(feature_cache, Exception):
                raise feature_cache
            return feature_cache

        monkeypatch.setattr(capacity, "rest", lambda path, params=None: [{"city_key": "austin"}])
        monkeypatch.setattr(capacity, "_call_rpc", fake_rpc)
        monkeypatch.setattr(capacity, "log_run", fake_log_run)
        monkeypatch.setattr(capacity, "refresh_feature_cache", fake_features)
        return capacity.main(), logged
    return go


ALL_OK = {
    "recompute_capacity": 203,
    "recompute_correlation": 1299,
    "refresh_weather_peak": 609,
    "refresh_city_climate": {"ok": True, "cities": 54},
    "refresh_calibration_adjustment": {"ok": True},
}


def test_a_clean_run_is_ok_and_exits_zero(run_main):
    code, logged = run_main(dict(ALL_OK), feature_cache={"cities": 54})
    assert code == 0
    assert logged["status"] == "ok"
    assert logged["detail"]["failures"] is None


def test_the_peak_count_reaches_ingest_log(run_main):
    """`peaks` was computed and never logged, so the failing table left no trace."""
    _, logged = run_main(dict(ALL_OK), feature_cache={"cities": 54})
    assert logged["detail"]["peaks"] == 609


def test_a_failing_peak_refresh_fails_the_job(run_main):
    rpcs = dict(ALL_OK)
    rpcs["refresh_weather_peak"] = http_error(500, '{"code":"42P10"}')
    code, logged = run_main(rpcs, feature_cache={"cities": 54})
    assert code == 1, "this returned 0 for seven days"
    assert logged["status"] == "attention"
    assert any("refresh_weather_peak" in f for f in logged["detail"]["failures"])


def test_a_failing_climate_refresh_fails_the_job(run_main):
    """derived_city_climate is what City Clusters reads for its forecast."""
    rpcs = dict(ALL_OK)
    rpcs["refresh_city_climate"] = http_error(500, "canceling statement due to statement timeout")
    code, logged = run_main(rpcs, feature_cache={"cities": 54})
    assert code == 1
    assert any("refresh_city_climate" in f for f in logged["detail"]["failures"])


def test_a_database_without_the_optional_files_still_passes(run_main):
    absent = http_error(404, '{"code":"PGRST202","message":"Could not find the function"}')
    rpcs = dict(ALL_OK)
    for fn in ("refresh_weather_peak", "refresh_city_climate",
               "refresh_calibration_adjustment"):
        rpcs[fn] = absent
    code, logged = run_main(rpcs, feature_cache={"cities": 54})
    assert code == 0
    assert logged["status"] == "ok"


def test_several_failures_are_all_reported_not_just_the_first(run_main):
    rpcs = dict(ALL_OK)
    rpcs["refresh_weather_peak"] = http_error(500, '{"code":"42P10"}')
    rpcs["refresh_city_climate"] = http_error(500, "timeout")
    code, logged = run_main(rpcs, feature_cache={"cities": 54})
    assert code == 1
    assert len(logged["detail"]["failures"]) == 2


def test_recompute_correlation_failing_still_stops_the_job(run_main):
    """Not optional: without it the run has produced nothing worth logging."""
    rpcs = dict(ALL_OK)
    rpcs["recompute_correlation"] = http_error(500, "canceling statement due to statement timeout")
    with pytest.raises(requests.HTTPError, match="statement timeout"):
        run_main(rpcs, feature_cache={"cities": 54})


def test_a_feature_cache_failure_is_still_counted(run_main):
    """RuntimeError means ad4_28 is absent (a note); anything else is a failure."""
    code, logged = run_main(dict(ALL_OK),
                            feature_cache=http_error(500, "refresh_city_day_features"))
    assert code == 1
    assert logged["detail"]["features_error"]
    assert any("refresh_feature_cache" in f for f in logged["detail"]["failures"])


def test_a_database_without_ad4_28_is_only_a_note(run_main):
    code, logged = run_main(dict(ALL_OK),
                            feature_cache=RuntimeError("refresh_city_day_features: no such function"))
    assert code == 0
    assert logged["detail"]["features_error"] is None


# --------------------------------------------------------------------------
# the source itself
# --------------------------------------------------------------------------
def test_capacity_reports_the_server_message_not_a_bare_status():
    """raise_for_status() throws away the body, and the body is the diagnosis.

    Six daily runs reported only `500 Server Error for .../recompute_correlation`
    while Postgres was logging `canceling statement due to statement timeout`
    at the same second.
    """
    src = (pathlib.Path(__file__).parent.parent / "scripts" / "capacity.py").read_text()
    # the CALL, not the word - the comment in the file explains the history
    assert not re.search(r"^\s*[\w.]*\.raise_for_status\(\)", src, re.M), \
        "use common.rpc, which keeps the server's message"
    assert re.search(r"from common import .*\brpc\b", src)
