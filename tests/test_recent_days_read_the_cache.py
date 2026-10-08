"""weather_model.recent_days anchors on whole days (Fresh Supabase, part 2a,
8 Oct): the live view for each city's days from its first whole one, the
cache (derived_city_day_features) for the days before, as
v_trajectory_evidence takes them."""
import datetime as dt
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import weather_history as wh  # noqa: E402
import weather_model as wm  # noqa: E402

TODAY = dt.date.today()


def day(k):
    return (TODAY - dt.timedelta(days=k)).isoformat()


def _rest_all(oldest, view, cache):
    def rest_all(path, params=None, order=None, page_size=1000):
        if path == "weather_observations":
            return [{"valid_at": oldest}] if oldest else []
        if path == "cities":
            return [{"city_key": "tokyo", "timezone": "Asia/Tokyo"}, {"city_key": "lima", "timezone": "America/Lima"}]
        lo = next(v for k, v in params if k == "obs_date")[4:]
        rows = {"v_city_day_features": view, "derived_city_day_features": cache}[path]
        return [dict(r) for r in rows if r["obs_date"] >= lo]
    return rest_all


@pytest.fixture(autouse=True)
def _fresh():
    wh.reset()
    yield
    wh.reset()


def _row(city, k, max_c, n=24):
    return {"city_key": city, "obs_date": day(k), "max_c": max_c, "n_obs": n}


def test_the_first_whole_day_is_the_one_after_a_cut():
    # 15:00Z is midnight in Tokyo (UTC+9): its day starts whole; 10:00 in Lima.
    t = f"{day(3)}T15:00:00+00:00"
    first = wh.first_whole_days(rest_all_fn=_rest_all(t, [], []))
    assert first == {"tokyo": day(2), "lima": day(2)}
    t = f"{day(3)}T15:00:01+00:00"
    assert wh.first_whole_days(rest_all_fn=_rest_all(t, [], []))["tokyo"] == day(1)


def test_with_every_reading_held_it_is_the_view(monkeypatch):
    view = [_row("lima", k, 20 + k) for k in range(10, -1, -1)]
    cache = [_row("lima", k, 99) for k in range(10, -1, -1)]
    monkeypatch.setattr(wm, "rest_all", _rest_all(f"{day(40)}T02:41:00+00:00", view, cache))
    got = wm.recent_days(5)
    assert got == {"lima": [r for r in view if r["obs_date"] >= day(5)]}


def test_the_cut_day_and_before_come_from_the_cache(monkeypatch):
    # cut at 02:36Z three days back: in Lima (UTC-5) that is 21:36 on day(4),
    # which the view holds only from then (it reads low); day(3) is whole
    oldest = f"{day(3)}T02:36:00+00:00"
    view = [_row("lima", 4, 11.0, n=3)] + [_row("lima", k, 20 + k) for k in (3, 2, 1, 0)]
    cache = [_row("lima", k, 30 + k) for k in range(10, 0, -1)]
    monkeypatch.setattr(wm, "rest_all", _rest_all(oldest, view, cache))
    got = wm.recent_days(5)["lima"]
    assert [(r["obs_date"], r["max_c"]) for r in got] == (
        [(day(k), 30 + k) for k in (5, 4)] + [(day(k), 20 + k) for k in (3, 2, 1, 0)])


def test_no_reading_held_is_every_day_from_the_cache(monkeypatch):
    cache = [_row("tokyo", k, 30 + k) for k in (2, 1)]
    monkeypatch.setattr(wm, "rest_all", _rest_all(None, [], cache))
    assert [r["obs_date"] for r in wm.recent_days(5)["tokyo"]] == [day(2), day(1)]
