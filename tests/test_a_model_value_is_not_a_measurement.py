"""A model value is never a floor, a maximum or a current temperature (plan v2 P2.7).

For 37 of 48 active cities live_weather holds Open-Meteo model output
(source_kind = 'model'). On 23 Sep it had become the stored running maximum and
then the pricing floor in 10 of them - Seoul's at 23.2 C while its station had
measured 21.0. These pin the three places that let it through, and the guard
behind them.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _sql(name):
    return re.sub(r"--[^\n]*", "", (ROOT / "sql" / name).read_text())


def test_the_timing_job_folds_only_a_station_into_the_maximum():
    src = _sql("ad4_live_weather_timing.sql")
    assert "source_kind is not distinct from 'station' as live_is_station" in src
    for col in ("kept_max_c", "kept_max_at", "kept_min_c", "live_temp_c", "live_at"):
        m = re.search(r"case when (.*?)\n\s*then pv\.\w+ end\s+as " + col, src)
        assert m and "pv.live_is_station" in m.group(1), f"{col} can still carry a model value"


def test_the_floor_view_takes_only_a_station_reading():
    src = _sql("ad4_71_observation_health.sql")
    today = re.search(r"case when (.*?)then lw\.temp_c end\s+as latest_temp_today_c", src, re.S)
    assert today and "lw.source_kind is not distinct from 'station'" in today.group(1)
    stored = re.search(r"case when (.*?)then lw\.running_max_c end\s+as stored_running_max_c", src, re.S)
    assert stored and "'station'" in stored.group(1)
    assert "h.live_source_kind" in src.split("create view v_city_running_max")[1]


def test_the_strategy_context_falls_back_only_to_a_station_maximum():
    src = (ROOT / "scripts/signal_engine.py").read_text()
    assert 'else (lw.get("running_max_c") if lw.get("source_kind") == "station"' in src
