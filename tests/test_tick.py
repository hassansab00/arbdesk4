"""The hourly tick's checkpoint writer (plan v2 P6.1 / P4.2).

A checkpoint is the ladder the engine published at a fixed moment on the
CITY'S clock. These tests hold the clock arithmetic, the due window, the
market read, the row's own constraints and the time budget to what the plan
says, against an in-memory stand-in for the database and the CLOB.
"""
import datetime as dt
import json
import pathlib

import pytest
import yaml

import probability_engine as pe
import tick

ROOT = pathlib.Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc


def at(s):
    return dt.datetime.fromisoformat(s).replace(tzinfo=UTC)


# --------------------------------------------------------------------------
# the city's clock
# --------------------------------------------------------------------------
def test_the_checkpoints_are_on_the_city_s_clock():
    t = tick.decision_times("2026-09-25", "America/New_York", 15.5)
    assert t["d1_eve"][0] == dt.datetime(2026, 9, 24, 18, 0)
    assert t["d1_eve"][1] == at("2026-09-24T22:00")          # EDT is UTC-4
    assert t["morning"][1] == at("2026-09-25T13:00")
    assert t["noon"][1] == at("2026-09-25T16:00")
    assert t["prepeak_2h"][0] == dt.datetime(2026, 9, 25, 13, 30)
    assert t["prepeak_1h"][0] == dt.datetime(2026, 9, 25, 14, 30)
    assert t["postpeak_1h"][0] == dt.datetime(2026, 9, 25, 16, 30)


def test_a_city_east_of_utc_calls_its_eve_on_the_utc_morning_before():
    t = tick.decision_times("2026-09-25", "Asia/Tokyo", 14.0)
    assert t["d1_eve"][1] == at("2026-09-24T09:00")


def test_the_clock_follows_daylight_saving():
    before = tick.decision_times("2026-11-01", "America/New_York", None)["morning"][1]
    after = tick.decision_times("2026-11-02", "America/New_York", None)["morning"][1]
    assert (before.hour, after.hour) == (14, 14)            # 09:00 EST both days
    summer = tick.decision_times("2026-10-31", "America/New_York", None)["morning"][1]
    assert summer.hour == 13                                  # 09:00 EDT


MARKETS = [{"market_id": "m1", "city_key": "nyc", "resolution_date": "2026-09-25", "unit": "F"},
           {"market_id": "m1", "city_key": "nyc", "resolution_date": "2026-09-25", "unit": "F"}]
TZ = {"nyc": "America/New_York"}
PEAK = {("nyc", 9): 15.5}


def test_a_checkpoint_is_due_for_75_minutes_after_its_time():
    due, _ = tick.due_checkpoints(at("2026-09-24T22:35"), MARKETS, TZ, PEAK, set())
    assert [(c, t, k) for c, t, k, _ in due] == [("nyc", "2026-09-25", "d1_eve")], (
        "the duplicated market row is one city-day, and one checkpoint")
    assert tick.due_checkpoints(at("2026-09-24T23:14"), MARKETS, TZ, PEAK, set())[0]
    assert not tick.due_checkpoints(at("2026-09-24T23:15"), MARKETS, TZ, PEAK, set())[0], (
        "75 minutes after is the first moment it is no longer due")
    assert not tick.due_checkpoints(at("2026-09-24T21:59"), MARKETS, TZ, PEAK, set())[0], (
        "a checkpoint is never taken before its time")


def test_a_checkpoint_already_written_is_not_due_again():
    due, _ = tick.due_checkpoints(at("2026-09-24T22:35"), MARKETS, TZ, PEAK,
                                  {("nyc", "2026-09-25", "d1_eve")})
    assert due == []


def test_no_measured_peak_skips_the_peak_checkpoints_and_says_so():
    due, notes = tick.due_checkpoints(at("2026-09-25T17:40"), MARKETS, TZ, {}, set())
    assert due == []
    assert any("no measured peak hour" in n for n in notes)
    with_peak, _ = tick.due_checkpoints(at("2026-09-25T17:40"), MARKETS, TZ, PEAK, set())
    assert [k for _, _, k, _ in with_peak] == ["prepeak_2h"], "13:40 EDT, ten minutes after 13:30"


# --------------------------------------------------------------------------
# the market beside the call
# --------------------------------------------------------------------------
def test_the_best_prices_do_not_depend_on_the_level_order():
    book = {"bids": [{"price": "0.10"}, {"price": "0.30"}, {"price": "0.20"}],
            "asks": [{"price": "0.90"}, {"price": "0.35"}, {"price": "0.50"}],
            "last_trade_price": "0.31"}
    top = tick.book_top(book)
    assert (top["bid"], top["ask"], top["last"]) == (0.30, 0.35, 0.31)
    assert tick.market_price(top) == pytest.approx(0.325)
    assert tick.market_price({"bid": None, "ask": 0.2, "last": 0.15}) == 0.15
    assert tick.market_price({"bid": None, "ask": None, "last": None}) is None


def test_books_are_matched_by_asset_id_not_position(monkeypatch):
    class R:
        def raise_for_status(self):
            pass

        def json(self):
            return [{"asset_id": "T2", "bids": [{"price": "0.2"}], "asks": [], "last_trade_price": "0.2"},
                    {"asset_id": "T1", "bids": [{"price": "0.7"}], "asks": [], "last_trade_price": "0.7"}]
    sent = {}

    def fake_post(url, **kw):
        sent["url"], sent["body"] = url, json.loads(kw["data"])
        return R()
    monkeypatch.setattr(tick, "_post", fake_post)
    got = tick.fetch_books({"b1": "T1", "b2": "T2", "b3": None})
    assert sent["url"] == tick.CLOB_BOOKS and len(sent["body"]) == 2, "one request, every token"
    assert got["b1"]["bid"] == 0.7 and got["b2"]["bid"] == 0.2


# --------------------------------------------------------------------------
# the row
# --------------------------------------------------------------------------
def _rows(probs, **extra):
    base = {"centre_c": 21.4, "sigma_c": 1.1, "observed_floor_c": None,
            "input_forecast_run": "2026-09-24T12:00:00+00:00", "pricing_eligible": True}
    base.update(extra)
    return [dict(base, band_id=b, calibrated_prob=p) for b, p in probs.items()]


LOCAL = dt.datetime(2026, 9, 24, 18, 0)


def test_a_row_carries_the_ladder_the_top_pick_and_the_market():
    rows = _rows({"b1": 0.2, "b2": 0.5, "b3": 0.3})
    books = {"b1": {"bid": 0.30, "ask": 0.34, "last": 0.3}, "b2": {"bid": 0.2, "ask": 0.24, "last": 0.2}}
    row, why = tick.build_row("nyc", "2026-09-25", "d1_eve", LOCAL, rows,
                              ["priced_from:open_meteo_forecast:2026-09-24T12:00:00+00:00"],
                              books, None, "git:abc")
    assert why is None
    assert row["top_band_id"] == "b2" and row["top_prob"] == 0.5 and row["second_prob"] == 0.3
    assert row["market_top_band_id"] == "b1", "the market favours b1 at 32c against 22c"
    assert row["model_path"] == "forecast" and row["forecast_model"] == "open_meteo_forecast"
    assert row["local_decision_time"] == "2026-09-24T18:00"
    assert row["inputs_ok"] is True and row["block_reason"] is None
    assert row["reading_at"] is None, "no floor, so no reading is claimed"


def test_a_ladder_that_does_not_sum_to_one_is_not_written():
    row, why = tick.build_row("nyc", "2026-09-25", "noon", LOCAL, _rows({"b1": 0.2, "b2": 0.5}),
                              [], {}, None, "v")
    assert row is None and "sums to 0.7" in why


def test_a_blocked_price_is_written_with_its_reason():
    rows = _rows({"b1": 0.5, "b2": 0.5}, pricing_eligible=False, pricing_block_reason="no_skill")
    row, _ = tick.build_row("nyc", "2026-09-25", "noon", LOCAL, rows, [], {}, None, "v")
    assert row["inputs_ok"] is False and row["block_reason"] == "no_skill"


@pytest.mark.parametrize("label,path,model", [
    ("nws:2026-09-24T12:00", "forecast", "nws"),
    ("model:v3:2026-09-24T12:00", "model", "arbdesk_weather_model"),
    ("trajectory:14h:nws:2026-09-24T12:00", "trajectory", "nws"),
    ("trajectory:nws:2026-09-24T12:00", "trajectory", "nws"),
    ("trajectory:09h:model:v3:2026-09-24T12:00", "trajectory+model", "arbdesk_weather_model"),
])
def test_the_path_is_read_from_what_the_engine_did(label, path, model):
    assert tick._path_and_model([f"priced_from:{label}"]) == (path, model)


def test_the_engine_names_the_forecast_it_priced_from():
    src = (ROOT / "scripts" / "probability_engine.py").read_text()
    assert 'reasons.append(f"priced_from:{forecast_label}")' in src


# --------------------------------------------------------------------------
# a whole tick against a stand-in database
# --------------------------------------------------------------------------
@pytest.fixture
def world(monkeypatch):
    w = {"written": [], "logged": [], "held": [], "delay": 0.0}
    monkeypatch.setattr(tick, "get_cities", lambda **kw: [
        {"city_key": "nyc", "timezone": "America/New_York", "unit": "F"}])
    monkeypatch.setattr(pe, "_upcoming_markets", lambda: MARKETS[:1])
    monkeypatch.setattr(pe, "_bands_for_markets", lambda ids: [
        {"band_id": "b1", "market_id": "m1"}, {"band_id": "b2", "market_id": "m1"}])
    monkeypatch.setattr(pe, "_promoted_models", lambda: {})
    monkeypatch.setattr(pe, "_model_forecasts", lambda p: {})
    monkeypatch.setattr(pe, "_warm_caches", lambda: None)

    def price(city, target, unit, bands, *a):
        import time as _t
        _t.sleep(w["delay"])
        return (_rows({"b1": 0.4, "b2": 0.6}), None, ["priced_from:nws:2026-09-24T12:00"])
    monkeypatch.setattr(pe, "process_city_day", price)

    def rest(path, params=None):
        if path == "derived_weather_peak":
            return [{"city_key": "nyc", "month": 9, "peak_hour_local": 15.5}]
        if path == "v_city_running_max":
            return []
        if path == "bands":
            return [{"band_id": "b1", "token_yes": "T1"}, {"band_id": "b2", "token_yes": "T2"}]
        raise AssertionError(path)
    monkeypatch.setattr(tick, "rest", rest)
    monkeypatch.setattr(tick, "rest_all", lambda path, params, **kw: w["held"])
    monkeypatch.setattr(tick, "fetch_books", lambda tokens: {
        "b1": {"bid": 0.5, "ask": 0.52, "last": 0.5}})

    def upsert(table, rows, on_conflict):
        w["written"].append((table, rows, on_conflict))
        return len(rows)
    monkeypatch.setattr(tick, "upsert", upsert)
    monkeypatch.setattr(tick, "log_run", lambda *a: w["logged"].append(a))
    return w


def test_a_tick_writes_one_row_per_due_checkpoint(world):
    out = tick.run(now=at("2026-09-24T22:35"))
    (table, rows, conflict), = world["written"]
    assert table == "prediction_checkpoints" and conflict == tick.ON_CONFLICT
    assert [(r["city_key"], r["checkpoint"], r["top_band_id"], r["market_top_band_id"]) for r in rows] == [
        ("nyc", "d1_eve", "b2", "b1")]
    assert out["written"] == 1 and world["logged"][0][1] == "ok"


def test_a_checkpoint_this_version_already_holds_is_not_priced(world):
    world["held"] = [{"city_key": "nyc", "target_date": "2026-09-25", "checkpoint": "d1_eve"}]
    out = tick.run(now=at("2026-09-24T22:35"))
    assert world["written"] == [] and out["due"] == 0 and out["already_written"] == 1


def test_work_past_the_budget_is_deferred_and_logged_not_dropped(world, monkeypatch):
    world["delay"] = 2.5
    monkeypatch.setattr(tick.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        tick.run(now=at("2026-09-24T22:35"), budget_s=9.0)
    assert world["written"] == []
    status, detail = world["logged"][0][1], world["logged"][0][3]
    assert status == "attention" and detail["deferred"] == ["nyc 2026-09-25"]


def test_the_tick_never_asks_to_overwrite():
    """prediction_checkpoints refuses an UPDATE; merge-duplicates would turn a
    second tick in the same window into a failed batch."""
    src = (ROOT / "scripts" / "tick.py").read_text()
    assert "upsert_replace" not in src and "merge-duplicates" not in src


# --------------------------------------------------------------------------
# the workflow
# --------------------------------------------------------------------------
def _tick_yml():
    return yaml.safe_load((ROOT / ".github" / "workflows" / "tick.yml").read_text())


def test_the_tick_is_one_job_with_no_matrix():
    doc = _tick_yml()
    jobs = doc["jobs"]
    assert len(jobs) == 1, "each job bills its own whole minutes"
    job = next(iter(jobs.values()))
    assert "strategy" not in job and "matrix" not in json.dumps(job)
    assert job["timeout-minutes"] == 3
    assert doc["concurrency"] == {"group": "tick", "cancel-in-progress": False}


def test_the_tick_is_not_scheduled_until_it_has_been_timed():
    """Rule 7: the schedule, the budget constants and retiring intraday land
    together, with a measured wall time. Until then it runs by hand only."""
    triggers = _tick_yml().get(True) or _tick_yml().get("on")
    assert set(triggers) == {"workflow_dispatch"}


def test_dispatch_inputs_never_reach_the_shell_line():
    for step in next(iter(_tick_yml()["jobs"].values()))["steps"]:
        assert "inputs." not in (step.get("run") or ""), step
