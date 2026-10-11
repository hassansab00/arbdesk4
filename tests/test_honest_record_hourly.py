"""The nightly record also appends the hourly lead-1 record
(best_match_hourly_day1_utc.csv.gz) from the heating answer it already asks
for (scripts/honest_record.py, 11 Oct): it stopped on 5 Oct because only the
daily files were appended. Rows already in it are never rewritten; an hour
asked again that differs is reported, not merged."""
import csv
import datetime as dt
import gzip
import importlib

import honest_record as hr


def _answer(times, temps, tail=None):
    """A heating answer in UTC: every HEATING variable at lead 1 and 2."""
    h = {"time": times}
    for v in hr.HEATING:
        for lead in hr.LEADS:
            h[f"{v}_previous_day{lead}"] = list(tail[v] if tail and v in tail else [1.5] * len(times))
    h["temperature_2m_previous_day1"] = list(temps)
    return {"hourly": h}


def test_the_hourly_columns_are_the_heating_variables_in_order():
    assert hr.HOURLY_HEADER[2:] == ["t", "td", "cloud", "sw", "wind", "precip", "pmsl"]
    assert hr.HEATING == ("temperature_2m", "dew_point_2m", "cloud_cover", "shortwave_radiation",
                          "wind_speed_10m", "precipitation", "pressure_msl")


def test_rows_are_the_hours_before_today_with_a_temperature():
    ans = _answer(["2026-10-09T23:00", "2026-10-10T00:00", "2026-10-10T01:00", "2026-10-11T00:00"],
                  [12.0, None, 13.25, 14.0], tail={"cloud_cover": [69, 70, 71, 72]})
    rows = hr.hourly_day1_rows("london", ans, dt.datetime(2026, 10, 11))
    assert [r[1] for r in rows] == ["2026-10-09T23:00", "2026-10-10T01:00"]   # no temperature; today
    assert rows[0][:5] == ["london", "2026-10-09T23:00", 12.0, 1.5, 69]
    assert hr._text(rows[0])[2:5] == ["12", "1.5", "69"]                     # the record's spelling


def test_lead_2_never_reaches_the_lead_1_record():
    ans = _answer(["2026-10-10T00:00"], [10.0], tail={"dew_point_2m": [3.0]})
    ans["hourly"]["temperature_2m_previous_day2"] = [99.0]
    ans["hourly"]["dew_point_2m_previous_day2"] = [99.0]
    row = hr.hourly_day1_rows("paris", ans, dt.datetime(2026, 10, 11))[0]
    assert row[2] == 10.0 and row[3] == 3.0


def test_an_empty_or_missing_answer_gives_no_rows():
    before = dt.datetime(2026, 10, 11)
    assert hr.hourly_day1_rows("tokyo", None, before) == []
    assert hr.hourly_day1_rows("tokyo", {"hourly": {}}, before) == []


def test_existing_rows_are_never_rewritten_and_a_difference_is_reported():
    old = [["london", "2026-10-05T05:00", "11", "8", "50", "0", "9.2", "0", "1012.5"],
           ["london", "2026-10-05T06:00", "11.5", "8", "50", "3", "9", "0", "1012.4"]]
    new = [["london", "2026-10-05T05:00", 11.0, 8.0, 50, 0, 9.2, 0, 1012.5],      # the same, spelled otherwise
           ["london", "2026-10-05T06:00", 11.5, 8.0, 50, 3, 9.0, 0.1, 1012.4],    # differs: precip
           ["london", "2026-10-05T07:00", 12.0, 8.0, 40, 30, 9.0, 0, 1012.3],     # new
           ["amsterdam", "2026-10-05T07:00", 10.0, 7.0, 40, 30, 9.0, 0, 1012.0]]  # new, sorts first
    rows, added, same, differ, off = hr.append_hourly(old, new)
    assert (added, same, differ, off) == (2, 1, [("london", "2026-10-05T06:00")], 0)
    assert rows[0][0] == "amsterdam"
    assert rows[2] == old[1], "the differing hour keeps the record's row"
    assert rows[3] == ["london", "2026-10-05T07:00", "12", "8", "40", "30", "9", "0", "1012.3"]


def test_an_hour_off_a_citys_grid_is_left_out():
    """Lucknow's record sits at :30 UTC (built with timezone=auto on a +5:30
    offset); a UTC answer's :00 hours would make a second grid."""
    old = [["lucknow", "2026-10-05T05:30", "30", "20", "10", "500", "8", "0", "1008"]]
    new = [["lucknow", "2026-10-05T06:00", 31.0, 20.0, 10, 520, 8.0, 0, 1008.0],
           ["lucknow", "2026-10-05T07:00", 32.0, 20.0, 10, 540, 8.0, 0, 1008.0],
           ["delhi", "2026-10-05T07:00", 32.0, 20.0, 10, 540, 8.0, 0, 1008.0]]    # a city new to the record
    rows, added, same, differ, off = hr.append_hourly(old, new)
    assert (added, same, differ, off) == (1, 0, [], 2)
    assert [r[:2] for r in rows] == [["delhi", "2026-10-05T07:00"], ["lucknow", "2026-10-05T05:30"]]


def test_the_nightly_run_writes_the_hourly_record(tmp_path, monkeypatch):
    for name in ("BEST_MATCH_FILE", "MODELS_FILE", "HOURLY_FILE"):
        monkeypatch.setattr(hr, name, str(tmp_path / f"{name}.csv.gz"))
    hr.write_rows(hr.HOURLY_FILE, hr.HOURLY_HEADER,
                  [["london", "2026-10-01T00:00", "9", "7", "50", "0", "5", "0", "1010"]])
    city = {"city_key": "london", "timezone": "UTC", "latitude": 51.5, "longitude": 0.0, "status": "active"}
    now = dt.datetime(2026, 10, 11, 0, 30, tzinfo=dt.timezone.utc)      # half an hour into the day
    today = now.date()
    yday = (dt.datetime.combine(today, dt.time()) - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    midnight = dt.datetime.combine(today, dt.time()).strftime("%Y-%m-%dT%H:%M")
    heating = _answer(["2026-10-01T00:00", yday, midnight], [9.0, 10.0, 11.0])
    heating["hourly"]["dew_point_2m_previous_day1"] = [7, 1.5, 1.5]
    heating["hourly"]["cloud_cover_previous_day1"] = [50, 1.5, 1.5]
    heating["hourly"]["shortwave_radiation_previous_day1"] = [0, 1.5, 1.5]
    heating["hourly"]["wind_speed_10m_previous_day1"] = [5, 1.5, 1.5]
    heating["hourly"]["precipitation_previous_day1"] = [0, 1.5, 1.5]
    heating["hourly"]["pressure_msl_previous_day1"] = [1010, 1.5, 1.5]
    # main() imports these from whatever module is `common` when it runs (another
    # test may have put a fresh one in sys.modules), so that one is patched
    cm = importlib.import_module("common")
    monkeypatch.setattr(cm, "get_cities", lambda: [city])
    logged = {}
    monkeypatch.setattr(cm, "log_run", lambda job, status, n, detail: logged.update(status=status, d=detail))
    monkeypatch.setattr(hr, "fetch_each", lambda ask, cities, rounds=None: [(city, (heating, {"hourly": {}}))])
    assert hr.main(["--days", "2"], now=now) == 0
    with gzip.open(hr.HOURLY_FILE, "rt", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == hr.HOURLY_HEADER
    assert [r[1] for r in rows[1:]] == ["2026-10-01T00:00", yday], "yesterday's last hour, not today's"
    assert logged["d"]["hourly_added"] == 1 and logged["d"]["hourly_identical"] == 1
    assert logged["d"]["hourly_differ"] == 0 and logged["d"]["hourly_off_grid"] == 0
