"""A request Open-Meteo leaves unanswered is asked again in rounds spread over
the run, the missing part only (scripts/honest_record.fetch_each,
common.ask_in_rounds). On 10 Oct four requests stalled together and every ask
of the same URL stalled again, 5 s and a pass later, while the other cities
answered; one answered when asked about 4 minutes after its first stall. On
27 Sep the nightly station model's multi-model request timed out twice for
moscow, paris, lucknow and houston, so those four had no station-model row
and priced on P3.9 alone that day."""
import threading

import common
import ensemble_record as er
import honest_record as hr

CITIES = [{"city_key": k, "timezone": "UTC"} for k in ("london", "moscow", "paris", "tokyo")]


def _asker(failures):
    """failures: {(city, part): number of asks that get no answer}; every
    other ask answers f"{city} {part}"."""
    calls, lock = [], threading.Lock()

    def ask(c, part):
        with lock:
            n = calls.count((c["city_key"], part))
            calls.append((c["city_key"], part))
        return None if n < failures.get((c["city_key"], part), 0) else f"{c['city_key']} {part}"
    return ask, calls


def test_only_the_missing_part_is_asked_again_and_order_is_kept(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    ask, calls = _asker({("moscow", "models"): 1, ("paris", "heating"): 1, ("paris", "models"): 1})
    out = hr.fetch_each(ask, CITIES)
    assert [c["city_key"] for c, _ in out] == ["london", "moscow", "paris", "tokyo"]
    assert sorted(calls) == sorted([(c["city_key"], p) for c in CITIES for p in hr.PARTS]
                                   + [("moscow", "models"), ("paris", "heating"), ("paris", "models")])
    got = {c["city_key"]: r for c, r in out}
    assert got["moscow"] == ("moscow heating", "moscow models")   # the heating it had is not asked again
    assert all(None not in r for r in got.values())


def test_the_rounds_go_on_with_the_waits_and_are_counted(monkeypatch):
    slept, rounds = [], []
    monkeypatch.setattr(common.time, "sleep", slept.append)
    ask, calls = _asker({("paris", "heating"): 3})
    out = dict((c["city_key"], r) for c, r in hr.fetch_each(ask, CITIES, rounds))
    assert out["paris"] == ("paris heating", "paris models")
    assert slept == list(hr.RETRY_WAITS[:3]) and calls.count(("paris", "heating")) == 4
    assert [(r["asked"], r["answered"]) for r in rounds] == [(8, 7), (1, 0), (1, 0), (1, 1)]
    assert all(set(r) == {"after_s", "asked", "answered", "slowest_answer_s"} for r in rounds)
    assert rounds[1]["slowest_answer_s"] is None and rounds[-1]["slowest_answer_s"] is not None


def test_a_city_still_unreached_is_reported_missing(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    ask, calls = _asker({("paris", "models"): 99})
    monkeypatch.setattr(hr, "current_request", ask)
    monkeypatch.setattr(hr, "current_rows", lambda *a: ([], []))
    _, _, missing = hr.forward_rows(CITIES)
    assert missing == ["paris"]
    assert calls.count(("paris", "models")) == 1 + len(hr.RETRY_WAITS) and calls.count(("paris", "heating")) == 1


def test_nobody_is_asked_twice_when_every_city_answers(monkeypatch):
    slept, rounds = [], []
    monkeypatch.setattr(common.time, "sleep", slept.append)
    ask, calls = _asker({})
    hr.fetch_each(ask, CITIES, rounds)
    assert len(calls) == 8 and slept == [] and len(rounds) == 1


# A run that outlives its step is killed before it writes anything: on 28 Sep
# eight cities timed out and the 5-minute step lost the whole night. main()
# sets a deadline; no request waits past it and no round starts without time.

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
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.TIMEOUT - 5)
    assert hr._get("u", {}, "london previous models") == {"ok": 1}
    assert seen and seen[0] <= hr.TIMEOUT - 5
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.MIN_LEFT - 1)
    assert hr._get("u", {}, "london previous models") is None and len(seen) == 1   # not asked at all
    monkeypatch.setattr(hr, "_deadline", None)
    hr._get("u", {}, "london previous models")
    assert seen[-1] == hr.TIMEOUT                         # no deadline: the whole timeout


def test_a_stalled_ask_is_not_repeated_at_once(monkeypatch):
    """10 Oct: no stalled URL answered when asked again 5 s later. _get asks
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
    monkeypatch.setattr(hr, "_deadline", hr.time.monotonic() + hr.RETRY_WAITS[0] + hr.MIN_LEFT - 1)
    ask, calls = _asker({("paris", "heating"): 1})
    out = dict((c["city_key"], r) for c, r in hr.fetch_each(ask, CITIES))
    assert calls.count(("paris", "heating")) == 1 and slept == [] and out["paris"] == (None, "paris models")


def test_the_waits_fit_inside_the_runs():
    """The waits alone leave each run time to ask; the deadline stops the
    rounds that do not fit."""
    assert all(a < b for a, b in zip(hr.RETRY_WAITS, hr.RETRY_WAITS[1:]))
    assert hr.RETRY_WAITS[0] + hr.TIMEOUT < hr.RUN_SECONDS
    assert er.RETRY_WAITS == hr.RETRY_WAITS and er.TIMEOUT == hr.TIMEOUT
    assert hr.TIMEOUT <= 20


def test_the_deadline_fits_inside_the_step():
    import pathlib
    import re
    wf = (pathlib.Path(__file__).resolve().parents[1] / ".github" / "workflows" / "archive_observations.yml").read_text()
    step = wf[wf.index("- name: Append the honest training record"):]
    minutes = int(re.search(r"timeout-minutes:\s*(\d+)", step[:400]).group(1))
    assert hr.RUN_SECONDS + 30 <= minutes * 60
