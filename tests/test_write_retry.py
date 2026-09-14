"""Writes survive a transient 5xx; they still fail loudly on a real error.

The bug these cover: _post_rows called raise_for_status() on the first
response >= 400, with no retry. Reads have had common.retry() since
live_weather.py started hitting 429s from IEM. Writes had nothing, so one
504 anywhere in a 53-city loop ended the whole run - the observations ingest
died at `[11/53] KAUS` having written ten cities and abandoned forty-two.

The failure looked like broken workflows. The workflows were fine; they were
writing real data right up to the point a transient 5xx killed them.
"""
import json
import pytest
import requests

import common


class FakeResponse:
    def __init__(self, status_code, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)


@pytest.fixture
def no_sleep(monkeypatch):
    """Retries must not actually wait - record the delays instead."""
    slept = []
    monkeypatch.setattr(common.time, "sleep", slept.append)
    return slept


@pytest.fixture
def poster(monkeypatch):
    """Replace requests.post with a scripted sequence of outcomes.

    Each entry is either a FakeResponse to return or an Exception to raise.
    The list of calls made is returned for inspection.
    """
    def install(outcomes):
        calls = []

        def fake_post(url, headers=None, params=None, data=None, timeout=None):
            calls.append({"url": url, "params": params, "rows": json.loads(data)})
            outcome = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        monkeypatch.setattr(common.requests, "post", fake_post)
        return calls
    return install


@pytest.fixture(autouse=True)
def fake_config(monkeypatch):
    monkeypatch.setattr(common, "_cfg", lambda: {"url": "https://db.test", "key": "k"})
    monkeypatch.setattr(common, "_headers", lambda: {"apikey": "k"})


OK = FakeResponse(201)


# --------------------------------------------------------------------------
# the actual fix
# --------------------------------------------------------------------------
@pytest.mark.parametrize("status", [500, 502, 503, 504, 429, 408])
def test_a_transient_failure_is_retried_and_the_rows_still_land(status, poster, no_sleep):
    calls = poster([FakeResponse(status, "gateway timeout"), OK])
    written = common.insert("observations", [{"station": "KAUS", "temp_c": 31.0}])
    assert written == 1, "the caller must be told the row was written"
    assert len(calls) == 2, f"{status} should have been retried once"
    assert calls[1]["rows"] == [{"station": "KAUS", "temp_c": 31.0}]


def test_a_network_drop_mid_transfer_is_retried(poster, no_sleep):
    calls = poster([requests.ConnectionError("connection reset by peer"), OK])
    assert common.insert("observations", [{"station": "KHOU"}]) == 1
    assert len(calls) == 2


def test_a_read_timeout_is_retried(poster, no_sleep):
    calls = poster([requests.Timeout("Read timed out"), OK])
    assert common.insert("forecasts", [{"city_key": "houston"}]) == 1
    assert len(calls) == 2


def test_the_run_survives_a_failure_partway_through_a_city_loop(poster, no_sleep):
    """The KAUS case: city 11 of 53 gets a 504, the other 42 still get written."""
    outcomes = []
    for i in range(53):
        if i == 10:
            outcomes.append(FakeResponse(504, "upstream timed out"))
        outcomes.append(OK)
    poster(outcomes)

    written = sum(common.insert("observations", [{"station": f"K{i:03d}"}])
                  for i in range(53))
    assert written == 53, "every city must be written, not just the ten before the 504"


# --------------------------------------------------------------------------
# what must NOT be retried
# --------------------------------------------------------------------------
@pytest.mark.parametrize("status,body", [
    (400, "PGRST102: All object keys must match"),
    (401, "invalid JWT"),
    (403, "permission denied for table observations (42501)"),
    (404, "relation does not exist"),
    (409, "duplicate key value violates unique constraint"),
])
def test_a_permanent_error_fails_immediately(status, body, poster, no_sleep):
    calls = poster([FakeResponse(status, body)])
    with pytest.raises(requests.HTTPError):
        common.insert("observations", [{"station": "KAUS"}])
    assert len(calls) == 1, f"{status} will fail identically every time - do not retry it"
    assert no_sleep == [], "a permanent error must not cost 35s of backoff"


def test_giving_up_raises_rather_than_reporting_success(poster, no_sleep):
    """A silent write failure would be worse than the bug being fixed."""
    calls = poster([FakeResponse(503, "service unavailable")])
    with pytest.raises(requests.HTTPError):
        common.insert("observations", [{"station": "KAUS"}])
    assert len(calls) == 4, "four attempts, then give up"


# --------------------------------------------------------------------------
# backoff behaviour
# --------------------------------------------------------------------------
def test_retry_after_is_honoured_on_429(poster, no_sleep):
    poster([FakeResponse(429, "rate limited", {"Retry-After": "12"}), OK])
    common.insert("observations", [{"station": "KAUS"}])
    assert no_sleep == [12.0]


def test_a_nonsense_retry_after_falls_back_to_the_normal_backoff(poster, no_sleep):
    poster([FakeResponse(429, "rate limited", {"Retry-After": "in a bit"}), OK])
    common.insert("observations", [{"station": "KAUS"}])
    assert no_sleep == [5]


def test_backoff_is_bounded(poster, no_sleep):
    poster([FakeResponse(429, "x", {"Retry-After": "9999"}), OK])
    common.insert("observations", [{"station": "KAUS"}])
    assert no_sleep == [120], "never sleep longer than two minutes on one batch"


# --------------------------------------------------------------------------
# the retry must not change what gets written
# --------------------------------------------------------------------------
def test_upsert_keeps_its_conflict_target_across_a_retry(poster, no_sleep):
    calls = poster([FakeResponse(502, "bad gateway"), OK])
    common.upsert("trades_observed", [{"condition_id": "c1", "price": 0.4}],
                  "condition_id,traded_at,price,size,proxy_wallet")
    assert len(calls) == 2
    assert calls[1]["params"] == {
        "on_conflict": "condition_id,traded_at,price,size,proxy_wallet"}


def test_a_retry_resends_the_same_batch_not_the_whole_set(poster, no_sleep):
    calls = poster([OK, FakeResponse(500, "boom"), OK])
    rows = [{"n": i} for i in range(4)]
    assert common.insert("t", rows, chunk=2) == 4
    assert [c["rows"] for c in calls] == [
        [{"n": 0}, {"n": 1}],
        [{"n": 2}, {"n": 3}],
        [{"n": 2}, {"n": 3}],
    ]


def test_mixed_row_shapes_still_split_before_any_retry(poster, no_sleep):
    """by_shape runs first; a retry must not merge the groups back together."""
    calls = poster([FakeResponse(503, "x"), OK, OK])
    rows = [{"kind": "SPIKE", "change_c": 4.0}, {"kind": "CONDITION_CHANGE"}]
    assert common.insert("weather_events", rows) == 2
    assert [c["rows"] for c in calls] == [
        [{"kind": "SPIKE", "change_c": 4.0}],
        [{"kind": "SPIKE", "change_c": 4.0}],
        [{"kind": "CONDITION_CHANGE"}],
    ]


def test_nothing_is_posted_for_an_empty_write(poster, no_sleep):
    calls = poster([OK])
    assert common.insert("observations", []) == 0
    assert calls == []


# --------------------------------------------------------------------------
# READS, for exactly the same reason.
#
# The docstring at the top of this file said "Reads have had common.retry()
# since live_weather.py started hitting 429s" - but that is a helper callers
# have to remember to wrap themselves in, and most never did. rest() itself
# retried nothing, so one blip killed a pipeline:
#
#   Intraday Pipeline, 2026-09-14 - all three stages dead in 30 seconds
#   edge_engine.py:170  rest("v_canonical_markets", ...)
#   502 Bad Gateway
#
# Nothing was wrong with the query; the same request returned 101 rows a few
# minutes later. PostgREST had briefly gone away (it reloads its schema cache
# when the schema changes, and a function had just been created).
#
# A read is safe to repeat - no idempotency to reason about - so this is
# strictly easier than the write case it mirrors.
# --------------------------------------------------------------------------
@pytest.fixture
def getter(monkeypatch):
    """Replace requests.get with a scripted sequence of outcomes."""
    def go(outcomes):
        calls = []

        def fake_get(url, headers=None, params=None, timeout=None):
            calls.append(url)
            out = outcomes[min(len(calls) - 1, len(outcomes) - 1)]
            if isinstance(out, Exception):
                raise out
            return out

        monkeypatch.setattr(common.requests, "get", fake_get)
        return calls
    return go


class OkResponse(FakeResponse):
    def __init__(self, rows):
        super().__init__(200)
        self._rows = rows

    def json(self):
        return self._rows


def test_a_read_survives_a_transient_502(getter, no_sleep):
    calls = getter([FakeResponse(502, "Bad Gateway"),
                    FakeResponse(502, "Bad Gateway"),
                    OkResponse([{"market_id": "m1"}])])
    assert common.rest("v_canonical_markets") == [{"market_id": "m1"}]
    assert len(calls) == 3
    assert no_sleep == [5, 10], "linear backoff, same shape as writes"


def test_a_read_gives_up_loudly_rather_than_returning_nothing(getter, no_sleep):
    """A read that quietly returned [] would let a job compute over an empty
    set and report success - a worse bug than the one being fixed."""
    calls = getter([FakeResponse(503, "unavailable")])
    with pytest.raises(requests.HTTPError):
        common.rest("v_canonical_markets")
    assert len(calls) == 4


def test_a_read_does_not_retry_a_real_error(getter, no_sleep):
    """403 is permission denied and 404 is no such relation. Both will fail
    identically three more times."""
    for status in (403, 404, 400):
        calls = getter([FakeResponse(status, "nope")])
        with pytest.raises(requests.HTTPError):
            common.rest("v_canonical_markets")
        assert len(calls) == 1, f"{status} must not be retried"
    assert no_sleep == []


def test_a_read_retries_a_dropped_connection(getter, no_sleep):
    calls = getter([requests.ConnectionError("reset by peer"),
                    OkResponse([{"ok": True}])])
    assert common.rest("cities") == [{"ok": True}]
    assert len(calls) == 2
