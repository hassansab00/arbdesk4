"""Why each city-day hit or missed (tools/wxpredict/day_review.py): the facts
of the afternoon against the forecast, worded only when unusual for the city
and large, and each checkpoint's engine, market and wxrd-v2 call."""
import datetime as dt
import pathlib
import sys
from zoneinfo import ZoneInfo

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import day_review as dr  # noqa: E402
from wxpredict import features  # noqa: E402

TZ = ZoneInfo("Europe/Madrid")
DAY = dt.date(2026, 10, 14)


def report(at, tmpf="70.0", dwpf="50.0", sknt="10", sky="FEW", wx="", p01i="0.00"):
    row = {k: "" for k in ("valid", "tmpf", "t10_c", "td10_c", "dwpf", "drct", "mslp", "alti", "relh", "sknt",
                           "gust", "p01i", "vsby", "skyc1", "skyc2", "skyc3", "skyl1", "skyl2", "skyl3",
                           "wxcodes", "max6_c", "min6_c")}
    row.update(valid=at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), tmpf=tmpf, dwpf=dwpf,
               sknt=sknt, skyc1=sky, wxcodes=wx, p01i=p01i)
    return row


def day_of(hours, **kw):
    return features.Reports([report(dt.datetime.combine(DAY, dt.time(h, 30), TZ), **kw) for h in hours])


def test_the_afternoon_is_the_reports_between_09_and_17():
    obs = dr.afternoon(day_of(range(0, 24), sky="BKN", sknt="10"), DAY, TZ)
    assert obs["reports"] == 8, "09:30 to 16:30"
    assert obs["cloud_pct"] == 6 / 8 * 100 and abs(obs["wind_kmh"] - 18.52) < 1e-9
    assert obs["rain"] is False
    assert dr.afternoon(day_of(range(9, 13)), DAY, TZ) is None, "under six reports"


def test_rain_is_a_wet_code_or_measured_precipitation():
    assert dr.afternoon(day_of(range(9, 17), wx="-RA"), DAY, TZ)["rain"] is True
    assert dr.afternoon(day_of(range(9, 17), wx="BR"), DAY, TZ)["rain"] is False, "mist is not rain"
    assert dr.afternoon(day_of(range(9, 17), p01i="0.02"), DAY, TZ)["rain"] is True


def test_a_z_needs_the_citys_own_history():
    assert dr.zscore(2.0, [0.0] * 5) is None, "under MIN_HISTORY"
    hist = [(-1) ** k for k in range(40)]                      # mean 0, sd 1
    assert dr.zscore(2.0, hist) == 2.0


def test_the_largest_unusual_difference_comes_first_and_rain_is_said_when_it_disagrees():
    fc = {"cloud_pct": 10.0, "td_c": 10.0, "wind_kmh": 20.0, "rain": False}
    obs = {"cloud_pct": 70.0, "td_c": 10.5, "wind_kmh": 21.0, "rain": True}
    hist = {"cloud_pct": [(-1) ** k * 20.0 for k in range(40)], "td_c": [(-1) ** k * 1.0 for k in range(40)],
            "wind_kmh": [(-1) ** k * 5.0 for k in range(40)]}
    found = dr.surprises(fc, obs, hist)
    assert [s["what"] for s in found] == ["cloud", "dew point", "wind", "rain"]
    assert found[0]["z"] == 3.0
    line = dr.sentence(-2.0, 14.0, 14.5, found)
    assert line == "2.0 C cooler than forecast; cloudier than forecast (70 vs 10 %); rain the forecast did not have."
    assert "dew point" not in line and "humid" not in line, "z 0.5: usual for the city"


def test_an_unusual_but_tiny_difference_is_not_worded():
    found = [{"what": "dew point", "unit": "C", "forecast": 8.5, "observed": 8.4, "delta": -0.1, "z": -1.0}]
    assert dr.sentence(-1.4, None, None, found) == "1.4 C cooler than forecast."


def test_a_late_maximum_is_said():
    assert dr.sentence(0.5, 17.5, 14.0, []) == \
        "0.5 C warmer than forecast; the maximum came late (17:30 vs 14:00 usually)."
    assert dr.sentence(None, None, None, []) == "No day-ahead forecast to compare with."


def test_each_checkpoint_carries_the_three_calls_in_the_days_order():
    cp = [{"checkpoint": "noon", "winner_band_id": "b2", "top_band_id": "b1", "model_prob_on_winner": 0.3,
           "hit": False, "market_top_band_id": "b2", "market_prob_on_winner": 0.6, "market_hit": True},
          {"checkpoint": "morning", "winner_band_id": "b2", "top_band_id": "b2", "model_prob_on_winner": 0.5,
           "hit": True, "market_top_band_id": "b2", "market_prob_on_winner": 0.55, "market_hit": True}]
    wx = {"noon": {"top_band_id": "b2", "probs": {"b1": 0.2, "b2": 0.7}}}
    out = dr.calls(cp, wx)
    assert [c["checkpoint"] for c in out] == ["morning", "noon"]
    assert out[1] == {"checkpoint": "noon", "engine_top": "b1", "engine_p_win": 0.3, "engine_hit": False,
                      "market_top": "b2", "market_p_win": 0.6, "market_hit": True,
                      "wxrd_top": "b2", "wxrd_p_win": 0.7, "wxrd_hit": True,
                      "engine_label": None, "market_label": None, "wxrd_label": None}
    assert out[0]["wxrd_top"] is None and out[0]["wxrd_hit"] is None
    named = dr.calls(cp, wx, {"b1": "20°C", "b2": "21°C"})
    assert (named[1]["engine_label"], named[1]["market_label"], named[1]["wxrd_label"]) == ("20°C", "21°C", "21°C")
    assert named[0]["wxrd_label"] is None, "no wxrd row: no label"


def test_temperatures_are_worded_in_the_citys_unit():
    found = [{"what": "dew point", "unit": "C", "forecast": 10.0, "observed": 15.0, "delta": 5.0, "z": 2.0}]
    assert dr.sentence(-2.0, None, None, found, "F") == \
        "3.6 F cooler than forecast; more humid than forecast (59.0 vs 50.0 F)."
    assert dr.sentence(-2.0, None, None, found, "C") == \
        "2.0 C cooler than forecast; more humid than forecast (15.0 vs 10.0 C)."


def test_the_nightly_job_runs_it_after_the_station_data():
    steps = yaml.safe_load((ROOT / ".github" / "workflows" / "archive_observations.yml").read_text())
    steps = steps["jobs"]["archive"]["steps"]
    names = [s.get("name") for s in steps]
    i = next(k for k, s in enumerate(steps) if "day_review.py" in str(s.get("run", "")))
    assert names.index("Append the station reports and days (WXPredict)") < i < names.index("Commit the mirror")
    assert steps[i]["if"] == "always()" and steps[i]["timeout-minutes"] <= 5
