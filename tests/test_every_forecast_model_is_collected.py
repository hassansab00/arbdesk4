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
    block = src[src.index("if MODELS:\n                mjs"):src.index("total += got")]
    for counter in ("missing_chunks", "unreached_chunks", "refused +=", "unreached +="):
        assert counter not in block, counter


def test_a_silent_source_is_given_up_on_in_seconds_not_minutes():
    assert f.TIMEOUT <= 30


def test_per_model_rows_never_enter_weather_forecasts():
    """probability_engine._forecast_for takes the newest, shortest-lead row of
    ANY model. A per-model row there would tie with best_match on run_at."""
    src = open(f.__file__).read()
    block = src[src.index("if MODELS:\n                mjs"):src.index("total += got")]
    assert '"weather_forecast_models"' in block
    assert '"weather_forecasts"' not in block
