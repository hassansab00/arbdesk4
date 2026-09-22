"""A market the venue has closed is not an upcoming one, whatever its date says.

A city's market closes when its local day finishes, so at any hour a slice of
today's board has already been decided. Measured on the live database, 21 Sep:

    resolution_date   markets   closed   open
    2026-09-21             51       18     33
    2026-09-22             49        0     49

Both engines asked only `resolution_date >= today`, so those 18 markets - 198
bands - were priced on every run. SIX RUNS A DAY, and nothing could ever read
the result: v_opportunities, the only thing the strategies see, already returns
zero rows on a closed market. The rows were computed, stored, archived and
pruned unread, on a database at 95.6% of its tier.

IT ALSO MADE THE DIAGNOSTICS LIE, which is how it was found. 196 of the 218
bands blocked `stale_book` were closed markets whose books the venue had simply
stopped quoting. Read as a fraction that looks like a broken snapshot job -
19.8% of the board - and it is nothing of the kind: on live, current bands the
real figure is 22 of 902.

P0.3 paid for it too. It reported 218-306 of 1,100 bands "failed" on every run
while logging `ok`, because it was asking Polymarket for books on markets the
venue had closed - hourly, about 6,000 futile calls a day.
"""
import json
import pathlib

import edge_engine as ee
import probability_engine as pe

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _params(module, monkeypatch):
    seen = {}

    def fake_rest(table, params, **kw):
        seen["table"] = table
        seen["params"] = list(params)
        return []

    monkeypatch.setattr(module, "rest", fake_rest)
    module._upcoming_markets()
    return seen


def test_the_edge_engine_does_not_price_a_closed_market(monkeypatch):
    seen = _params(ee, monkeypatch)
    assert ("closed", "eq.false") in seen["params"], seen["params"]


def test_the_probability_engine_does_not_price_a_closed_market(monkeypatch):
    seen = _params(pe, monkeypatch)
    assert ("closed", "eq.false") in seen["params"], seen["params"]


def test_both_engines_still_bound_by_date(monkeypatch):
    # The closed filter REPLACES nothing. A market that is open but resolved
    # last week is still not upcoming, and dropping the date bound would price
    # the entire history.
    for module in (ee, pe):
        params = _params(module, monkeypatch)["params"]
        assert any(k == "resolution_date" and v.startswith("gte.") for k, v in params), params


def test_the_book_snapshot_job_does_not_ask_for_a_closed_market_s_book():
    # Same waste, one layer earlier, and this one is a request to the venue
    # rather than a row in our own database.
    doc = json.loads((ROOT / "n8n/P0.3_book_volume_snapshot.template.json").read_text())
    urls = [n.get("parameters", {}).get("url", "") for n in doc["nodes"]]
    band_query = [u for u in urls if isinstance(u, str) and "/rest/v1/bands?" in u]
    assert len(band_query) == 1, f"expected one band query, found {len(band_query)}"
    assert "markets.closed=eq.false" in band_query[0], band_query[0]
