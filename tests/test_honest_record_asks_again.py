"""A city whose Open-Meteo request hangs is asked again once, after the pass
(scripts/honest_record.fetch_each). On 27 Sep the nightly station model's
multi-model request timed out twice for moscow, paris, lucknow and houston,
so those four had no station-model row and priced on P3.9 alone that day."""
import threading

import honest_record as hr

CITIES = [{"city_key": k, "timezone": "UTC"} for k in ("london", "moscow", "paris", "tokyo")]


def _fetcher(failures):
    """failures: {city: [answer for call 1, call 2, ...]}; others answer ('h', 'm')."""
    calls, lock = [], threading.Lock()

    def fetch(c):
        with lock:
            n = sum(1 for k in calls if k == c["city_key"])
            calls.append(c["city_key"])
        seq = failures.get(c["city_key"])
        return seq[n] if seq else ("h", "m")
    return fetch, calls


def test_only_the_unreached_are_asked_again_and_order_is_kept(monkeypatch):
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    fetch, calls = _fetcher({"moscow": [("h", None), ("h2", "m2")], "paris": [(None, None), ("h", "m")]})
    out = hr.fetch_each(fetch, CITIES)
    assert [c["city_key"] for c, _ in out] == ["london", "moscow", "paris", "tokyo"]
    assert sorted(calls) == sorted(["london", "moscow", "paris", "tokyo", "moscow", "paris"])
    assert dict((c["city_key"], r) for c, r in out)["moscow"] == ("h", "m2")    # the first pass's part is kept
    assert all(None not in r for _, r in out)


def test_a_city_still_unreached_is_reported_missing(monkeypatch):
    monkeypatch.setattr(hr.time, "sleep", lambda s: None)
    fetch, calls = _fetcher({"paris": [(None, None), ("h", None)]})
    monkeypatch.setattr(hr, "fetch_current", fetch)
    monkeypatch.setattr(hr, "current_rows", lambda *a: ([], []))
    _, _, missing = hr.forward_rows(CITIES)
    assert missing == ["paris"] and calls.count("paris") == 2 and len(calls) == 5


def test_nobody_is_asked_twice_when_every_city_answers(monkeypatch):
    slept = []
    monkeypatch.setattr(hr.time, "sleep", slept.append)
    fetch, calls = _fetcher({})
    hr.fetch_each(fetch, CITIES)
    assert len(calls) == 4 and slept == []


# A run that outlives its step is killed before it writes anything: on 28 Sep
# eight cities timed out and the 5-minute step lost the whole night. main()
# sets a deadline; no request waits past it and no retry starts without time.

def test_no_request_waits_past_the_runs_deadline(monkeypatch):
    seen = []

    class Answer:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"ok": 1}

    def get(url, params=None, timeout=None):
        seen.append(timeout)
        return Answer()
    monkeypatch.setattr(hr.requests, "get", get)
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + 30)
    assert hr._get("u", {}, "london previous models") == {"ok": 1}
    assert seen and seen[0] <= 30 < hr.TIMEOUT
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.MIN_LEFT - 1)
    assert hr._get("u", {}, "london previous models") is None and len(seen) == 1   # not asked at all
    monkeypatch.setattr(hr, "_deadline", None)
    hr._get("u", {}, "london previous models")
    assert seen[-1] == hr.TIMEOUT                         # no deadline: the old behaviour


def test_no_second_pass_starts_without_time_for_it(monkeypatch):
    slept = []
    monkeypatch.setattr(hr.time, "sleep", slept.append)
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.RETRY_WAIT)
    fetch, calls = _fetcher({"paris": [(None, None), ("h", "m")]})
    out = dict((c["city_key"], r) for c, r in hr.fetch_each(fetch, CITIES))
    assert calls.count("paris") == 1 and slept == [] and out["paris"] == (None, None)


def test_the_deadline_fits_inside_the_step():
    import pathlib
    import re
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "archive_observations.yml").read_text()
    step = wf[wf.index("- name: Append the honest training record"):]
    minutes = int(re.search(r"timeout-minutes:\s*(\d+)", step[:400]).group(1))
    assert hr.RUN_SECONDS + 30 <= minutes * 60
