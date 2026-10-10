"""The record asks many cities a request, one request at a time, and asks a
chunk Open-Meteo left unanswered again in rounds spread over the run, the
missing part only (scripts/honest_record.fetch_each, common.ask_in_rounds).

Each of the record's 13 logged runs from 27 Sep to 10 Oct was partial, asking
one city a request, four at a time, from a GitHub runner; n8n's P1.5, one
request for every city, logged ok on 55 of 55 runs in the week to 10 Oct. On
10 Oct 3 of 8 requests sent at once came back 429 "Too many concurrent
requests", and a URL asked again soon after hanging hung again. On 27 Sep the
nightly station model's multi-model request timed out twice for moscow,
paris, lucknow and houston, so those four had no station-model row and priced
on P3.9 alone that day."""
import datetime as dt
import threading

import common
import ensemble_record as er
import honest_record as hr

CITIES = [{"city_key": k, "timezone": "UTC", "latitude": 1.0 + i, "longitude": 2.0 + i}
          for i, k in enumerate(("london", "moscow", "paris", "tokyo"))]


def _asker(failures):
    """failures: {(first city of the chunk, part): number of asks with no
    answer}; every other ask answers each city of the chunk."""
    calls, lock = [], threading.Lock()

    def ask(chunk, part):
        key = (chunk[0]["city_key"], part)
        with lock:
            n = calls.count(key)
            calls.append(key)
        if n < failures.get(key, 0):
            return None
        return [f"{c['city_key']} {part}" for c in chunk]
    return ask, calls


def test_cities_go_in_chunks_one_request_a_part(monkeypatch):
    monkeypatch.setattr(hr, "CHUNK", 3)
    ask, calls = _asker({})
    out = hr.fetch_each(ask, CITIES)
    assert sorted(calls) == sorted([("london", "heating"), ("london", "models"),
                                    ("tokyo", "heating"), ("tokyo", "models")])
    assert [c["city_key"] for c, _ in out] == ["london", "moscow", "paris", "tokyo"]
    assert dict((c["city_key"], r) for c, r in out)["paris"] == ("paris heating", "paris models")


def test_only_the_missing_part_is_asked_again_and_order_is_kept(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    monkeypatch.setattr(hr, "CHUNK", 2)
    ask, calls = _asker({("paris", "models"): 1})
    out = hr.fetch_each(ask, CITIES)
    assert [c["city_key"] for c, _ in out] == ["london", "moscow", "paris", "tokyo"]
    assert sorted(calls) == sorted([("london", "heating"), ("london", "models"), ("paris", "heating"),
                                    ("paris", "models"), ("paris", "models")])
    got = {c["city_key"]: r for c, r in out}
    assert got["tokyo"] == ("tokyo heating", "tokyo models")   # its heating was not asked again
    assert all(None not in r for r in got.values())


def test_the_rounds_go_on_with_the_waits_and_are_counted(monkeypatch):
    slept, rounds = [], []
    monkeypatch.setattr(common.time, "sleep", slept.append)
    monkeypatch.setattr(hr, "CHUNK", 2)
    ask, calls = _asker({("paris", "heating"): 3})
    out = dict((c["city_key"], r) for c, r in hr.fetch_each(ask, CITIES, rounds))
    assert out["paris"] == ("paris heating", "paris models")
    assert slept == list(hr.RETRY_WAITS[:3]) and calls.count(("paris", "heating")) == 4
    assert [(r["asked"], r["answered"]) for r in rounds] == [(4, 3), (1, 0), (1, 0), (1, 1)]
    assert all(set(r) == {"after_s", "asked", "answered", "slowest_answer_s"} for r in rounds)
    assert rounds[1]["slowest_answer_s"] is None and rounds[-1]["slowest_answer_s"] is not None


def test_a_chunk_still_unreached_is_reported_missing_city_by_city(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    monkeypatch.setattr(hr, "CHUNK", 2)
    ask, calls = _asker({("paris", "models"): 99})
    monkeypatch.setattr(hr, "current_request", ask)
    monkeypatch.setattr(hr, "current_rows", lambda *a: ([], []))
    _, _, missing = hr.forward_rows(CITIES)
    assert missing == ["paris", "tokyo"]
    assert calls.count(("paris", "models")) == 1 + len(hr.RETRY_WAITS) and calls.count(("paris", "heating")) == 1


def test_nobody_is_asked_twice_when_every_chunk_answers(monkeypatch):
    slept, rounds = [], []
    monkeypatch.setattr(common.time, "sleep", slept.append)
    ask, calls = _asker({})
    hr.fetch_each(ask, CITIES, rounds)
    assert len(calls) == 2 and slept == [] and len(rounds) == 1     # 4 cities, one chunk, two parts


def test_one_request_at_a_time():
    """10 Oct: 3 of 8 requests sent at once came back 429 'Too many concurrent
    requests'; a runner's address is shared with other people's jobs."""
    assert hr.WORKERS == 1 and er.WORKERS == 1
    assert 1 < hr.CHUNK == er.CHUNK <= 50


class _Answer:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        pass

    def json(self):
        return self.body


def test_a_chunk_is_one_request_with_every_citys_coordinates(monkeypatch):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(params)
        return _Answer([{"place": i} for i in range(len(params["latitude"].split(",")))])
    monkeypatch.setattr(hr.requests, "get", get)
    monkeypatch.setattr(hr, "_deadline", None)
    start, end = dt.date(2026, 10, 1), dt.date(2026, 10, 9)
    assert hr.previous_request(CITIES[:3], "heating", start, end) == [{"place": 0}, {"place": 1}, {"place": 2}]
    assert seen[-1]["latitude"] == "1.0,2.0,3.0" and seen[-1]["longitude"] == "2.0,3.0,4.0"
    assert hr.current_request(CITIES[:2], "models") == [{"place": 0}, {"place": 1}]
    assert "models" in seen[-1] and seen[-1]["latitude"] == "1.0,2.0"


def test_a_single_place_answer_and_a_short_list(monkeypatch):
    """One place comes back as an object, several as a list; a list of the
    wrong length is no answer, never paired with the wrong cities."""
    answers = iter([{"one": 1}, [{"a": 1}]])
    monkeypatch.setattr(hr.requests, "get", lambda url, params=None, timeout=None: _Answer(next(answers)))
    monkeypatch.setattr(hr, "_deadline", None)
    assert hr.current_request(CITIES[:1], "heating") == [{"one": 1}]
    assert hr.current_request(CITIES[:2], "heating") is None


# A run that outlives its step is killed before it writes anything: on 28 Sep
# eight cities timed out and the 5-minute step lost the whole night. main()
# sets a deadline; no request waits past it and no round starts without time.

def test_no_request_waits_past_the_runs_deadline(monkeypatch):
    seen = []

    def get(url, params=None, timeout=None):
        seen.append(timeout)
        return _Answer({"ok": 1})
    monkeypatch.setattr(hr.requests, "get", get)
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.TIMEOUT - 5)
    assert hr._get("u", {}, "london previous models") == {"ok": 1}
    assert seen and seen[0] <= hr.TIMEOUT - 5
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.MIN_LEFT - 1)
    assert hr._get("u", {}, "london previous models") is None and len(seen) == 1   # not asked at all
    monkeypatch.setattr(hr, "_deadline", None)
    hr._get("u", {}, "london previous models")
    assert seen[-1] == hr.TIMEOUT                         # no deadline: the whole timeout


def test_a_hung_ask_is_not_repeated_at_once(monkeypatch):
    """10 Oct: no hung URL answered when asked again soon after. _get asks
    once; the rounds come back to it after a wait."""
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(timeout)
        raise hr.requests.exceptions.ReadTimeout("Read timed out.")
    monkeypatch.setattr(hr.requests, "get", get)
    monkeypatch.setattr(hr, "_deadline", None)
    assert hr._get("u", {}, "paris previous heating") is None and len(calls) == 1


def test_no_round_starts_without_time_for_it(monkeypatch):
    slept = []
    monkeypatch.setattr(common.time, "sleep", slept.append)
    monkeypatch.setattr(hr, "CHUNK", 2)
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.RETRY_WAITS[0] + hr.MIN_LEFT - 1)
    ask, calls = _asker({("paris", "heating"): 1})
    out = dict((c["city_key"], r) for c, r in hr.fetch_each(ask, CITIES))
    assert calls.count(("paris", "heating")) == 1 and slept == []
    assert out["paris"] == (None, "paris models") and out["tokyo"] == (None, "tokyo models")


def test_the_waits_fit_inside_the_runs():
    """The waits alone leave each run time to ask; the deadline stops the
    rounds that do not fit."""
    assert all(a < b for a, b in zip(hr.RETRY_WAITS, hr.RETRY_WAITS[1:]))
    assert hr.RETRY_WAITS[0] + hr.TIMEOUT < hr.RUN_SECONDS
    assert er.RETRY_WAITS == hr.RETRY_WAITS and er.TIMEOUT == hr.TIMEOUT


def test_the_deadline_fits_inside_the_step():
    import pathlib
    import re
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "archive_observations.yml").read_text()
    step = wf[wf.index("- name: Append the honest training record"):]
    minutes = int(re.search(r"timeout-minutes:\s*(\d+)", step[:400]).group(1))
    assert hr.RUN_SECONDS + 30 <= minutes * 60
