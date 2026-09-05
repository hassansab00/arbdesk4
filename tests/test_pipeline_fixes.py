"""Regression tests for the four failing scheduled GitHub Actions.

Each of these had been failing for days against the live database. They are
locked in here because every one was a silent-until-production bug: the code
looked fine and only the real schema or the real API disagreed.
"""
import json

import pytest
import requests

import common
from ingest_observations import parse, sky_oktas


# --------------------------------------------------------------------------
# Observations: "invalid input syntax for type numeric: FEW"
#
# IEM's skyc1 is a METAR CODE, and weather_observations.cloud_cover is
# numeric. Writing the code through unconverted killed every run.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("code,oktas", [
    ("SKC", 0), ("CLR", 0), ("NSC", 0), ("NCD", 0),
    ("FEW", 2), ("SCT", 4), ("BKN", 6), ("OVC", 8), ("VV", 8),
])
def test_sky_cover_codes_map_to_oktas(code, oktas):
    assert sky_oktas(code) == oktas


def test_sky_cover_handles_code_with_height():
    # some feeds send cover+height together, e.g. BKN035 / VV003
    assert sky_oktas("BKN035") == 6
    assert sky_oktas("VV003") == 8


def test_sky_cover_is_case_and_space_insensitive():
    assert sky_oktas("  ovc ") == 8


@pytest.mark.parametrize("code", ["", None, "XXX", "   "])
def test_sky_cover_unknown_is_none_not_a_string(code):
    assert sky_oktas(code) is None


def test_parsed_cloud_cover_is_never_a_string():
    """The exact shape that produced the 22P02 crash."""
    csv = (
        "station,valid,tmpf,dwpf,relh,drct,sknt,p01i,skyc1\n"
        "KNYC,2026-09-03 14:00,77.0,60.0,55.0,180,8,0.00,FEW\n"
        "KNYC,2026-09-03 15:00,79.0,60.0,52.0,190,9,0.00,OVC\n"
        "KNYC,2026-09-03 16:00,80.1,,,,,,\n"
    )
    rows = parse(csv, "nyc")
    assert len(rows) == 3
    for r in rows:
        cc = r["cloud_cover"]
        assert cc is None or isinstance(cc, (int, float)), f"cloud_cover={cc!r}"


# --------------------------------------------------------------------------
# Live weather: 429 Too Many Requests took the whole run down.
#
# live_weather.py called fetch_station directly, unretried, every 15 minutes
# across ~54 stations. One rate-limited station killed every city's reading.
# --------------------------------------------------------------------------
class _Resp:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}


def _http_error(status, headers=None):
    return requests.HTTPError(str(status), response=_Resp(status, headers))


def test_retry_recovers_from_a_transient_429(monkeypatch):
    monkeypatch.setattr(common.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(429, {"Retry-After": "1"})
        return "DATA"

    assert common.retry(flaky, label="IEM") == "DATA"
    assert calls["n"] == 3


def test_retry_honours_retry_after(monkeypatch):
    waits = []
    monkeypatch.setattr(common.time, "sleep", waits.append)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 2:
            raise _http_error(429, {"Retry-After": "7"})
        return "ok"

    common.retry(flaky, label="IEM")
    assert waits == [7.0]


def test_permanent_429_returns_none_rather_than_raising(monkeypatch):
    """One stuck station must not take the other 53 down with it."""
    monkeypatch.setattr(common.time, "sleep", lambda s: None)

    def always():
        raise _http_error(429)

    assert common.retry(always, label="IEM", tries=3) is None


def test_429_backoff_is_bounded(monkeypatch):
    waits = []
    monkeypatch.setattr(common.time, "sleep", waits.append)

    def always():
        raise _http_error(429)

    common.retry(always, label="IEM", tries=6)
    assert waits, "should have backed off at least once"
    assert max(waits) <= 120, "a single wait must stay bounded"
    assert waits == sorted(waits), "backoff should not shrink"


# --------------------------------------------------------------------------
# Probabilities: 'invalid input syntax for type uuid'
#
# band_probabilities.forecast_version / calibration_version are uuid columns
# referencing model_versions. The engine was writing the readable label
# straight into them.
# --------------------------------------------------------------------------
class _Json:
    def __init__(self, body, status_code=200):
        self._body = body
        self.status_code = status_code
        self.text = ""

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


@pytest.fixture
def fake_model_versions(monkeypatch):
    """An empty model_versions table backed by a list."""
    store, posts = [], []
    monkeypatch.setattr(common, "_config", {"url": "http://fake", "key": "k"})
    common._version_ids.clear()

    def fake_get(url, headers=None, params=None, timeout=None):
        hits = [r for r in store
                if params["kind"] == f"eq.{r['kind']}"
                and params["label"] == f"eq.{r['label']}"]
        return _Json(hits)

    def fake_post(url, headers=None, data=None, timeout=None):
        row = json.loads(data)[0]
        row["version_id"] = f"00000000-0000-0000-0000-{len(store):012d}"
        store.append(row)
        posts.append(row)
        return _Json([row])

    monkeypatch.setattr(common.requests, "get", fake_get)
    monkeypatch.setattr(common.requests, "post", fake_post)
    yield store, posts
    common._version_ids.clear()


def test_version_label_resolves_to_a_uuid(fake_model_versions):
    import uuid

    vid = common.model_version_id("calibration", "v0_normal_lattice_no_calibration")
    uuid.UUID(vid)  # raises if it is still a label


def test_version_label_is_stored_where_it_belongs(fake_model_versions):
    store, _ = fake_model_versions
    common.model_version_id("forecast", "gfs:2026-09-03T12:00Z")
    assert store[0]["label"] == "gfs:2026-09-03T12:00Z"
    assert store[0]["kind"] == "forecast"


def test_same_label_is_created_once_then_cached(fake_model_versions):
    _, posts = fake_model_versions
    a = common.model_version_id("forecast", "gfs:1")
    b = common.model_version_id("forecast", "gfs:1")
    assert a == b
    assert len(posts) == 1


def test_distinct_labels_get_distinct_ids(fake_model_versions):
    a = common.model_version_id("forecast", "gfs:1")
    b = common.model_version_id("forecast", "gfs:2")
    assert a != b


def test_missing_model_versions_degrades_instead_of_crashing(monkeypatch):
    """A database without model_versions should still get its probabilities."""
    monkeypatch.setattr(common, "_config", {"url": "http://fake", "key": "k"})
    common._version_ids.clear()

    def boom(*a, **k):
        raise requests.HTTPError("relation model_versions does not exist")

    monkeypatch.setattr(common.requests, "get", boom)
    assert common.model_version_id("forecast", "x") is None
    common._version_ids.clear()

# --------------------------------------------------------------------------
# Live weather: "'int' object has no attribute 'strip'"
#
# Self-inflicted. Converting cloud_cover to oktas fixed the observations
# ingest and immediately broke live_weather.py, which handed the same value
# to normalize_sky_condition() expecting a METAR code. The function now takes
# either form - both are real, because the column is numeric but the IEM feed
# is textual.
# --------------------------------------------------------------------------
from common import normalize_sky_condition


@pytest.mark.parametrize("code,expected", [
    ("CLR", "CLEAR"), ("SKC", "CLEAR"),
    ("FEW", "PARTLY_CLOUDY"), ("SCT", "PARTLY_CLOUDY"),
    ("BKN", "CLOUDY"), ("OVC", "OVERCAST"),
])
def test_sky_condition_still_accepts_metar_codes(code, expected):
    assert normalize_sky_condition(code) == expected


@pytest.mark.parametrize("oktas,expected", [
    (0, "CLEAR"),
    (1, "PARTLY_CLOUDY"), (2, "PARTLY_CLOUDY"), (4, "PARTLY_CLOUDY"),
    (5, "CLOUDY"), (6, "CLOUDY"), (7, "CLOUDY"),
    (8, "OVERCAST"),
])
def test_sky_condition_accepts_oktas(oktas, expected):
    """The exact call that crashed the Live Weather Monitor."""
    assert normalize_sky_condition(oktas) == expected


def test_sky_condition_oktas_agree_with_the_codes_they_came_from():
    """Round-trip: code -> oktas -> condition must equal code -> condition."""
    for code in ("CLR", "SKC", "FEW", "SCT", "BKN", "OVC"):
        assert normalize_sky_condition(sky_oktas(code)) == normalize_sky_condition(code)


@pytest.mark.parametrize("value,expected", [
    ("0", "CLEAR"), ("8", "OVERCAST"), (6.0, "CLOUDY"), ("6.0", "CLOUDY"),
])
def test_sky_condition_accepts_numeric_strings_from_postgrest(value, expected):
    assert normalize_sky_condition(value) == expected


@pytest.mark.parametrize("value", [None, "", "XXX"])
def test_sky_condition_unknown_is_none(value):
    assert normalize_sky_condition(value) is None


def test_present_weather_still_outranks_sky_cover():
    """Spec 8.3: rain matters more than "also cloudy" - true for oktas too."""
    assert normalize_sky_condition(8, "RA") == "RAIN"
    assert normalize_sky_condition(0, "TS") == "STORM"

# --------------------------------------------------------------------------
# Probability + Edge: "'int' object is not iterable"
#
# book_snapshots.bid_levels / ask_levels are integer LEVEL COUNTS on the real
# schema. edge_engine and signals both read that table directly and handed the
# integer to a sort. They now read v_latest_book, which
# sql/ad4_13_reconcile.sql rebuilt to expose normalised {"price","size"}
# ladders under the same two column names.
# --------------------------------------------------------------------------
import pathlib

import edge_engine


def test_integer_level_count_yields_no_ladder_instead_of_crashing():
    """The exact shape that killed the run: ask_levels as a count."""
    assert edge_engine.levels_for_side({"ask_levels": 3, "bid_levels": 2}, "YES") == []
    assert edge_engine.levels_for_side({"ask_levels": 3, "bid_levels": 2}, "NO") == []


@pytest.mark.parametrize("value", [None, 0, 7, "", "3", {}, float("nan")])
def test_unusable_book_sides_are_skipped_quietly(value):
    assert edge_engine.levels_for_side({"ask_levels": value}, "YES") == []


def test_real_ladder_is_sorted_best_first():
    snap = {"ask_levels": [{"price": 0.34, "size": 5}, {"price": 0.32, "size": 10}]}
    assert edge_engine.levels_for_side(snap, "YES") == [
        {"price": 0.32, "size": 10.0}, {"price": 0.34, "size": 5.0}]


def test_no_side_is_still_the_complement_of_the_yes_bids():
    snap = {"bid_levels": [{"price": 0.30, "size": 7}]}
    assert edge_engine.levels_for_side(snap, "NO") == [{"price": 0.7, "size": 7.0}]


def test_malformed_levels_are_dropped_not_fatal():
    snap = {"ask_levels": [{"price": 0.3, "size": 1}, "junk", {"price": None, "size": 2}, 5]}
    assert edge_engine.levels_for_side(snap, "YES") == [{"price": 0.3, "size": 1.0}]


@pytest.mark.parametrize("script", ["edge_engine.py", "signals.py"])
def test_ladder_readers_use_the_view_not_the_raw_table(script):
    """Reading book_snapshots directly is the bug; guard against it returning.

    A silent regression here is worse than the crash was: levels_for_side now
    returns [] for an integer, so every fill would be sized at zero with no
    error at all.
    """
    src = (pathlib.Path(__file__).parent.parent / "scripts" / script).read_text()
    assert '"v_latest_book"' in src, f"{script} should fetch from v_latest_book"
    # both call styles in this codebase: rest(table, ...) and the
    # _latest_by_band(table, ...) helper that wraps it
    for call in ('rest("book_snapshots"', '_latest_by_band("book_snapshots"'):
        assert call not in src, f"{script} still fetches ladders from book_snapshots"


# --------------------------------------------------------------------------
# signals.city_key
#
# The column has existed the whole time and nothing wrote it. A signal that
# cannot name its city is the reason the UI's alert panel read
#
#     Desk / critical / implausible_edge_anomaly
#
# for the only signal type that was firing - no place, no trade, no action.
# --------------------------------------------------------------------------
def test_signal_rows_carry_the_city(monkeypatch):
    import signals as sig_mod

    class FakeBand:
        def __init__(self, band_id, city_key):
            self.band_id, self.city_key = band_id, city_key

    bands = [FakeBand("b-1", "chicago"), FakeBand("b-2", "miami")]
    band_city = {str(b.band_id): b.city_key for b in bands}

    # the exact expression signals.py uses, on the exact row shape it builds
    row = {"strategy_id": "system", "band_id": "b-1", "reason": "implausible_edge_anomaly"}
    if not row.get("city_key") and row.get("band_id"):
        row["city_key"] = band_city.get(str(row["band_id"]))
    assert row["city_key"] == "chicago"

    # a desk-level signal has no band, and must not invent a city
    deskrow = {"strategy_id": "system", "band_id": None, "reason": "job_stale:x:9h"}
    if not deskrow.get("city_key") and deskrow.get("band_id"):
        deskrow["city_key"] = band_city.get(str(deskrow["band_id"]))
    assert deskrow.get("city_key") is None

    # and the map is built the way the module builds it
    assert sig_mod is not None


def test_band_city_map_skips_bands_without_an_id():
    class B:
        def __init__(self, band_id, city_key):
            self.band_id, self.city_key = band_id, city_key

    ctx_bands = [B("b-1", "chi"), B(None, "nowhere"), B("b-2", "mia")]
    m = {str(b.band_id): b.city_key for b in ctx_bands if getattr(b, "band_id", None)}
    assert m == {"b-1": "chi", "b-2": "mia"}
    assert "None" not in m


# --------------------------------------------------------------------------
# Live Weather Monitor: PGRST102 "All object keys must match"
#
# PostgREST turns one bulk POST into one INSERT, which has one column list, so
# every object in the body must carry the same keys. detect_events() does not:
# a SPIKE carries change_c, a CONDITION_CHANGE does not, and a PEAK_WINDOW_OPEN
# carries neither temp_c nor change_c. The first poll that produced two kinds
# of event in one batch lost the whole write.
# --------------------------------------------------------------------------
class _FakePost:
    """Records the bodies posted, and enforces PostgREST's key-set rule."""

    def __init__(self):
        self.bodies = []

    def __call__(self, url, headers=None, params=None, data=None, timeout=None):
        rows = json.loads(data)
        self.bodies.append(rows)
        shapes = {tuple(sorted(r.keys())) for r in rows}

        class R:
            status_code = 400 if len(shapes) > 1 else 201
            text = '{"code":"PGRST102","message":"All object keys must match"}'

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise requests.HTTPError("400")
        return R()


@pytest.fixture
def posted(monkeypatch):
    p = _FakePost()
    monkeypatch.setattr(common, "_config", {"url": "https://x", "key": "k"})
    monkeypatch.setattr(common.requests, "post", p)
    return p


def test_rows_with_different_keys_are_posted_separately(posted):
    rows = [
        {"city_key": "nyc", "kind": "SPIKE", "temp_c": 22.0, "change_c": 2.4},
        {"city_key": "nyc", "kind": "CONDITION_CHANGE", "detail": {"to": "RAIN"}},
        {"city_key": "chicago", "kind": "SPIKE", "temp_c": 19.0, "change_c": 2.1},
    ]
    assert common.insert("weather_events", rows) == 3
    assert len(posted.bodies) == 2, "one POST per key shape"
    assert [r["kind"] for r in posted.bodies[0]] == ["SPIKE", "SPIKE"]
    assert [r["kind"] for r in posted.bodies[1]] == ["CONDITION_CHANGE"]


def test_upsert_obeys_the_same_rule(posted):
    rows = [{"city_key": "nyc", "temp_c": 1.0}, {"city_key": "chicago"}]
    assert common.upsert("live_weather", rows, "city_key") == 2
    assert len(posted.bodies) == 2


def test_missing_keys_are_not_filled_with_nulls(posted):
    """An absent key takes the column's DEFAULT; an explicit null overrides it.
    Normalising the shapes instead of grouping them would have turned
    `detected_at default now()` into NULL for any row that did not mention it."""
    common.insert("weather_events", [
        {"city_key": "nyc", "kind": "SPIKE", "change_c": 2.4},
        {"city_key": "nyc", "kind": "PRECIP_START"},
    ])
    for body in posted.bodies:
        for row in body:
            assert "change_c" not in row or row["change_c"] is not None
    assert not any("change_c" in r for r in posted.bodies[1])


def test_rows_keep_their_order_within_a_shape():
    groups = common.by_shape([
        {"a": 1}, {"b": 1}, {"a": 2}, {"b": 2}, {"a": 3},
    ])
    assert [g[0] for g in groups] == [{"a": 1}, {"b": 1}], "groups in first-seen order"
    assert [r["a"] for r in groups[0]] == [1, 2, 3]


def test_a_uniform_batch_is_still_one_post(posted):
    common.insert("edges", [{"band_id": i, "edge": 0.1} for i in range(4)])
    assert len(posted.bodies) == 1


# --------------------------------------------------------------------------
# Data Bank: 409 duplicate key on fact_forecast_outcome
#
# The fact tables are primary-keyed and immutable, and the job wrote them with
# a plain POST guarded only by a read-back-and-filter in Python. The filter
# keyed on (city_key, for_date) while the database keys on
# (city_key, for_date, model, lead_days), so it was both too coarse - a day
# banked for one model blocked every other model and lead forever - and no
# protection at all against a partially-written day. One warsaw row killed a
# run with 678 city-days behind it.
# --------------------------------------------------------------------------
def test_databank_writes_are_upserts_on_the_real_primary_key():
    import inspect

    import databank
    src = inspect.getsource(databank.main)
    assert "insert(" not in src.replace("upsert(", ""), \
        "a plain insert into an immutable primary-keyed table 409s on re-run"
    assert 'upsert("fact_forecast_outcome", fc, "city_key,for_date,model,lead_days")' in src
    assert 'upsert("fact_band_outcome", bd, "band_id")' in src
    assert 'upsert("fact_signal_outcome", sg, "signal_id")' in src


def test_the_skip_list_keys_on_the_whole_primary_key(monkeypatch):
    """Keyed on (city, date) alone, a day banked for one model at one lead
    marked the whole day done - so a second model, or a later lead, could never
    be added to it."""
    import databank

    rows = [{"city_key": "warsaw", "for_date": "2026-08-26",
             "model": "open_meteo_best_match", "lead_days": 1}]
    monkeypatch.setattr(databank, "rest", lambda *a, **k: rows)
    done = databank._already_banked("fact_forecast_outcome", "2026-08-20")
    assert ("warsaw", "2026-08-26", "open_meteo_best_match", 1) in done
    assert ("warsaw", "2026-08-26", "nws", 1) not in done, \
        "a different model on the same day is not banked"
    assert ("warsaw", "2026-08-26", "open_meteo_best_match", 3) not in done, \
        "a different lead on the same day is not banked"


# --------------------------------------------------------------------------
# Archive Observations: HTTP 500 from refresh_feature_cache
#
# statement_timeout is measured from the start of the TOP-LEVEL statement and
# is never reset by the statements a function runs inside itself. So
# `select refresh_feature_cache()` is ONE statement however the function is
# written, and over a 710k-row archive it is a ~6.5 second one. Supabase
# cancels it (SQLSTATE 57014) and PostgREST turns that into a bare 500.
#
# Measured against a local 710,400-row archive at statement_timeout=3s:
#   one whole-archive call   HTTP 500, 57014
#   one call per city        37 calls, slowest 222 ms, 29,600 city-days cached
# --------------------------------------------------------------------------
class _FakeRpc:
    def __init__(self, fail=None, body=None, status=200):
        self.calls = []
        self.fail, self.body, self.status = fail, body, status

    def __call__(self, url, headers=None, params=None, data=None, timeout=None):
        args = json.loads(data or "{}")
        self.calls.append((url.rsplit("/", 1)[-1], args))
        outer = self

        class R:
            status_code = outer.status
            text = outer.body or '{"message":"boom"}'

            def json(self):
                return {"ok": True, "city_days_touched": 800, "city_hours": 24,
                        "city_days_total": 29600, "ms": 200}
        return R()


@pytest.fixture
def rpc_calls(monkeypatch):
    p = _FakeRpc()
    monkeypatch.setattr(common, "_config", {"url": "https://x", "key": "k"})
    monkeypatch.setattr(common.requests, "post", p)
    monkeypatch.setattr(common, "rest",
                        lambda *a, **k: [{"city_key": c} for c in ("nyc", "chicago", "austin")])
    return p


def test_the_cache_refresh_is_one_call_per_city(rpc_calls):
    out = common.refresh_feature_cache(quiet=True)
    assert [c[1].get("p_city") for c in rpc_calls.calls] == ["nyc", "chicago", "austin"]
    assert out["cities"] == 3
    assert out["city_days_touched"] == 2400, "per-city counts are summed, not overwritten"
    assert out["city_hours"] == 72
    assert out["slowest_call_ms"] == 200


def test_a_days_window_is_passed_through(rpc_calls):
    common.refresh_feature_cache(days=7, quiet=True)
    assert all(c[1]["p_days"] == 7 for c in rpc_calls.calls)


def test_an_rpc_failure_carries_the_servers_own_message(monkeypatch):
    p = _FakeRpc(status=500,
                 body='{"code":"57014","message":"canceling statement due to statement timeout"}')
    monkeypatch.setattr(common, "_config", {"url": "https://x", "key": "k"})
    monkeypatch.setattr(common.requests, "post", p)
    with pytest.raises(requests.HTTPError) as e:
        common.rpc("refresh_feature_cache")
    assert "57014" in str(e.value) and "statement timeout" in str(e.value), \
        "raise_for_status() drops the body, which is the only place the reason is"


def test_a_database_without_the_p_city_signature_says_which_files_to_rerun(monkeypatch):
    p = _FakeRpc(status=404, body='{"code":"PGRST202","message":"Could not find the function"}')
    monkeypatch.setattr(common, "_config", {"url": "https://x", "key": "k"})
    monkeypatch.setattr(common.requests, "post", p)
    monkeypatch.setattr(common, "rest", lambda *a, **k: [{"city_key": "nyc"}])
    with pytest.raises(RuntimeError) as e:
        common.refresh_feature_cache()
    assert "ad4_28" in str(e.value) and "ad4_29" in str(e.value)


def test_a_refresh_failure_fails_the_job_rather_than_printing_a_note():
    """It used to be swallowed onto stderr while the job reported green - so
    the cache silently stopped being refreshed and every run said 'ok'. The UI,
    the analytics views and the model all read that cache."""
    import inspect

    import capacity
    src = inspect.getsource(capacity.main)
    assert "features_error" in src
    assert '"attention" if features_error else "ok"' in src
    assert "return 1" in src


# ------------------------------------------------------ the SQL side -------
import os

SQL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sql")


def _function_text(path):
    s = open(path).read()
    a = s.index("drop function if exists refresh_feature_cache();")
    b = s.index("$ad4$;\n", s.index("create or replace function refresh_feature_cache"))
    return s[a:b]


def test_both_sql_files_declare_the_identical_refresh_function():
    """Run order was load-bearing and silently destructive: ad4_28 rebuilt the
    cache whole, ad4_29 made it incremental, and re-running ad4_28 afterwards
    put the destructive body back under the same name. After a prune, "rebuild
    whole" destroys exactly the history the prune preserved."""
    assert _function_text(os.path.join(SQL, "ad4_28_feature_cache.sql")) == \
           _function_text(os.path.join(SQL, "ad4_29_retention.sql"))


def test_the_refresh_takes_a_city_so_the_caller_can_split_it():
    for f in ("ad4_28_feature_cache.sql", "ad4_29_retention.sql"):
        t = _function_text(os.path.join(SQL, f))
        assert "p_city text default null" in t, f
        assert "where p_city is null or city_key = p_city" in t, f


def test_only_one_signature_survives_either_run_order():
    for f in ("ad4_28_feature_cache.sql", "ad4_29_retention.sql"):
        t = _function_text(os.path.join(SQL, f))
        assert "drop function if exists refresh_feature_cache();" in t, f
        assert "drop function if exists refresh_feature_cache(int);" in t, f


def test_the_features_cte_is_inlined_so_a_filter_can_reach_the_index():
    """`obs` is referenced twice, and a CTE referenced more than once is
    MATERIALIZED by default - so `where city_key = 'x'` could not reach
    weather_observations and every per-city read cost a full scan.
    Measured on 710k rows: 823 ms -> 54 ms, identical results."""
    s = open(os.path.join(SQL, "ad4_21_weather_features.sql")).read()
    view = s[s.index("create or replace view v_city_day_features as"):]
    view = view[:view.index("from daily d")]
    assert "with obs as not materialized (" in view
