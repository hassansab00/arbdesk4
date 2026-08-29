import datetime as dt

import live_weather as lw
from common import normalize_sky_condition


def test_normalize_sky_condition_basic_mapping():
    assert normalize_sky_condition("CLR") == "CLEAR"
    assert normalize_sky_condition("SKC") == "CLEAR"
    assert normalize_sky_condition("FEW") == "PARTLY_CLOUDY"
    assert normalize_sky_condition("BKN") == "CLOUDY"
    assert normalize_sky_condition("OVC") == "OVERCAST"
    assert normalize_sky_condition(None) is None


def test_normalize_sky_condition_present_weather_takes_priority():
    # present weather overrides sky cover even when sky cover alone would
    # say something calmer (BKN/CLR)
    assert normalize_sky_condition("BKN", present_weather_raw="RA") == "RAIN"
    assert normalize_sky_condition("CLR", present_weather_raw="SN") == "SNOW"


def test_compass_from_deg():
    assert lw.compass_from_deg(0) == "N"
    assert lw.compass_from_deg(90) == "E"
    assert lw.compass_from_deg(180) == "S"
    assert lw.compass_from_deg(None) is None


def test_running_stats_tracks_max_and_min():
    obs = [("2026-08-30T00:00:00Z", 20.0), ("2026-08-30T04:00:00Z", 24.0), ("2026-08-30T08:00:00Z", 22.0)]
    running_max, max_at, running_min = lw.running_stats(obs)
    assert running_max == 24.0
    assert max_at == "2026-08-30T04:00:00Z"
    assert running_min == 20.0


def test_running_stats_empty():
    assert lw.running_stats([]) == (None, None, None)


def test_trend_from_change():
    assert lw.trend_from_change(1.0) == "RISING"
    assert lw.trend_from_change(-1.0) == "FALLING"
    assert lw.trend_from_change(0.05) == "FLAT"
    assert lw.trend_from_change(None) is None


def test_day_decided_requires_consecutive_drop_not_one_reading():
    # A single dip is a passing cloud, not the day turning over.
    obs = [("t1", 24.0), ("t2", 23.0), ("t3", 24.2)]  # dip then new (near) max - not decided
    assert lw.compute_day_decided(obs, running_max_c=24.2, consecutive_needed=3, min_drop_c=0.5) is False


def test_day_decided_true_after_sustained_decline():
    obs = [("t1", 24.5), ("t2", 23.5), ("t3", 23.4), ("t4", 23.2)]
    assert lw.compute_day_decided(obs, running_max_c=24.5, consecutive_needed=3, min_drop_c=0.5) is True


def test_day_decided_false_with_insufficient_history():
    obs = [("t1", 23.0)]
    assert lw.compute_day_decided(obs, running_max_c=24.0, consecutive_needed=3) is False


def test_peak_window_state_before_inside_after():
    d = dt.datetime(2026, 8, 30, 10, 0)
    assert lw.peak_window_state(d, window_start_hour=14, window_end_hour=18) == ("BEFORE", 240)
    d = dt.datetime(2026, 8, 30, 15, 0)
    assert lw.peak_window_state(d, window_start_hour=14, window_end_hour=18) == ("INSIDE", 0)
    d = dt.datetime(2026, 8, 30, 20, 0)
    assert lw.peak_window_state(d, window_start_hour=14, window_end_hour=18) == ("AFTER", None)


def test_detect_events_none_on_first_poll():
    assert lw.detect_events("paris", None, {"temp_c": 20}, {}) == []


def test_detect_events_spike_and_new_max():
    prev = {"running_max_c": 22.0, "day_decided": False, "peak_window_state": "BEFORE", "sky_condition": "CLEAR"}
    curr = {"temp_c": 25.0, "temp_change_1h": 3.0, "running_max_c": 25.0, "day_decided": False,
            "peak_window_state": "BEFORE", "sky_condition": "CLEAR"}
    events = lw.detect_events("paris", prev, curr, {"spike_c_per_hour": 2.0})
    kinds = {e["kind"] for e in events}
    assert "SPIKE" in kinds
    assert "NEW_RUNNING_MAX" in kinds


def test_detect_events_day_decided_transition():
    prev = {"running_max_c": 24.0, "day_decided": False, "peak_window_state": "INSIDE", "sky_condition": "CLEAR"}
    curr = {"temp_c": 23.0, "temp_change_1h": -0.3, "running_max_c": 24.0, "day_decided": True,
            "peak_window_state": "INSIDE", "sky_condition": "CLEAR"}
    events = lw.detect_events("paris", prev, curr, {})
    assert any(e["kind"] == "DAY_DECIDED" and e["severity"] == "critical" for e in events)


def test_detect_events_precip_start():
    prev = {"running_max_c": 24.0, "day_decided": False, "peak_window_state": "INSIDE", "sky_condition": "CLOUDY"}
    curr = {"temp_c": 22.0, "temp_change_1h": None, "running_max_c": 24.0, "day_decided": False,
            "peak_window_state": "INSIDE", "sky_condition": "RAIN"}
    events = lw.detect_events("paris", prev, curr, {})
    kinds = {e["kind"] for e in events}
    assert "CONDITION_CHANGE" in kinds
    assert "PRECIP_START" in kinds
