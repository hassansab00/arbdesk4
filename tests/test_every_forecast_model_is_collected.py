"""Every forecast model, not one blend (plan v2.1 P2.8).

On 23 Sep weather_forecasts held one forecast series per city - Open-Meteo's
best_match, its own choice of model per location - plus NWS for 12 cities.
With one predictor there is nothing to weight, so the hit tournament (P3.8)
had no model to choose between. The previous-runs ingest now also asks for
each model by name, in one extra request per city, and stores each under its
own label.
"""
import ingest_forecasts as f


def _response(models):
    times = [f"2026-09-2{d}T{h:02d}:00" for d in (1, 2) for h in range(24)]
    hourly = {"time": times}
    for i, m in enumerate(models):
        for lead in f.LEADS:
            hourly[f"temperature_2m_previous_day{lead}_{m}"] = [
                20.0 + i + (h % 24) / 10 for h in range(len(times))]
    return {"hourly": hourly}


def test_the_default_asks_for_seven_models():
    assert len(f.MODELS) == 7
    assert "ecmwf_ifs025" in f.MODELS and "gfs_seamless" in f.MODELS
    assert f.MODEL_LABEL not in [f"open_meteo_{m}" for m in f.MODELS]


def test_each_model_reads_its_own_columns_and_keeps_its_own_label():
    js = _response(["ecmwf_ifs025", "gfs_seamless"])
    ecmwf = f.build_rows("paris", js, "ecmwf_ifs025")
    gfs = f.build_rows("paris", js, "gfs_seamless")
    assert {r["model"] for r in ecmwf} == {"open_meteo_ecmwf_ifs025"}
    assert {r["model"] for r in gfs} == {"open_meteo_gfs_seamless"}
    assert len(ecmwf) == len(gfs) == 2 * len(f.LEADS), "2 local days x 7 leads"
    # the maximum of each local day, from that model's column only
    assert max(r["forecast_max_c"] for r in ecmwf) == 22.3
    assert max(r["forecast_max_c"] for r in gfs) == 23.3


def test_a_model_the_response_lacks_writes_nothing():
    assert f.build_rows("paris", _response(["ecmwf_ifs025"]), "jma_seamless") == []


def test_best_match_is_read_exactly_as_before():
    """An unsuffixed response is the existing series, under its existing label."""
    times = [f"2026-09-21T{h:02d}:00" for h in range(24)]
    js = {"hourly": {"time": times, **{f"temperature_2m_previous_day{l}": [15.0] * 24
                                       for l in f.LEADS}}}
    rows = f.build_rows("paris", js)
    assert {r["model"] for r in rows} == {f.MODEL_LABEL}
    assert {r["source"] for r in rows} == {"open-meteo-previous-runs"}


def test_the_model_request_cannot_stop_best_match():
    """Additive by construction: its failures are counted apart and never enter
    the counters that decide `incomplete`, a failed job or a paid continuation."""
    src = open(f.__file__).read()
    block = src[src.index("if MODELS:\n                mjs"):src.index('total += out["got"]')]
    for counter in ("missing_chunks", "unreached_chunks", "refused +=", "unreached +="):
        assert counter not in block, counter


def test_a_silent_source_is_given_up_on_in_seconds_not_minutes():
    assert f.TIMEOUT <= 30


def test_per_model_rows_never_enter_weather_forecasts():
    """probability_engine._forecast_for takes the newest, shortest-lead row of
    ANY model. A per-model row there would tie with best_match on run_at."""
    src = open(f.__file__).read()
    block = src[src.index("if MODELS:\n                mjs"):src.index('total += out["got"]')]
    assert '"weather_forecast_models"' in block
    assert '"weather_forecasts"' not in block


def test_a_current_run_row_is_a_forecast_known_at_its_fetch_time():
    """Plan v2.1 P3.8: the tournament's evening-before checkpoint needs rows
    that provably existed then - run_at is the fetch, lead is days ahead of
    the city's own today."""
    import datetime as dt
    fetched = dt.datetime(2026, 9, 23, 3, 10, tzinfo=dt.timezone.utc)   # New York: still 22 Sep
    # UTC hours from yesterday (UTC) to three days ahead, as fetch_current asks
    start = dt.datetime(2026, 9, 22, 0, 0)
    times = [(start + dt.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in range(5 * 24)]
    # the value is the New York local hour / 10 + 20, so each local day peaks at 22.3
    vals = [20.0 + ((start + dt.timedelta(hours=i) - dt.timedelta(hours=4)).hour) / 10 for i in range(5 * 24)]
    js = {"hourly": {"time": times, "temperature_2m_gfs_seamless": vals}}
    rows = f.build_current_rows("nyc", js, "gfs_seamless", fetched, "America/New_York")
    by_date = {r["for_date"]: r for r in rows}
    assert by_date["2026-09-22"]["lead_days"] == 0 and by_date["2026-09-23"]["lead_days"] == 1
    assert sorted(by_date) == ["2026-09-22", "2026-09-23", "2026-09-24"], \
        "today and the next two days; the partial local days at the edges are dropped"
    assert {r["source"] for r in rows} == {f.CURRENT_SOURCE}
    assert {r["run_at"] for r in rows} == {fetched.isoformat()}
    assert by_date["2026-09-23"]["forecast_max_c"] == 22.3


def test_previous_runs_hours_are_grouped_into_the_city_s_wall_clock_days():
    """26 Sep: Open-Meteo's timezone=auto is ONE fixed offset for the whole
    range (the offset at request time), so the job asks for UTC and groups
    with the city's zone. Paris in January is UTC+1: 23:30 UTC on 14 Jan is
    00:30 on the 15th; in July (UTC+2) 22:30 UTC is already the next day."""
    hours = ["2026-01-14T22:00", "2026-01-14T23:00", "2026-07-14T21:00", "2026-07-14T22:00"]
    vals = [5.0, 9.0, 25.0, 30.0]
    js = {"hourly": {"time": hours, **{f"temperature_2m_previous_day{l}": vals for l in f.LEADS}}}
    rows = f.build_rows("paris", js, tz="Europe/Paris")
    lead1 = {r["for_date"]: r["forecast_max_c"] for r in rows if r["lead_days"] == 1}
    assert lead1 == {"2026-01-14": 5.0, "2026-01-15": 9.0, "2026-07-14": 25.0, "2026-07-15": 30.0}


def test_only_the_asked_days_are_kept_from_a_request_that_reaches_further():
    hours = [f"2026-09-2{d}T{h:02d}:00" for d in (0, 1, 2, 3) for h in range(24)]
    js = {"hourly": {"time": hours, **{f"temperature_2m_previous_day{l}": [10.0] * 96 for l in f.LEADS}}}
    rows = f.build_rows("tokyo", js, tz="Asia/Tokyo", first="2026-09-21", last="2026-09-22")
    assert {r["for_date"] for r in rows} == {"2026-09-21", "2026-09-22"}


def test_the_requests_ask_for_utc_and_a_day_either_side():
    src = open(f.__file__).read()
    assert src.count('"timezone": "GMT"') == 2 and '"timezone": "auto"' not in src
    assert "start - dt.timedelta(days=1)" in src and "end + dt.timedelta(days=1)" in src
