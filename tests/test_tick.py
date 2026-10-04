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


def test_a_one_sided_book_is_held_inside_its_quote():
    """Live 26 Sep: a dead bucket offered at 0.001 with a last trade of 0.999
    was the market's favourite on 970 books. It is worth at most its ask."""
    assert tick.market_price({"bid": None, "ask": 0.001, "last": 0.999}) == 0.001
    assert tick.market_price({"bid": 0.05, "ask": None, "last": 0.01}) == 0.05
    assert tick.market_price({"bid": 0.05, "ask": None, "last": 0.3}) == 0.3
    assert tick.market_price({"bid": None, "ask": 0.3, "last": None}) is None
    assert tick.market_price({"bid": None, "ask": None, "last": 0.4}) == 0.4


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


def test_a_row_names_its_full_artifact_version_and_its_raw_forecast():
    """P2.2 (4 Oct): the prediction contract needs the label the call was
    priced from in full - model_path and forecast_model are cut from it - and
    the forecast before correction."""
    label = ("station_correction:station-correction:2026-10-04:da319e41d6+station-mos:"
             "2026-10-04:0656cd4985:open_meteo_forecast:2026-10-04T12:54:43.624+00:00")
    rows = _rows({"b1": 0.4, "b2": 0.6}, forecast_max_c=22.37)
    row, _ = tick.build_row("nyc", "2026-09-25", "d1_eve", LOCAL, rows,
                            ["measurement_layer:q_down0.0200_q_up0.0500:city", "priced_from:" + label],
                            {}, None, "git:abc")
    assert row["priced_from"] == label and row["raw_forecast_c"] == 22.37
    assert row["station"] is None, "no station given, none recorded"
    with_station, _ = tick.build_row("nyc", "2026-09-25", "d1_eve", LOCAL, rows, [], {}, None, "git:abc",
                                     station="KLGA")
    assert with_station["station"] == "KLGA"
    bare, _ = tick.build_row("nyc", "2026-09-25", "d1_eve", LOCAL, _rows({"b1": 0.4, "b2": 0.6}), [],
                             {}, None, "git:abc")
    assert bare["priced_from"] is None and bare["raw_forecast_c"] is None


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
    w = {"written": [], "logged": [], "held": [], "asked": [], "delay": 0.0, "picks": [], "running": [],
         "published": [], "publish": None, "priced": [],
         "stations": {"rows": 12, "cities": 1, "silent": [], "error": None, "seconds": 0.1}}
    monkeypatch.setattr(tick, "read_stations", lambda now, dry_run=False: dict(w["stations"]))
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
        w["priced"].append((city, str(target)))
        _t.sleep(w["delay"].get((city, str(target)), 0) if isinstance(w["delay"], dict) else w["delay"])
        probs = {str(b["band_id"]): 1 / len(bands) for b in bands} if bands else {"b1": 0.4, "b2": 0.6}
        if [b["band_id"] for b in bands] == ["b1", "b2"]:
            probs = {"b1": 0.4, "b2": 0.6}
        return (_rows(probs, computed_at="2026-09-24T22:35:05+00:00"), None,
                ["priced_from:nws:2026-09-24T12:00"])
    monkeypatch.setattr(pe, "process_city_day", price)

    def rest(path, params=None):
        if path == "derived_weather_peak":
            return [{"city_key": "nyc", "month": 9, "peak_hour_local": 15.5}]
        if path == "v_city_running_max":
            return list(w["running"])
        if path == "v_current_prediction":
            if isinstance(w["picks"], Exception):
                raise w["picks"]
            return list(w["picks"])
        if path == "bands":
            return [{"band_id": "b1", "token_yes": "T1"}, {"band_id": "b2", "token_yes": "T2"}]
        raise AssertionError(path)
    monkeypatch.setattr(tick, "rest", rest)
    def rest_all(path, params, **kw):
        w["asked"].append((path, params))
        return w["held"]
    monkeypatch.setattr(tick, "rest_all", rest_all)
    monkeypatch.setattr(tick, "fetch_books", lambda tokens: {
        "b1": {"bid": 0.5, "ask": 0.52, "last": 0.5}})

    def upsert(table, rows, on_conflict):
        w["written"].append((table, rows, on_conflict))
        return len(rows)
    monkeypatch.setattr(tick, "upsert", upsert)

    def rpc(fn, params=None, **kw):
        assert fn == "publish_current_ladders"
        w["published"].append(params["p_rows"])
        if isinstance(w["publish"], Exception):
            raise w["publish"]
        return w["publish"] or {"rows": len(params["p_rows"]), "written": len(params["p_rows"]),
                                "not_newer": 0, "refused": []}
    monkeypatch.setattr(tick, "rpc", rpc)
    monkeypatch.setattr(tick, "log_run", lambda *a: w["logged"].append(a))
    # The engine's step (P5.12 part 3a) has its own tests (test_engine_shadow);
    # here it only reports, and w["engine"] says what.
    import engine_shadow
    w["engine"] = {"strategies": 6, "written": 0, "city_days": 0}
    monkeypatch.setattr(engine_shadow, "record", lambda *a, **k: dict(w["engine"]))
    # P1.1's candidate in shadow has its own tests (test_variant_shadow); here
    # it only records what the tick handed it.
    import variant_shadow
    w["variants"] = []

    w["variant_counts"] = {"version": variant_shadow.VERSION, "due": 0, "written": 0, "skipped": {}}

    def variants(out, results, *a, **k):
        w["variants"].append((list(out), dict(results), k))
        return dict(w["variant_counts"])
    monkeypatch.setattr(variant_shadow, "record", variants)
    return w


def test_a_tick_writes_one_row_per_due_checkpoint(world):
    out = tick.run(now=at("2026-09-24T22:35"))
    (table, rows, conflict), = world["written"]
    assert table == "prediction_checkpoints" and conflict == tick.ON_CONFLICT
    assert [(r["city_key"], r["checkpoint"], r["top_band_id"], r["market_top_band_id"]) for r in rows] == [
        ("nyc", "d1_eve", "b2", "b1")]
    assert out["written"] == 1 and world["logged"][0][1] == "ok"


def test_the_variant_shadow_gets_the_rows_written_and_the_engine_s_reasons(world):
    """P1.1's candidate (docs/P11_DA_FLOOR_PREREG.md) is built beside each
    call from the row the tick writes and the pricing behind it, inside the
    tick's budget, and its counts go into the tick's log."""
    out = tick.run(now=at("2026-09-24T22:35"))
    (rows, results, kw), = world["variants"]
    assert [(r["city_key"], r["checkpoint"]) for r in rows] == [("nyc", "d1_eve")]
    assert set(results) == {("nyc", "2026-09-25")}
    assert results[("nyc", "2026-09-25")][2] == ["priced_from:nws:2026-09-24T12:00"]
    assert kw["dry_run"] is False and kw["deadline"] is not None
    assert out["variants"]["version"] == "da_floor:v1"
    assert world["logged"][0][3]["variants"]["version"] == "da_floor:v1"


@pytest.mark.parametrize("counts, status", [
    ({"error": "HTTPError: 503"}, "attention"),
    ({"skipped": {"out_of_time": 2}}, "attention"),
    ({"skipped": {"error": 1}}, "attention"),
    ({"skipped": {"no_day_ahead_call": 3}}, "ok"),     # a pre-registered exclusion
])
def test_a_lost_capture_turns_the_tick_to_attention(world, counts, status):
    """Codex on #303: the checkpoint is written, so no later tick retries
    its shadow row; a row lost to a failure or the clock must show."""
    world["variant_counts"] = dict(world["variant_counts"], **counts)
    tick.run(now=at("2026-09-24T22:35"))
    assert world["logged"][0][1] == status


def test_a_checkpoint_already_held_is_not_priced(world):
    world["held"] = [{"city_key": "nyc", "target_date": "2026-09-25", "checkpoint": "d1_eve"}]
    out = tick.run(now=at("2026-09-24T22:35"))
    assert world["written"] == [] and out["due"] == 0 and out["already_written"] == 1


def test_a_checkpoint_another_version_holds_is_not_written_again(world):
    """4 Oct: the version is the commit, and a commit between two ticks that
    share a checkpoint's window let the second write it again an hour late.
    The question asked of the table names no version."""
    world["held"] = [{"city_key": "nyc", "target_date": "2026-09-25", "checkpoint": "d1_eve"}]
    tick.run(now=at("2026-09-24T22:35"))
    (path, params), = [a for a in world["asked"] if a[0] == "prediction_checkpoints"]
    assert not any(k == "engine_version" for k, _ in params), params
    assert world["written"] == []


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


def test_the_tick_runs_hourly_at_36_on_the_n8n_clock_and_can_be_started_by_hand():
    """Scheduled only after it was timed (35 s cold, 21 s warm, 24 Sep), at
    one fixed minute an hour: :36, so a checkpoint on the hour is 36 minutes
    old when priced and a delayed run still falls inside the 75-minute grace.

    The clock is n8n's, not GitHub's: GitHub's cron started this job once in
    its first seven hourly slots (24 Sep). So tick.yml has no schedule of its
    own, and n8n/P6.1_clock.template.json dispatches it every hour."""
    import re
    triggers = _tick_yml().get(True) or _tick_yml().get("on")
    import test_github_actions as gha
    if "tick.yml" in gha.GITHUB_CRON_FALLBACK:
        # Until n8n's dispatches stop being refused (24 Sep), GitHub's cron is
        # the fallback, at the clock's own minute.
        assert set(triggers) == {"workflow_dispatch", "schedule"}
        assert triggers["schedule"] == [{"cron": "36 * * * *"}]
    else:
        assert set(triggers) == {"workflow_dispatch"}, "a GitHub cron as well would run it twice"
    clock = json.loads((pathlib.Path(__file__).resolve().parents[1] / "n8n" / "P6.1_clock.template.json").read_text())
    sched = next(n for n in clock["nodes"] if n["type"].endswith("scheduleTrigger"))
    assert sched["parameters"]["rule"]["interval"] == [
        {"field": "hours", "hoursInterval": 1, "triggerAtMinute": 36}]
    code = next(n for n in clock["nodes"] if n["name"] == "Due this hour")["parameters"]["jsCode"]
    table = json.loads(re.search(r"const CLOCK = (\[.*?\]);", code).group(1))
    assert {"file": "tick.yml", "hours_utc": "*"} in table


def test_the_deadline_leaves_the_job_inside_its_minute():
    """27 Sep 12:36Z: at 52 s the job ran 62 s (this step began 4 s in, the
    post steps and completion took 6 s). 48 + 4 + 6 = 58."""
    import re
    step = next(s for s in next(iter(_tick_yml()["jobs"].values()))["steps"] if s.get("name") == "Deadline")
    assert int(re.search(r"\+ (\d+) \)\)", step["run"]).group(1)) <= 48


def test_every_repo_file_the_tick_reads_is_checked_out():
    """The checkout is sparse. From 27 Sep 09:36Z every tick wrote no S10
    shadow row with "no fitted parameters at data/models/remaining_day/
    current.json": the file is committed, but the checkout held only scripts/."""
    import s10_shadow
    step = next(s for s in next(iter(_tick_yml()["jobs"].values()))["steps"]
                if str(s.get("uses", "")).startswith("actions/checkout"))
    paths = step["with"]["sparse-checkout"].split()
    root = pathlib.Path(__file__).resolve().parents[1]
    for path in (s10_shadow.PARAMS_PATH, s10_shadow.CHALLENGER_PATH):   # rd1, and rd3's forward shadow
        needed = pathlib.Path(path).resolve().relative_to(root).as_posix()
        assert (root / needed).exists()
        assert any(needed == p or needed.startswith(p.rstrip("/") + "/") for p in paths), needed


def test_the_forecast_archive_regime_reads_is_checked_out():
    """Every tick prices through regime.classify, whose history reads through
    weather_history: below the database's keep it reads data/archive/forecasts
    and refuses (StaleCheckout) when the newest prune's file is not there. At
    a 60-day window and a 60-day keep it reads nothing from it; at 30 it
    would refuse every hour without this path."""
    import weather_history
    step = next(s for s in next(iter(_tick_yml()["jobs"].values()))["steps"]
                if str(s.get("uses", "")).startswith("actions/checkout"))
    paths = step["with"]["sparse-checkout"].split()
    dataset = weather_history.SOURCES["weather_forecasts"]["dataset"]
    assert f"data/archive/{dataset}" in paths
    assert weather_history.archive_files(dataset), "no forecast archive file is committed"


def test_dispatch_inputs_never_reach_the_shell_line():
    for step in next(iter(_tick_yml()["jobs"].values()))["steps"]:
        assert "inputs." not in (step.get("run") or ""), step


# --------------------------------------------------------------------------
# the stations, every hour (plan v2 P6.2: n8n's P1.6 retired into the tick)
# --------------------------------------------------------------------------
def test_a_tick_logs_its_station_read_and_a_failed_read_is_attention(world):
    out = tick.run(now=at("2026-09-24T22:35"))
    assert out["observations"]["rows"] == 12 and world["logged"][0][1] == "ok"
    world["logged"].clear(); world["written"].clear()
    world["stations"]["error"] = "ReadTimeout: IEM"
    tick.run(now=at("2026-09-24T22:35"))
    assert world["logged"][0][1] == "attention", "a missed station read must not look healthy"


def test_a_failed_engine_step_is_attention_not_ok(world):
    """27 Sep 15:36Z: the engine decided 78 rows, the insert into decisions was
    refused, and the tick logged "ok" - the failure sat only inside detail.
    A step that fails inside the tick is attention."""
    tick.run(now=at("2026-09-24T22:35"))
    assert world["logged"][0][1] == "ok"
    world["logged"].clear(); world["written"].clear()
    world["engine"] = {"strategies": 6, "written": 0, "error": "HTTPError: 400 Client Error: Bad Request"}
    tick.run(now=at("2026-09-24T22:35"))
    status, detail = world["logged"][0][1], world["logged"][0][3]
    assert status == "attention" and detail["engine"]["error"].startswith("HTTPError")


def test_the_stations_are_read_even_when_no_checkpoint_is_due(world):
    world["held"] = [{"city_key": "nyc", "target_date": "2026-09-25", "checkpoint": "d1_eve"}]
    out = tick.run(now=at("2026-09-24T22:35"))
    assert out["due"] == 0 and out["observations"]["rows"] == 12


def _iem_csv(rows):
    head = "station,valid,tmpf,dwpf,relh,drct,sknt,p01i,skyc1,mslp"
    return "\n".join([head] + rows) + "\n"


def test_read_stations_is_one_request_for_the_board_over_six_hours(monkeypatch):
    import ingest_observations as io
    calls, written = [], []
    monkeypatch.setattr(tick, "get_cities", lambda **kw: [
        {"city_key": "nyc", "icao": "KLGA"}, {"city_key": "london", "icao": "EGLC"}])

    def fetch(station, start, end, since=None, until=None, timeout=300):
        calls.append((list(station), start, end, since, until, timeout))
        return _iem_csv(["LGA,2026-09-24 20:51,70.0,60.0,70,180,5,0,FEW,1015.0"])
    monkeypatch.setattr(io, "fetch_station", fetch)
    monkeypatch.setattr(tick, "upsert", lambda t, rows, k: written.append((t, rows, k)) or len(rows))
    now = at("2026-09-24T22:36")
    out = tick.read_stations(now)
    (stations, start, end, since, until, timeout), = calls
    assert stations == ["EGLC", "KLGA"], "one request carries every station"
    assert since == now - dt.timedelta(hours=6) and until == now
    assert end == dt.date(2026, 9, 25), "day2 is exclusive, so it must be tomorrow"
    assert timeout == tick.STATION_TIMEOUT_S
    (table, rows, key), = written
    assert table == "weather_observations" and key == "city_key,valid_at,source"
    assert rows[0]["city_key"] == "nyc", "a US station answers under its three-letter id"
    assert out["rows"] == 1 and out["cities"] == 1 and out["silent"] == ["london"] and out["error"] is None


def test_read_stations_never_raises(monkeypatch):
    import ingest_observations as io
    monkeypatch.setattr(tick, "get_cities", lambda **kw: [{"city_key": "nyc", "icao": "KLGA"}])

    def boom(*a, **k):
        raise TimeoutError("IEM slow")
    monkeypatch.setattr(io, "fetch_station", boom)
    out = tick.read_stations(at("2026-09-24T22:36"))
    assert out["rows"] == 0 and "TimeoutError" in out["error"]
    monkeypatch.setattr(io, "fetch_station", lambda *a, **k: "Too many requests from your IP address")
    out = tick.read_stations(at("2026-09-24T22:36"))
    assert "rate limited" in out["error"]


def test_a_dry_run_reads_but_writes_no_stations(monkeypatch):
    import ingest_observations as io
    monkeypatch.setattr(tick, "get_cities", lambda **kw: [{"city_key": "nyc", "icao": "KLGA"}])
    monkeypatch.setattr(io, "fetch_station", lambda *a, **k: _iem_csv(["LGA,2026-09-24 20:51,70.0,,,,,,,"]))
    monkeypatch.setattr(tick, "upsert", lambda *a: (_ for _ in ()).throw(AssertionError("wrote in a dry run")))
    assert tick.read_stations(at("2026-09-24T22:36"), dry_run=True)["rows"] == 1


# --------------------------------------------------------------------------
# the current prediction between pricing runs (plan v2.3 P4.9)
# --------------------------------------------------------------------------
F_BANDS = [
    {"band_id": "c-lo", "market_id": "m0", "band_lo": None, "band_hi": 70, "open_low": True, "open_high": False},
    {"band_id": "c70", "market_id": "m0", "band_lo": 70, "band_hi": 72, "open_low": False, "open_high": False},
    {"band_id": "c72", "market_id": "m0", "band_lo": 72, "band_hi": 74, "open_low": False, "open_high": False},
    {"band_id": "c-hi", "market_id": "m0", "band_lo": 74, "band_hi": None, "open_low": False, "open_high": True},
]
TODAY = {"market_id": "m0", "city_key": "nyc", "resolution_date": "2026-09-24", "unit": "F"}
C_725F = (72.5 - 32) * 5 / 9


@pytest.fixture
def same_day(world, monkeypatch):
    """nyc at 22:35Z on 24 Sep: 18:35 local. The d1_eve checkpoint for 25 Sep
    is due; today's market is not, and its pick (70-71 F, priced before any
    reading) has been passed by a 72.5 F reading."""
    monkeypatch.setattr(pe, "_upcoming_markets", lambda: [MARKETS[0], TODAY])
    monkeypatch.setattr(pe, "_bands_for_markets", lambda ids: (
        [b for b in F_BANDS if "m0" in ids]
        + ([{"band_id": "b1", "market_id": "m1"}, {"band_id": "b2", "market_id": "m1"}] if "m1" in ids else [])))
    world["running"] = [{"city_key": "nyc", "local_date": "2026-09-24", "running_max_c": C_725F,
                         "observed_max_today_c": C_725F, "live_source_kind": "station",
                         "running_max_basis": "series", "latest_reading_at": "2026-09-24T22:10:00+00:00"}]
    world["picks"] = [{"city_key": "nyc", "target_date": "2026-09-24", "market_id": "m0", "band_id": "c70",
                       "observed_floor_c": None, "source": "pricing", "priced_at": "2026-09-24T20:37:00+00:00"}]
    return world


def test_a_pick_the_station_passed_is_priced_again_and_published_beside_the_checkpoint(same_day):
    out = tick.run(now=at("2026-09-24T22:35"))
    assert same_day["priced"] == [("nyc", "2026-09-25"), ("nyc", "2026-09-24")], "the checkpoint first"
    (table, rows, _), = same_day["written"]
    assert table == "prediction_checkpoints" and [r["target_date"] for r in rows] == ["2026-09-25"], (
        "a re-price is not a checkpoint")
    (ladders,) = same_day["published"]
    assert [(r["target_date"], r["reason"], r["checkpoint"], r["market_id"]) for r in ladders] == [
        ("2026-09-24", "station_max", None, "m0"), ("2026-09-25", "checkpoint", "d1_eve", "m1")]
    assert ladders[0]["ladder"] == {"c-lo": 0.25, "c70": 0.25, "c72": 0.25, "c-hi": 0.25}
    assert ladders[0]["priced_at"] == "2026-09-24T22:35:05+00:00"
    assert ladders[0]["priced_from"] == "nws:2026-09-24T12:00"
    assert out["current"]["reprice"] == ["nyc 2026-09-24"] and out["current"]["ladders"] == 2
    assert same_day["logged"][0][1] == "ok"
    # the step's own cost is in the detail, apart from the tick's seconds
    assert isinstance(out["current"]["select_s"], float) and isinstance(out["current"]["publish_s"], float)


def test_a_pick_priced_with_that_reading_already_is_left_alone(same_day):
    same_day["picks"][0]["observed_floor_c"] = C_725F
    tick.run(now=at("2026-09-24T22:35"))
    assert same_day["priced"] == [("nyc", "2026-09-25")]
    assert [r["reason"] for r in same_day["published"][0]] == ["checkpoint"]


def test_no_current_picks_readable_is_attention_and_the_checkpoints_still_written(same_day):
    same_day["picks"] = RuntimeError("relation v_current_prediction does not exist")
    out = tick.run(now=at("2026-09-24T22:35"))
    assert [r["checkpoint"] for r in same_day["written"][0][1]] == ["d1_eve"]
    assert same_day["logged"][0][1] == "attention" and "v_current_prediction" in out["current"]["error"]


def test_a_publish_that_fails_or_is_refused_is_attention(same_day):
    same_day["publish"] = RuntimeError("HTTPError: 500")
    out = tick.run(now=at("2026-09-24T22:35"))
    assert same_day["logged"][0][1] == "attention" and out["current"]["error"].startswith("publish_current_ladders")
    same_day["logged"].clear(); same_day["written"].clear()
    same_day["publish"] = {"rows": 2, "written": 1, "not_newer": 0,
                           "refused": [{"city_key": "nyc", "target_date": "2026-09-24", "why": "x"}]}
    tick.run(now=at("2026-09-24T22:35"))
    assert same_day["logged"][0][1] == "attention"


def test_a_dry_run_publishes_nothing(same_day):
    out = tick.run(now=at("2026-09-24T22:35"), dry_run=True)
    assert same_day["published"] == [] and out["current"]["ladders"] == 2


def test_a_re_price_out_of_time_is_not_a_deferred_checkpoint(same_day, monkeypatch):
    same_day["delay"] = {("nyc", "2026-09-24"): 3.0}
    monkeypatch.setattr(tick.os, "_exit", lambda code: (_ for _ in ()).throw(SystemExit(code)))
    with pytest.raises(SystemExit):
        tick.run(now=at("2026-09-24T22:35"), budget_s=10.0)
    status, detail = same_day["logged"][0][1], same_day["logged"][0][3]
    assert detail["deferred"] == [] and detail["current"]["deferred"] == ["nyc 2026-09-24"]
    assert status == "ok", "the checkpoint was written; the re-price waits for the next hour"
    assert [r["reason"] for r in same_day["published"][0]] == ["checkpoint"]


def test_a_same_day_re_price_runs_even_when_no_checkpoint_is_due(same_day):
    same_day["held"] = [{"city_key": "nyc", "target_date": "2026-09-25", "checkpoint": "d1_eve"}]
    out = tick.run(now=at("2026-09-24T22:35"))
    assert out["due"] == 0 and same_day["priced"] == [("nyc", "2026-09-24")]
    assert same_day["written"] == [] and [r["reason"] for r in same_day["published"][0]] == ["station_max"]


def test_a_re_price_selection_that_breaks_never_costs_the_checkpoints(same_day, monkeypatch):
    import current_ladder
    monkeypatch.setattr(current_ladder, "reprice_targets", lambda *a, **k: 1 / 0)
    out = tick.run(now=at("2026-09-24T22:35"))
    assert [r["checkpoint"] for r in same_day["written"][0][1]] == ["d1_eve"]
    assert out["current"]["error"].startswith("re-price selection: ZeroDivisionError")
    assert same_day["logged"][0][1] == "attention"
    assert [r["reason"] for r in same_day["published"][0]] == ["checkpoint"]


def test_an_s10_decision_that_cannot_name_its_call_is_attention(world, monkeypatch):
    """P2.2 part 3: S10 acts on the stored row and names it. A row it cannot
    read back leaves its decisions naming no call, and that must show."""
    import s10_shadow
    monkeypatch.setattr(s10_shadow, "record", lambda *a, **k: {"due": 1, "written": 1, "unrecorded": 0})
    tick.run(now=at("2026-09-24T22:35"))
    assert world["logged"][0][1] == "ok"
    world["logged"].clear(); world["written"].clear()
    monkeypatch.setattr(s10_shadow, "record", lambda *a, **k: {"due": 1, "written": 1, "unrecorded": 1,
                                                               "read_back_error": "HTTPError"})
    tick.run(now=at("2026-09-24T22:35"))
    assert world["logged"][0][1] == "attention"
