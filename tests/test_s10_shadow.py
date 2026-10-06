"""Plan v2 P7.4 part 1: the remaining-day model in shadow at the tick's
checkpoints (scripts/s10_shadow.py). Observe only: it writes its own table and
nothing else, reads nothing after the decision hour, and never raises into the
tick."""
import datetime as dt
import random

import pytest

import remaining_day as rd
import s10_shadow as s

UTC = dt.timezone.utc
DAY = dt.date(2026, 9, 27)


def _gmt_response(tz, day, temp_of_local_hour, models=False):
    """An Open-Meteo answer in GMT covering day-1 .. day+1, as fetch_day1 asks."""
    from zoneinfo import ZoneInfo
    start = dt.datetime.combine(day - dt.timedelta(days=1), dt.time(0), tzinfo=UTC)
    times, vals = [], []
    for i in range(72):
        t = start + dt.timedelta(hours=i)
        times.append(t.strftime("%Y-%m-%dT%H:%M"))
        vals.append(temp_of_local_hour(t.astimezone(ZoneInfo(tz)).hour))
    if models:
        return {"hourly": {"time": times, **{f"temperature_2m_previous_day1_{m}": [v + k * 0.5 for v in vals]
                                             for k, m in enumerate(s.MODELS)}}}
    return {"hourly": {"time": times, "temperature_2m_previous_day1": vals,
                       "cloud_cover_previous_day1": [30.0] * 72,
                       "shortwave_radiation_previous_day1": [200.0] * 72}}


def test_the_inputs_row_is_the_citys_own_day():
    f = lambda h: 10 + h           # warmest at 23:00 local
    row = s.day1_row("tokyo", DAY, "Asia/Tokyo", _gmt_response("Asia/Tokyo", DAY, f),
                     _gmt_response("Asia/Tokyo", DAY, f, models=True))
    assert set(row["hourly"]) == {str(h) for h in range(24)}
    assert row["hourly"]["9"] == [19.0, 30.0, 200.0]
    assert row["models"]["ecmwf_ifs025"] == 33.0 and row["models"]["meteofrance_seamless"] == 36.0
    assert row["models_spread_c"] == pytest.approx(1.0, abs=1e-3)


def test_a_day_without_its_hours_is_no_row():
    assert s.day1_row("tokyo", DAY, "Asia/Tokyo", {"hourly": {"time": []}}, None) is None


def test_inputs_are_fetched_once_between_seven_and_nine_local():
    cities = [{"city_key": "london", "timezone": "Europe/London", "latitude": 51.5},
              {"city_key": "tokyo", "timezone": "Asia/Tokyo", "latitude": 35.6}]
    at = dt.datetime(2026, 9, 27, 6, 36, tzinfo=UTC)      # London 07:36, Tokyo 15:36
    assert [(c["city_key"], d.isoformat()) for c, d in s.due_fetches(at, cities, set())] == [("london", "2026-09-27")]
    assert s.due_fetches(at, cities, {("london", "2026-09-27")}) == []
    assert s.due_fetches(dt.datetime(2026, 9, 27, 5, 36, tzinfo=UTC), cities, set()) == []   # London 06:36


LADDER = ([{"band_id": "lo", "band_lo": None, "band_hi": 15, "open_low": True, "open_high": False}]
          + [{"band_id": f"b{k}", "band_lo": k, "band_hi": k + 1, "open_low": False, "open_high": False}
             for k in range(15, 30)]
          + [{"band_id": "hi", "band_lo": 30, "band_hi": None, "open_low": False, "open_high": True}])


@pytest.fixture(scope="module")
def params():
    """A small real fit, for hour 11, on synthetic days."""
    rng = random.Random(7)
    rows = []
    for c in ("london", "paris", "rome", "oslo"):
        for k in range(150):
            day = dt.date(2026, 1, 1) + dt.timedelta(days=k)
            peak = 16 + 8 * rng.random()
            fc = {h: (peak - 0.12 * (h - 15) ** 2, 40.0, 300.0) for h in range(24)}
            readings = [(h + m / 60, peak - 9 + 0.6 * (h + m / 60) + rng.gauss(0, 0.3))
                        for h in range(12) for m in (0, 30)]
            f = rd.features(readings, fc, 11, day, 1.0)
            f.update(city=c, date=day, y=max(f["R"], peak + rng.gauss(0, 0.8)))
            rows.append(f)
    old = rd.MIN_TRAIN_ROWS
    rd.MIN_TRAIN_ROWS = 100
    try:
        p = rd.fit_hour(rows)
    finally:
        rd.MIN_TRAIN_ROWS = old
    return {11: p}


def _obs(temps_by_local_hour, tz="Europe/London", day=DAY):
    from zoneinfo import ZoneInfo
    out = []
    for h, t in temps_by_local_hour:
        local = dt.datetime.combine(day, dt.time(0), tzinfo=ZoneInfo(tz)) + dt.timedelta(hours=h)
        out.append({"valid_at": local.astimezone(UTC).isoformat(), "temp_c": t})
    return out


def _run(params, obs):
    inputs = {("london", DAY.isoformat()): {
        "hourly": {str(h): [20 - 0.12 * (h - 15) ** 2, 40.0, 300.0] for h in range(24)},
        "models_spread_c": 1.2}}
    due = [("london", DAY.isoformat(), "noon", dt.datetime(2026, 9, 27, 11, 0)),
           ("london", DAY.isoformat(), "d1_eve", dt.datetime(2026, 9, 26, 18, 0)),
           ("paris", DAY.isoformat(), "morning", dt.datetime(2026, 9, 27, 9, 0))]
    return s.shadow_rows(due, params, "rd1:test", inputs, {"london": obs},
                         {"london": "Europe/London", "paris": "Europe/Paris"}, {"london": "C"},
                         {("london", DAY.isoformat()): LADDER, ("paris", DAY.isoformat()): LADDER},
                         0.02, 0.05, 1.0)


def test_a_shadow_row_is_a_whole_ladder_from_the_morning_only(params):
    morning = [(h + m / 60, 12 + 0.5 * h) for h in range(12) for m in (0, 30)]
    rows, skipped = _run(params, _obs(morning))
    assert len(rows) == 1 and skipped == {"no fit for local hour 9": 1}   # paris has no hour-9 fit here
    r = rows[0]
    assert r["checkpoint"] == "noon" and r["model_hour"] == 11 and r["contract"] == "s10-contract-v1"
    assert abs(sum(r["probs"].values()) - 1) < 1e-4 and r["top_band_id"] in r["probs"]
    assert r["q10_c"] <= r["median_c"] <= r["q90_c"] and r["running_max_c"] == pytest.approx(17.5)
    # the afternoon's readings - a 40 C spike at 14:00 - change nothing
    later, _ = _run(params, _obs(morning + [(14, 40.0), (15, 41.0)]))
    assert later[0]["probs"] == r["probs"]


def test_every_skip_is_counted_not_raised(params):
    rows, skipped = s.shadow_rows(
        [("london", DAY.isoformat(), "noon", dt.datetime(2026, 9, 27, 11, 0))], params, "v", {}, {},
        {"london": "Europe/London"}, {}, {("london", DAY.isoformat()): LADDER}, 0.02, 0.05, 1.0)
    assert rows == [] and skipped == {"no day-before inputs for the day": 1}


def test_the_committed_parameters_serve_every_hour():
    params, version, spread = s.load_params()
    assert params is not None, "data/models/remaining_day/current.json is missing"
    assert sorted(params) == list(rd.HOURS)
    assert version.startswith(rd.VERSION_PREFIX + ":") and spread > 0
    assert all(1.0 <= p["widen"] <= max(rd.WIDEN_GRID) for p in params.values())


def test_the_io_never_raises_into_the_tick(monkeypatch):
    import common

    def boom(*a, **k):
        raise RuntimeError("database down")
    monkeypatch.setattr(common, "rest_all", boom)
    at = dt.datetime(2026, 9, 27, 6, 36, tzinfo=UTC)
    out = s.fetch_inputs(at, [{"city_key": "london", "timezone": "Europe/London", "latitude": 51.5}])
    assert "error" in out
    out = s.record([("london", "2026-09-27", "noon", dt.datetime(2026, 9, 27, 11))], {}, {}, {}, {})
    assert "error" in out


def test_the_tick_calls_it_and_keeps_its_budget():
    import tick
    src = open(tick.__file__).read()
    assert "s10_shadow.fetch_inputs(now, cities, dry_run, budget_s=S10_FETCH_S)" in src
    # fetched even on a quiet hour (the early return; P4.9 added its re-prices to it)
    assert src.index("s10_shadow.fetch_inputs") < src.index("if not due and not reprice:")
    assert "s10_shadow.record(due, market_of, bands_by_market, tz_of, unit_of, dry_run," in src
    assert "ladders=s10_ladders)" in src          # the engine's S10 decisions read the same ladders
    assert src.index("s10_shadow.record(") < src.index("engine_shadow.record(")
    assert tick.S10_FETCH_S <= 10 and s.TIMEOUT_S <= 15


# ---------------------------------------------------------------------------
# Challenger C (rd3) in forward shadow (30 Sep): its own model_version, the
# same readings plus the day's models, never the engine's ladders.
# ---------------------------------------------------------------------------
MODELS7 = {m: 21.0 + 0.3 * k for k, m in enumerate(s.MODELS)}


@pytest.fixture(scope="module")
def challenger():
    """A small real rd3 fit for hour 11 on synthetic days, with a bias table."""
    rng = random.Random(11)
    rows = []
    bias, pooled = {("gfs_seamless", "london"): 1.0}, {"gfs_seamless": 0.5}
    for c in ("london", "paris", "rome", "oslo"):
        for k in range(150):
            day = dt.date(2026, 1, 1) + dt.timedelta(days=k)
            peak = 16 + 8 * rng.random()
            fc = {h: (peak - 0.12 * (h - 15) ** 2, 40.0, 300.0) for h in range(24)}
            readings = [(h + m / 60, peak - 9 + 0.6 * (h + m / 60) + rng.gauss(0, 0.3))
                        for h in range(12) for m in (0, 30)]
            f = rd.features(readings, fc, 11, day, 1.0)
            models = {m: peak + rng.gauss(0, 0.7) for m in s.MODELS}
            f = rd.row_c(f, models, c, bias, pooled)
            f.update(city=c, date=day, y=max(f["R"], peak + rng.gauss(0, 0.8)))
            rows.append(f)
    old = rd.MIN_TRAIN_ROWS
    rd.MIN_TRAIN_ROWS = 100
    try:
        p = rd.fit_hour(rows)
    finally:
        rd.MIN_TRAIN_ROWS = old
    return {"hours": {11: p}, "version": "rd3:test", "spread": 1.0, "bias": bias, "pooled": pooled}


def _inputs(models):
    return {("london", DAY.isoformat()): {
        "hourly": {str(h): [20 - 0.12 * (h - 15) ** 2, 40.0, 300.0] for h in range(24)},
        "models_spread_c": 1.2, "models": models}}


def test_the_challenger_row_is_its_own_version_with_the_models(challenger):
    morning = [(h + m / 60, 12 + 0.5 * h) for h in range(12) for m in (0, 30)]
    due = [("london", DAY.isoformat(), "noon", dt.datetime(2026, 9, 27, 11, 0))]
    rows, skipped = s.shadow_rows(due, challenger["hours"], challenger["version"], _inputs(MODELS7),
                                  {"london": _obs(morning)}, {"london": "Europe/London"}, {"london": "C"},
                                  {("london", DAY.isoformat()): LADDER}, 0.02, 0.05, 1.0, challenger=challenger)
    assert skipped == {} and len(rows) == 1
    r = rows[0]
    assert r["model_version"] == "rd3:test" and r["inputs"]["features"] == rd.FEATURES_C
    assert len(r["inputs"]["x"]) == len(rd.FEATURES_C)
    assert abs(sum(r["probs"].values()) - 1) < 1e-4
    # the models' summaries use the bias table: gfs at london is taken 1 C down
    raw = rd.models_features(MODELS7, r["running_max_c"], "london", {}, {})
    assert r["inputs"]["x"][-3] < round(raw[0], 4) or raw[0] == 0.0


def test_a_day_with_too_few_models_gets_no_challenger_row(challenger):
    morning = [(h + m / 60, 12 + 0.5 * h) for h in range(12) for m in (0, 30)]
    due = [("london", DAY.isoformat(), "noon", dt.datetime(2026, 9, 27, 11, 0))]
    three = dict(list(MODELS7.items())[:3])
    for models in (three, None):
        rows, skipped = s.shadow_rows(due, challenger["hours"], "rd3:test", _inputs(models),
                                      {"london": _obs(morning)}, {"london": "Europe/London"}, {"london": "C"},
                                      {("london", DAY.isoformat()): LADDER}, 0.02, 0.05, 1.0,
                                      challenger=challenger)
        assert rows == [] and skipped == {f"fewer than {rd.MIN_MODELS_C} models": 1}


def _fake_db(monkeypatch, models, upsert_fails_for=None, held=None, read_fails=False):
    """The table as PostgREST serves it: the write ignores a duplicate key
    (common.upsert), and the read-back returns what is stored. held: rows
    already stored before the tick."""
    import common
    written = []
    stored = {}
    for r in held or []:
        stored[(r["city_key"], r["target_date"], r["checkpoint"], r["model_version"])] = dict(r)
    morning = [(h + m / 60, 12 + 0.5 * h) for h in range(12) for m in (0, 30)]

    def rest_all(path, params=None, **k):
        if path == "s10_day1_inputs":
            row = dict(_inputs(models)[("london", DAY.isoformat())], city_key="london", local_date=DAY.isoformat())
            return [row]
        if path == "weather_observations":
            return [dict(o, city_key="london") for o in _obs(morning)]
        if path == "s10_shadow_checkpoints":
            if read_fails:
                raise RuntimeError("read refused")
            want = dict(params)["model_version"]
            assert want.startswith("in.("), want     # each row read back under its own version
            versions = {v.strip('"') for v in want[len("in.("):-1].split(",")}
            return [r for r in stored.values() if r["model_version"] in versions]
        raise AssertionError(path)

    def upsert(table, rows, on_conflict, **k):
        assert table == "s10_shadow_checkpoints" and on_conflict == "city_key,target_date,checkpoint,model_version"
        if upsert_fails_for and rows[0]["model_version"].startswith(upsert_fails_for):
            raise RuntimeError("refused")
        written.append(rows)
        for r in rows:
            key = (r["city_key"], r["target_date"], r["checkpoint"], r["model_version"])
            stored.setdefault(key, dict(r, checkpoint_id=f"id-{len(stored) + 1}"))
        return len(rows)
    monkeypatch.setattr(common, "rest_all", rest_all)
    monkeypatch.setattr(common, "upsert", upsert)
    return written


def _record(monkeypatch, params, challenger, models, **kw):
    monkeypatch.setattr(s, "load_params", lambda: (params, "rd1:test", 1.0))
    monkeypatch.setattr(s, "load_challenger", lambda: challenger)
    written = _fake_db(monkeypatch, models, **kw)
    ladders = {}
    out = s.record([("london", DAY.isoformat(), "noon", dt.datetime(2026, 9, 27, 11, 0))],
                   {("london", DAY.isoformat()): {"market_id": "m1"}}, {"m1": LADDER},
                   {"london": "Europe/London"}, {"london": "C"}, ladders=ladders)
    return out, written, ladders


KEY = ("london", DAY.isoformat(), "noon")


def test_the_engine_reads_rd1_only_and_rd3_is_written_on_its_own(monkeypatch, params, challenger):
    out, written, ladders = _record(monkeypatch, params, challenger, MODELS7)
    assert [[r["model_version"] for r in batch] for batch in written] == [["rd1:test"], ["rd3:test"]]
    assert out["written"] == 1 and out["challenger"]["written"] == 1 and out["challenger"]["version"] == "rd3:test"
    assert ladders == {KEY: {"probs": written[0][0]["probs"], "id": "id-1"}}, \
        "the engine's ladders are rd1's, as stored, with the row that holds them"
    assert out["unrecorded"] == 0


def test_a_held_key_keeps_its_first_ladder_and_the_engine_acts_on_it(monkeypatch, params, challenger):
    """P2.2 part 3. The write ignores a duplicate key, so a recomputed ladder
    is not stored. Until 4 Oct the engine decided on it anyway: 30 S10
    decisions (25 Sep - 1 Oct) acted on a ladder no row holds. Now it acts on
    the stored call and names it."""
    first = {b: (1.0 if b == "b18" else 0.0) for b in ("lo", "b15", "b16", "b17", "b18", "b19", "b20", "hi")}
    held = [{"checkpoint_id": "held-1", "city_key": "london", "target_date": DAY.isoformat(), "checkpoint": "noon",
             "model_version": "rd1:test", "probs": first}]
    out, written, ladders = _record(monkeypatch, params, challenger, MODELS7, held=held)
    assert written[0][0]["probs"] != first, "this tick computed a different ladder"
    assert ladders == {KEY: {"probs": first, "id": "held-1"}}
    assert out["unrecorded"] == 0


def test_a_failed_read_back_names_no_call_and_is_counted(monkeypatch, params, challenger):
    out, written, ladders = _record(monkeypatch, params, challenger, MODELS7, read_fails=True)
    assert ladders == {KEY: {"probs": written[0][0]["probs"], "id": None}}
    assert out["unrecorded"] == 1 and "read refused" in out["read_back_error"]
    assert "error" not in out, "the tick's S10 step still succeeds"


def test_a_failed_write_gives_the_engine_no_ladder(monkeypatch, params, challenger):
    """Nothing stored, nothing to act on: S10 decides NONE (no ladder) rather
    than on a ladder the record does not hold."""
    out, written, ladders = _record(monkeypatch, params, challenger, MODELS7, upsert_fails_for="rd1")
    assert ladders == {} and "RuntimeError" in out["error"]


def test_a_failing_challenger_never_touches_rd1(monkeypatch, params, challenger):
    out, written, ladders = _record(monkeypatch, params, challenger, MODELS7, upsert_fails_for="rd3")
    assert out["written"] == 1 and "error" not in out and "RuntimeError" in out["challenger"]["error"]
    assert len(ladders) == 1
    out, written, _ = _record(monkeypatch, params, None, MODELS7)
    assert out["written"] == 1 and "no rd3 parameters" in out["challenger"]["error"]
    assert [[r["model_version"] for r in b] for b in written] == [["rd1:test"]]


def test_the_committed_challenger_serves_every_hour():
    ch = s.load_challenger()
    assert ch is not None, "data/models/remaining_day/challenger_rd3.json is missing"
    assert sorted(ch["hours"]) == list(rd.HOURS)
    assert ch["version"].startswith(rd.VERSION_PREFIX_C + ":") and ch["spread"] > 0
    assert all(len(p["mu"]) == len(rd.FEATURES_C) for p in ch["hours"].values())
    assert {m for m, _ in ch["bias"]} == set(s.MODELS), "every model the inputs row stores has a bias"
    assert all(abs(v) <= rd.MODEL_BIAS_BOUND_C for v in list(ch["bias"].values()) + list(ch["pooled"].values()))
    import json
    blob = json.load(open(s.CHALLENGER_PATH))
    assert blob["features"] == rd.FEATURES_C and blob["models_column"] == "tmax_c"
    assert rd.version_of_c({int(h): p for h, p in blob["hours"].items()}, blob["bias"], blob["pooled"]) == blob["version"]


# ---------------------------------------------------------------------------
# rd1's late hours (the P.5 report, question 3; Hassan, 6 Oct: the strategies
# decide before and during each city's own peak). Madrid's post-peak fell at
# 18:xx local, an hour rd1 had no fit for, so S10 never decided it.
# ---------------------------------------------------------------------------
INCUMBENT = "rd1:2026-09-25:f5372ebb05"


def test_the_served_fit_and_its_version_are_untouched():
    """Challenger C's pre-registered forward comparison selects rd1's rows by
    this exact version (tools/fec_s10_forward.py INCUMBENT): the late hours
    live beside current.json, never in it."""
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    import fec_s10_forward as F
    params, version, _ = s.load_params()
    assert version == INCUMBENT == F.INCUMBENT
    assert rd.version_of(params) == INCUMBENT, "the stored parameters are the fit the version names"
    assert sorted(params) == list(rd.HOURS) == list(range(7, 18))


def test_the_late_fit_is_rd1_beside_the_served_one():
    params, version, _ = s.load_params()
    late, late_version = s.load_late(version)
    assert sorted(late) == list(rd.LATE_HOURS) == [18]
    assert late_version.startswith(rd.VERSION_PREFIX + ":") and late_version != INCUMBENT
    assert rd.version_of(late) == late_version
    assert s.load_late("rd1:some-other-fit") == ({}, None), "a late fit belongs to the served fit it was fitted beside"


def test_a_late_hour_never_replaces_a_served_one():
    merged, versions = s.with_late({11: "served"}, "v1", {11: "late", 18: "late18"}, "v2")
    assert merged == {11: "served", 18: "late18"} and versions == {11: "v1", 18: "v2"}
    assert s.with_late({11: "served"}, "v1", {}, None) == ({11: "served"}, {11: "v1"})


def test_the_rest_of_the_day_rule_is_unchanged_up_to_17():
    assert [rd._min_rest(h) for h in rd.HOURS] == [rd.MIN_REST_HOURS] * len(rd.HOURS)
    assert rd._min_rest(18) == 5, "at 18:00 the rest of the day is 19-23, every hour of it"
    assert rd._min_rest(19) == rd._min_rest(20) == rd.MIN_REST_HOURS, "no hour past the served ones gains a row"
    fc = {h: (20 - 0.12 * (h - 15) ** 2, 40.0, 300.0) for h in range(24)}
    readings = [(h + m / 60, 12 + 0.4 * h) for h in range(19) for m in (0, 30)]
    assert rd.features(readings, fc, 18, DAY, 1.0) is not None
    del fc[21]
    assert rd.features(readings, fc, 18, DAY, 1.0) is None, "a late hour needs every forecast hour left"


def test_madrid_at_18_is_decided_under_the_late_version(params):
    """The served hours keep their version and the late hour carries its own,
    in one tick's rows."""
    _, served, spread = s.load_params()
    late, late_version = s.load_late(served)
    merged, versions = s.with_late(dict(params), "rd1:test", late, late_version)
    inputs = {("madrid", DAY.isoformat()): {
        "hourly": {str(h): [24 - 0.12 * (h - 16) ** 2, 20.0, 300.0] for h in range(24)}, "models_spread_c": 1.0}}
    obs = [(h + m / 60, 14 + 0.6 * min(h, 16)) for h in range(19) for m in (0, 30)]
    due = [("madrid", DAY.isoformat(), "postpeak_1h", dt.datetime(2026, 9, 27, 18, 36)),
           ("madrid", DAY.isoformat(), "noon", dt.datetime(2026, 9, 27, 11, 0))]
    rows, skipped = s.shadow_rows(due, merged, versions, inputs, {"madrid": _obs(obs, "Europe/Madrid")},
                                  {"madrid": "Europe/Madrid"}, {"madrid": "C"},
                                  {("madrid", DAY.isoformat()): LADDER}, 0.02, 0.05, spread)
    assert skipped == {}
    by = {r["checkpoint"]: r for r in rows}
    assert by["postpeak_1h"]["model_hour"] == 18 and by["postpeak_1h"]["model_version"] == late_version
    assert by["noon"]["model_version"] == "rd1:test"
    assert abs(sum(by["postpeak_1h"]["probs"].values()) - 1) < 1e-4


def test_the_late_version_is_registered_as_shadow():
    import json
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    late = json.loads((root / "data" / "models" / "remaining_day" / "late_hours.json").read_text())["version"]
    sql = (root / "supabase" / "migrations" / "20261006160000_s10_late_hours_are_registered.sql").read_text()
    assert f"select 's10', '{late}', 'same day', 'shadow'," in sql
    assert INCUMBENT in sql and "Hours 7-17 keep rd1:2026-09-25:f5372ebb05" in sql
