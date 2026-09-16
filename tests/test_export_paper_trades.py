"""The repository IS the trading archive, so the file logic has to be exact.

A closed trade never changes again, which is what makes a file the right home
for it - but only if a re-run converges instead of duplicating, and only if the
row still means something after the bands table it referenced is gone.
"""

import importlib
import json
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))


@pytest.fixture
def exporter(tmp_path, monkeypatch):
    import export_paper_trades as m
    importlib.reload(m)
    monkeypatch.setattr(m, "OUT", str(tmp_path / "paper-trades"))
    return m


def trade(tid, closed="2026-09-16T10:00:00+00:00", net="2.50", city="milan"):
    return {"trade_id": tid, "account_id": "acct", "strategy_id": "s1",
            "signal_id": 1, "city_key": city, "band_id": "band-" + tid,
            "band_label": "26°C", "resolution_date": "2026-09-16",
            "side": "YES", "action": "BUY", "opened_at": "2026-09-16T09:00:00+00:00",
            "shares": "100", "avg_fill_price": "0.05", "quoted_price": "0.05",
            "slippage_paid": "0", "fee_paid": "0.25", "partial_fill": False,
            "requested_shares": "100", "legs_requested": 3, "closed_at": closed,
            "close_price": "0.08", "close_reason": "venue_resolution_won",
            "gross_pnl": "3.00", "net_pnl": net, "approved_by_user": False}


def test_a_trade_is_filed_by_the_month_it_closed_not_opened():
    """Closing is when the row stops changing. Filing by opened_at would let a
    position opened in one month and resolved in the next rewrite a file that
    was already published."""
    import export_paper_trades as m
    assert m.month_of({"closed_at": "2026-10-01T00:30:00+00:00"}) == "2026-10"


def test_running_it_twice_produces_the_same_file(exporter):
    rows = [trade("a"), trade("b")]
    first, _ = exporter.write(rows)
    assert first, "nothing was written"
    body = open(os.path.join(exporter.OUT, "2026-09.jsonl")).read()

    second, _ = exporter.write(rows)
    assert second == [], "a re-run rewrote unchanged files"
    assert open(os.path.join(exporter.OUT, "2026-09.jsonl")).read() == body


def test_a_trade_arriving_late_does_not_duplicate_an_earlier_one(exporter):
    exporter.write([trade("a")])
    exporter.write([trade("a"), trade("b")])
    back = exporter.read_existing()
    assert sorted(back) == ["a", "b"], "a re-export duplicated a trade"
    lines = open(os.path.join(exporter.OUT, "2026-09.jsonl")).read().strip().split("\n")
    assert len(lines) == 2


def test_every_line_stands_on_its_own_after_the_band_is_pruned(exporter):
    """bands is pruned. The archive still has to say 'Milan 26C, 16 Sep'."""
    exporter.write([trade("a")])
    row = json.loads(open(os.path.join(exporter.OUT, "2026-09.jsonl")).readline())
    for key in ("city_key", "band_label", "resolution_date", "strategy_id",
                "opened_at", "closed_at", "avg_fill_price", "close_price", "net_pnl"):
        assert row.get(key) not in (None, ""), f"{key} is missing from the archived line"


def test_the_index_totals_what_the_files_hold(exporter):
    _, index = exporter.write([trade("a", net="2.50"), trade("b", net="-1.25"),
                               trade("c", closed="2026-10-02T10:00:00+00:00", net="4.00",
                                     city="jeddah")])
    assert index["trades"] == 3
    assert index["net_pnl"] == pytest.approx(5.25)
    assert index["wins"] == 2 and index["losses"] == 1
    assert index["months"] == ["2026-09", "2026-10"]
    assert index["cities"] == ["jeddah", "milan"]


def test_a_changed_timestamp_alone_does_not_dirty_the_repo(exporter):
    """index.json carries generated_at. If that alone counted as a change,
    every run would commit and every commit would redeploy the site."""
    exporter.write([trade("a")])
    written, _ = exporter.write([trade("a")])
    assert written == [], f"an unchanged export still rewrote {written}"


def test_the_lines_are_ordered_so_a_diff_reads_as_new_trades(exporter):
    exporter.write([trade("b", closed="2026-09-16T12:00:00+00:00"),
                    trade("a", closed="2026-09-16T10:00:00+00:00")])
    ids = [json.loads(l)["trade_id"]
           for l in open(os.path.join(exporter.OUT, "2026-09.jsonl"))]
    assert ids == ["a", "b"], "lines must be in close order, so an export appends"
