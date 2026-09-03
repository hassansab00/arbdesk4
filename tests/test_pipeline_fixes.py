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
