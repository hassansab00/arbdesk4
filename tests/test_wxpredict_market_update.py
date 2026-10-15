"""New venue labels enter training without rewriting the frozen input record."""
import copy
import datetime as dt
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from wxpredict import common, market_update as mu  # noqa: E402


def raw():
    return {"id": "123", "slug": "highest-temperature-in-london-on-october-13-2026",
            "title": "Highest temperature in London on October 13?", "closed": True,
            "endDate": "2026-10-13T23:00:00Z", "startDate": "2026-10-11T00:00:00Z",
            "resolutionSource": "https://www.wunderground.com/history/daily/gb/london/EGLC",
            "markets": [{"groupItemTitle": "19°C or below", "clobTokenIds": '["a","no-a"]',
                         "outcomePrices": '["0","1"]', "closed": True},
                        {"groupItemTitle": "20°C or higher", "clobTokenIds": '["b","no-b"]',
                         "outcomePrices": '["1","0"]', "closed": True}]}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    previous = common.DATA_ROOT
    common.use_root(str(tmp_path))
    monkeypatch.setattr(common, "active_cities", lambda: {"london": {}})
    try:
        e, b = mu.event_rows(raw())
        e["closed"] = "0"
        for r in b:
            r["winner"] = ""
        for name, header, rows in (("events", mu.EVENT, [e]), ("bands", mu.BAND, b), ("prices", mu.PRICE, [])):
            common.write_csv(str(pathlib.Path(common.MH) / (name + ".csv.gz")), header,
                             ([r[k] for k in header] for r in rows))
        yield tmp_path
    finally:
        common.use_root(previous)


def fetch(url, deadline):
    if "/tags/slug/" in url:
        return {"id": "tag"}
    if "/events?" in url:
        return [raw()]
    return {"history": [{"t": 1791849600, "p": .4}, {"t": 1791853200, "p": .5}]}


def test_a_settled_day_and_prices_are_added_and_a_rerun_changes_no_record(tree):
    before = {p.name: p.read_bytes() for p in pathlib.Path(common.MH).glob("*")}
    out = mu.run(dt.date(2026, 10, 15), fetch=fetch)
    assert out["events_written"] == 1 and out["series_asked"] == 2
    assert out["series_failed"] == [] and out["refused_events"] == {}
    assert {p.name: p.read_bytes() for p in pathlib.Path(common.MH).glob("*")} == before
    assert list(common.market_rows("events"))[0]["closed"] == "1"
    assert [r["winner"] for r in common.market_rows("bands")] == ["0", "1"]
    points = list(common.market_rows("prices"))
    assert len(points) == 4
    again = mu.run(dt.date(2026, 10, 15), fetch=fetch)
    assert again["series_asked"] == 0
    assert list(common.market_rows("prices")) == points


def test_an_empty_or_failed_series_is_partial_then_retried_next_night(tree):
    def one_missing(url, deadline):
        return {"history": []} if "market=b&" in url else fetch(url, deadline)
    first = mu.run(dt.date(2026, 10, 15), fetch=one_missing)
    assert first["series_failed"] == [["123", "1"]]
    second = mu.run(dt.date(2026, 10, 16), fetch=fetch)
    assert second["series_asked"] == 1 and second["series_failed"] == []
    assert len(list(common.market_rows("prices"))) == 4


@pytest.mark.parametrize("change", ["station", "token", "winner", "size"])
def test_recorded_identity_and_winners_cannot_be_changed(change):
    event, bands = mu.event_rows(raw())
    new_e, new_b = copy.deepcopy(event), copy.deepcopy(bands)
    if change == "station":
        new_e["station_icao"] = "EGLL"
    if change == "token":
        new_b[0]["token_yes"] = "different"
    if change == "winner":
        new_b[0]["winner"] = "1"
    if change == "size":
        new_b.pop()
    with pytest.raises(ValueError):
        mu.checked_update(event, bands, new_e, new_b)


def test_an_unresolved_or_incomplete_ladder_gets_no_label():
    r = raw()
    r["closed"] = False
    assert mu.event_rows(r) is None
    r = raw()
    r["markets"][0]["outcomePrices"] = '[".1",".9"]'
    assert mu.event_rows(r) is None


def test_price_validation_rejects_conflicting_points_and_out_of_range_values():
    e, b = raw(), mu.event_rows(raw())[1][0]
    for h in ([{"t": 1791849600, "p": 1.1}],
              [{"t": 1791849600, "p": .2}, {"t": 1791849600, "p": .4}]):
        with pytest.raises(ValueError):
            mu.series(e, b, 10**20, lambda *_: {"history": h})


def test_training_reads_incremental_sources_and_records_their_blobs(tree, monkeypatch):
    mu.run(dt.date(2026, 10, 15), fetch=fetch)
    monkeypatch.setattr(common, "READ", {})
    list(common.market_rows("events"))
    sources = common.sources(common.READ)
    assert "data/training/market_history/events.csv.gz" in sources
    assert "data/training/market_history_nightly/events-2026-10-15.csv.gz" in sources
