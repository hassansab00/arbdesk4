"""An old successful HTTP response must not make a city healthy."""
import datetime as dt

import pytest

import ingest_observations as io
import station_fallback as sf
import tick

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 16, 8, 36, tzinfo=UTC)
CITIES = [{"city_key": "milan", "icao": "LIMC"}, {"city_key": "nyc", "icao": "KLGA"}]


def report(station="LIMC", valid=None, **changes):
    valid = valid or NOW - dt.timedelta(minutes=46)
    return dict(icaoId=station, metarType="METAR", obsTime=valid.timestamp(), temp=17.3,
                rawOb=f"METAR {station} {valid:%d%H%M}Z 21006KT CAVOK 17/13 Q1023",
                receiptTime="2026-10-16T07:55:00Z") | changes


def test_real_station_temperature_and_first_local_receipt_are_kept():
    rows = sf.parse([report(), report("KLGA", metarType="SPECI")], CITIES, NOW, NOW)
    assert [r["city_key"] for r in rows] == ["milan", "nyc"]
    assert rows[0]["temp_c"] == 17.3 and rows[0]["temp_f"] == 63.14
    assert rows[0]["valid_at"] == "2026-10-16T07:50:00+00:00"
    assert rows[0]["observed_at"] == NOW.isoformat()
    assert rows[0]["source"] == "AWC"
    assert "pressure_hpa" not in rows[0], "no invented weather fields"


@pytest.mark.parametrize("bad", [
    {"icaoId": "LFPG"}, {"rawOb": "METAR LFPG 160750Z 17/13"},
    {"rawOb": "METAR LIMC 160650Z 17/13"}, {"metarType": "TAF"},
    {"temp": None}, {"temp": "nan"}, {"temp": float("inf")},
    {"obsTime": "bad"}, {"obsTime": 1e100},
    {"obsTime": (NOW + dt.timedelta(minutes=1)).timestamp()},
    {"obsTime": (NOW - dt.timedelta(hours=7)).timestamp()},
])
def test_wrong_station_time_type_or_missing_temperature_cannot_refresh_a_card(bad):
    assert sf.parse([report(**bad)], CITIES, NOW, NOW) == []


def test_freshness_uses_reading_time_and_temperature_not_http_receipt():
    old = sf.parse([report(valid=NOW - dt.timedelta(hours=5))], CITIES, NOW, NOW)
    assert sf.stale_cities(CITIES, old, NOW) == ["milan", "nyc"]
    fresh = sf.parse([report()], CITIES, NOW, NOW)
    assert sf.stale_cities(CITIES, old + fresh, NOW) == ["nyc"]
    assert sf.stale_cities(CITIES, [{"city_key": "milan", "temp_c": None,
                                      "valid_at": NOW.isoformat()}], NOW) == ["milan", "nyc"]


class Response:
    status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return [report()]


def test_fallback_batches_under_provider_cap_and_keeps_partial_success(monkeypatch):
    calls = []
    cities = [{"icao": f"X{i:03}", "city_key": str(i)} for i in range(47)]

    def get(url, **kw):
        calls.append(kw)
        assert url == sf.AWC and kw["timeout"] == sf.TIMEOUT_S
        assert len(kw["params"]["ids"].split(",")) <= sf.BATCH_SIZE
        if len(calls) == 2:
            raise TimeoutError("one batch failed")
        return Response()

    monkeypatch.setattr(sf.requests, "get", get)
    _, detail = sf.fetch(cities, NOW)
    assert detail["requests"] == 3 and len(calls) == 3 and len(detail["errors"]) == 1
    assert "one batch failed" in detail["errors"][0]


def test_result_cap_is_a_failure_instead_of_silent_missing_history(monkeypatch):
    class Capped(Response):
        def json(self):
            return [report()] * sf.RESULT_LIMIT
    monkeypatch.setattr(sf.requests, "get", lambda *a, **k: Capped())
    rows, detail = sf.fetch(CITIES, NOW)
    assert not rows and "result cap" in detail["errors"][0]


@pytest.mark.parametrize("iem_fails", [False, True])
def test_tick_recovers_stale_or_failed_iem_and_does_not_forge_research_input(monkeypatch, iem_fails):
    monkeypatch.setattr(tick, "get_cities", lambda **kw: CITIES[:1])
    old = "station,valid,tmpf\nLIMC,2026-10-16 03:50,59.0\n"
    def iem(*a, **kw):
        if iem_fails:
            raise TimeoutError("IEM")
        return old
    monkeypatch.setattr(io, "fetch_station", iem)
    received = []
    monkeypatch.setattr(tick, "share_with_capture", lambda txt, when: received.append(txt))
    fallback = sf.parse([report()], CITIES[:1], NOW, NOW)
    monkeypatch.setattr(sf, "fetch", lambda cities, now: (fallback, {"requests": 1, "rows": 1, "errors": []}))
    written = []
    def write(table, rows, key):
        assert len({tuple(sorted(r)) for r in rows}) == 1, "PostgREST requires homogeneous JSON keys"
        written.extend(rows)
        return len(rows)
    monkeypatch.setattr(tick, "upsert", write)
    out = tick.read_stations(NOW)
    assert out["error"] is None and out["iem_stale"] == ["milan"] and out["stale"] == []
    assert written[-1]["source"] == "AWC" and written[-1]["temp_c"] == 17.3
    assert received == ([] if iem_fails else [old]), "only IEM is an IEM research response"


def test_a_failed_primary_write_preserves_the_fallback_write_and_reports_attention(monkeypatch):
    monkeypatch.setattr(tick, "get_cities", lambda **kw: CITIES[:1])
    monkeypatch.setattr(io, "fetch_station", lambda *a, **kw: "station,valid,tmpf\nLIMC,2026-10-16 03:50,59\n")
    fallback = sf.parse([report()], CITIES[:1], NOW, NOW)
    monkeypatch.setattr(sf, "fetch", lambda *a: (fallback, {"errors": []}))
    def write(table, rows, key):
        if rows[0]["source"] == "IEM":
            raise RuntimeError("primary write failed")
        return len(rows)
    monkeypatch.setattr(tick, "upsert", write)
    out = tick.read_stations(NOW)
    assert out["rows"] == len(fallback) and "primary write failed" in out["error"]


def test_both_stale_feeds_report_attention_and_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(tick, "get_cities", lambda **kw: CITIES[:1])
    monkeypatch.setattr(io, "fetch_station", lambda *a, **kw: "station,valid,tmpf\nLIMC,2026-10-16 03:50,59\n")
    monkeypatch.setattr(sf, "fetch", lambda *a: ([], {"errors": ["offline"]}))
    monkeypatch.setattr(tick, "upsert", lambda *a: pytest.fail("dry run wrote"))
    out = tick.read_stations(NOW, dry_run=True)
    assert out["stale"] == ["milan"] and "No fresh station report" in out["error"]
