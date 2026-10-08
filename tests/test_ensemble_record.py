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
    monkeypatch.setattr(er, "fetch_city", lambda c, runs, fetched_at: asked.append(c["city_key"]) or ([], []))
    assert er.main(["--dry-run"]) == 0
    assert asked == ["b", "c", "a"]
    assert "'asked_first': ['b', 'c', 'a']" in capsys.readouterr().out
