"""Plan v2.4 P2.10 part 1: the ensemble record (scripts/ensemble_record.py).
Open-Meteo keeps ensemble members for about four days, so the record is kept
from now on: each member's daily maximum on the city's own clock, summarised,
with the run's initialisation and publication times."""
import datetime as dt
import pathlib
import re

import yaml

import ensemble_record as er

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _response(start="2026-09-28T00:00", hours=72, members=3, base=10.0):
    t0 = dt.datetime.fromisoformat(start)
    times = [(t0 + dt.timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(hours)]
    hourly = {"time": times, "temperature_2m": [base + (h % 24) / 2 for h in range(hours)]}
    for m in range(1, members + 1):
        hourly[f"temperature_2m_member{m:02d}"] = [base + m + (h % 24) / 2 for h in range(hours)]
    return {"hourly": hourly}


def test_a_members_maximum_is_taken_on_the_citys_own_day():
    rows = er.daily_rows("london", "UTC", "ecmwf_ifs025", _response(), "2026-09-28T00:00:00+00:00",
                         "2026-09-28T12:07:44+00:00", "2026-09-29T02:40:00+00:00")
    by = {(r[4], r[5]): r for r in rows}
    assert set(by) == {(d, w) for d in ("2026-09-28", "2026-09-29", "2026-09-30") for w in ("00_23", "00_17")}
    whole = by[("2026-09-28", "00_23")]
    # the control peaks at 10 + 23/2 = 21.5; members 1..3 one degree higher each
    assert whole[6] == 4 and whole[7] == round((21.5 + 22.5 + 23.5 + 24.5) / 4, 2)
    assert by[("2026-09-28", "00_17")][7] == round((18.5 + 19.5 + 20.5 + 21.5) / 4, 2)   # hour 17
    assert whole[9:14] == [round(er.percentile([21.5, 22.5, 23.5, 24.5], q), 2) for q in er.QUANTILES]
    assert whole[2:4] == ["2026-09-28T00:00:00+00:00", "2026-09-28T12:07:44+00:00"]


def test_a_day_cut_by_the_time_zone_is_left_out():
    # Tokyo is UTC+9: the response starts at 00Z = 09:00 local on 28 Sep, so
    # that local day has 15 hours here - not a whole day - and 1 Oct only 9
    rows = er.daily_rows("tokyo", "Asia/Tokyo", "gfs025", _response(), None, None, "x")
    days = {r[4] for r in rows if r[5] == "00_23"}
    assert days == {"2026-09-29", "2026-09-30"}


def test_percentiles_interpolate_like_numpy():
    s = [1.0, 2.0, 3.0, 4.0]
    assert er.percentile(s, 50) == 2.5 and er.percentile(s, 10) == 1.3 and er.percentile([7.0], 90) == 7.0


def test_a_stored_key_is_kept_not_rewritten():
    old = [["london", "ecmwf_ifs025", "i", "a", "2026-09-28", "00_23", "51", "20.0", "1.0",
            "18", "19", "20", "21", "22", "t1"]]
    new = [["london", "ecmwf_ifs025", "i", "a", "2026-09-28", "00_23", 51, 25.0, 1.0, 1, 2, 3, 4, 5, "t2"],
           ["london", "ecmwf_ifs025", "i", "a", "2026-09-29", "00_23", 51, 21.0, 1.0, 1, 2, 3, 4, 5, "t2"]]
    got = er.merge(old, new)
    assert len(got) == 2 and got[0][7] == "20.0" and got[1][4] == "2026-09-29"


def test_run_times_come_from_the_meta_file():
    init, avail = er.run_times({"last_run_initialisation_time": 1790553600,
                                "last_run_availability_time": 1790597264})
    assert init == "2026-09-28T00:00:00+00:00" and avail.startswith("2026-09-28T12:07")
    assert er.run_times(None) == (None, None)


def test_the_step_is_in_the_nightly_job_inside_its_budget():
    wf = yaml.safe_load((ROOT / ".github" / "workflows" / "archive_observations.yml").read_text())
    steps = [s for job in wf["jobs"].values() for s in job["steps"]]
    step = next(s for s in steps if s.get("name") == "Append the ensemble record")
    assert "scripts/ensemble_record.py" in step["run"] and step.get("if") == "always()"
    assert er.RUN_SECONDS + 30 <= int(step["timeout-minutes"]) * 60
    names = [s.get("name") for s in steps]
    assert names.index("Append the ensemble record") < names.index("Commit the mirror"), \
        "the record must be written before the mirror is committed"
    assert re.search(r"git add data/mirror data/training", (ROOT / ".github" / "workflows" /
                                                            "archive_observations.yml").read_text())


def _row(city, model, fetched):
    return [city, model, "i", "a", "2026-10-07", "00_23", "51", "20.0", "1.0", "18", "19", "20", "21", "22", fetched]


def test_the_city_least_recently_recorded_is_asked_first():
    # The deadline does not reach every city (8 Oct: 29 of 48 whole), and the
    # table's own order put the same 17 last every night, so they never had a
    # row. A city missing either model counts as never recorded; ties keep the
    # order given.
    cities = [{"city_key": k} for k in ("a", "b", "c", "d")]
    rows = [_row("a", "ecmwf_ifs025", "2026-10-08T02:52:46+00:00"), _row("a", "gfs025", "2026-10-08T02:52:46+00:00"),
            _row("b", "ecmwf_ifs025", "2026-10-08T02:52:46+00:00"),
            _row("c", "ecmwf_ifs025", "2026-10-07T02:50:23+00:00"), _row("c", "gfs025", "2026-10-08T02:52:46+00:00")]
    assert [c["city_key"] for c in er.least_recorded_first(cities, rows)] == ["b", "d", "c", "a"]
    assert [c["city_key"] for c in er.least_recorded_first(cities, [])] == ["a", "b", "c", "d"]


def test_the_run_asks_in_that_order(monkeypatch, capsys):
    import common
    cities = [{"city_key": k, "latitude": 1.0, "longitude": 2.0, "timezone": "UTC"} for k in ("a", "b", "c")]
    asked = []
    monkeypatch.setattr(common, "get_cities", lambda: cities)
    monkeypatch.setattr(er, "_get", lambda *a, **k: None)
    monkeypatch.setattr(er, "WORKERS", 1)
    monkeypatch.setattr(er, "read_rows", lambda: [_row("a", "ecmwf_ifs025", "2026-10-08T02:52:46+00:00"),
                                                  _row("a", "gfs025", "2026-10-08T02:52:46+00:00")])
    monkeypatch.setattr(er, "RETRY_WAITS", ())
    monkeypatch.setattr(er, "CHUNK", 1)
    monkeypatch.setattr(er, "fetch_model", lambda chunk, model: asked.append(chunk[0]["city_key"]))
    assert er.main(["--dry-run"]) == 0
    assert list(dict.fromkeys(asked)) == ["b", "c", "a"]
    assert "'asked_first': ['b', 'c', 'a']" in capsys.readouterr().out


def test_a_model_left_unanswered_is_asked_again_later_and_alone(monkeypatch, capsys):
    """10 Oct: four asks hung together and each hung again when asked 5 s
    later; the run ended at 136 s of its 360 with them missing. Now the cities
    go in chunks, one request a model, and a chunk's missing model alone is
    asked again after each wait while the deadline leaves time; the rounds are
    in the run's detail."""
    import common
    cities = [{"city_key": k, "latitude": 1.0, "longitude": 2.0, "timezone": "UTC"} for k in ("a", "b", "c")]
    asked, slept = [], []
    monkeypatch.setattr(common, "get_cities", lambda: cities)
    monkeypatch.setattr(common.time, "sleep", slept.append)
    monkeypatch.setattr(er, "CHUNK", 2)
    monkeypatch.setattr(er, "_get", lambda *a, **k: None)
    monkeypatch.setattr(er, "read_rows", lambda: [])
    monkeypatch.setattr(er, "daily_rows",
                        lambda city, tz, model, js, *a: [_row(city, model, "2026-10-10T02:52:12+00:00")] if js == city else [])

    def fetch_model(chunk, model):
        key = (tuple(c["city_key"] for c in chunk), model)
        asked.append(key)
        if key == (("c",), "gfs025") and asked.count(key) < 3:
            return None
        return [c["city_key"] for c in chunk]
    monkeypatch.setattr(er, "fetch_model", fetch_model)
    assert er.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert asked.count((("c",), "gfs025")) == 3 and len(asked) == 6 and slept == list(er.RETRY_WAITS[:2])
    assert sorted(set(asked)) == [(("a", "b"), "ecmwf_ifs025"), (("a", "b"), "gfs025"),
                                  (("c",), "ecmwf_ifs025"), (("c",), "gfs025")]
    assert "'unreached': {}" in out and "'rows_new': 6" in out
    assert "'rounds': [{'after_s'" in out


def test_a_chunk_never_answered_leaves_each_of_its_cities_missing(monkeypatch, capsys):
    import common
    cities = [{"city_key": k, "latitude": 1.0, "longitude": 2.0, "timezone": "UTC"} for k in ("a", "b", "c")]
    monkeypatch.setattr(common, "get_cities", lambda: cities)
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    monkeypatch.setattr(er, "CHUNK", 2)
    monkeypatch.setattr(er, "_get", lambda *a, **k: None)
    monkeypatch.setattr(er, "read_rows", lambda: [])
    monkeypatch.setattr(er, "daily_rows", lambda *a: [])
    monkeypatch.setattr(er, "fetch_model",
                        lambda chunk, model: None if model == "ecmwf_ifs025" and chunk[0]["city_key"] == "a"
                        else [c["city_key"] for c in chunk])
    assert er.main(["--dry-run"]) == 0
    assert "'unreached': {'a': ['ecmwf_ifs025'], 'b': ['ecmwf_ifs025']}" in capsys.readouterr().out


def test_a_chunk_is_one_request_and_a_short_answer_is_none(monkeypatch):
    seen = []
    cities = [{"city_key": k, "latitude": 1.0 + i, "longitude": 5.0 + i} for i, k in enumerate(("a", "b"))]
    monkeypatch.setattr(er, "_deadline", None)
    monkeypatch.setattr(er, "_get", lambda url, params, label, tries=1: seen.append(params) or [{"x": 1}, {"x": 2}])
    assert er.fetch_model(cities, "gfs025") == [{"x": 1}, {"x": 2}]
    assert seen[-1]["latitude"] == "1.0,2.0" and seen[-1]["longitude"] == "5.0,6.0" and seen[-1]["models"] == "gfs025"
    monkeypatch.setattr(er, "_get", lambda url, params, label, tries=1: [{"x": 1}])
    assert er.fetch_model(cities, "gfs025") is None
    monkeypatch.setattr(er, "_get", lambda url, params, label, tries=1: {"x": 1})
    assert er.fetch_model(cities[:1], "gfs025") == [{"x": 1}]


def test_the_browser_port_reads_the_fixture_the_same():
    """The city popup computes each member's day in the browser
    (web/lib/ensemble.ts). web/tests/ensemble.test.cjs pins these same numbers
    on this same fixture, so the two cannot drift apart unseen."""
    import json
    js = json.loads((ROOT / "web" / "tests" / "fixtures" / "ensemble_tokyo.json").read_text())
    rows = {(r[4], r[5]): r[6:14] for r in er.daily_rows("tokyo", "Asia/Tokyo", "ecmwf_ifs025", js, None, None, "x")}
    assert rows[("2026-10-10", "00_23")] == [3, 26.17, 0.236, 26.0, 26.0, 26.0, 26.25, 26.4]
    assert rows[("2026-10-11", "00_23")] == [3, 30.83, 6.485, 26.1, 26.25, 26.5, 33.25, 37.3]
    assert {d for d, _ in rows} == {"2026-10-10", "2026-10-11"}
