"""The backtest prices on the book it would have had if nothing had been
pruned (plan v2 P1.6 phase 3, step 3.2, 29 Sep).

The books prune takes every snapshot that is not its band's last of the UTC
day, beyond the keep, after exporting it to data/archive/books. The backtest
asks for the newest book at or before 12:00 UTC, which is an intra-day one.
scripts/book_history.py answers book_as_of from the database and, below the
prune's cut, from the archive where its row is newer. tools/p16_step32_proof.py
proves it against the live database; this holds the rules.
"""
import csv
import datetime as dt
import gzip
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import book_history as bh  # noqa: E402
import weather_history as wh  # noqa: E402

COLUMNS = ["band_id", "observed_at", "snapshot_hour_utc", "market_state", "tradeable", "best_bid",
           "best_ask", "mid", "spread", "bid_levels", "ask_levels", "ask_usd_1c", "bid_usd_1c",
           "ask_usd_2c", "bid_usd_2c", "ask_usd_5c", "bid_usd_5c", "ask_usd_10c", "bid_usd_10c",
           "ask_usd_25c", "bid_usd_25c", "ask_total_usd", "bid_total_usd", "band_volume",
           "band_volume_24hr", "raw_book", "no_best_bid", "no_best_ask", "no_book"]
NOON = dt.datetime(2026, 9, 20, 12, tzinfo=dt.timezone.utc)


def _utc(s):
    return dt.datetime.fromisoformat(s)


def _write(root, name, rows):
    folder = root / "data" / "archive" / "books"
    folder.mkdir(parents=True, exist_ok=True)
    with gzip.open(folder / name, "wt", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLUMNS})
    return f"data/archive/books/{name}"


def _cell(band, at, bid="0.29"):
    return {"band_id": band, "observed_at": at, "snapshot_hour_utc": "9", "market_state": "LIVE",
            "tradeable": "True", "best_bid": bid, "best_ask": "0.33", "bid_levels": "9"}


def _db(band, at, bid=0.3):
    return {"snapshot_id": 1, "band_id": band, "observed_at": at, "best_bid": bid, "best_ask": 0.33}


class _Desk:
    """book_as_of and the archive's prune log, as the reader sees them."""

    def __init__(self, rows, cut_before=None, cut_file=None):
        self.rows, self.calls = rows, []
        self.log = ([{"finished_at": "x", "detail": {"archived_through": f"{cut_before}T02:37:13+00:00",
                                                     "file": cut_file}}] if cut_before else [])

    def rpc(self, fn, params):
        self.calls.append((fn, params))
        assert fn == "book_as_of"
        return [r for r in self.rows if r["band_id"] in params["p_band_ids"]]

    def rest(self, path, params):
        assert path == "ingest_log" and ("job", "eq.archive_books") in params
        self.calls.append((path, None))
        return self.log


@pytest.fixture(autouse=True)
def _fresh():
    bh.reset()
    yield
    bh.reset()


@pytest.fixture
def desk(tmp_path):
    f1 = _write(tmp_path, "books-2026-09-18-to-2026-09-20.csv.gz", [
        _cell("a", "2026-09-19T23:10:00+00:00"),
        _cell("a", "2026-09-20T09:00:00.517+00:00", bid="0.31"),
        _cell("a", "2026-09-20T12:00:00+00:00", bid="0.32"),       # exactly the instant: taken
        _cell("a", "2026-09-20T12:00:00.001+00:00", bid="0.99"),   # after it: never
        _cell("c", "2026-09-20T07:00:00+00:00"),                   # the database has nothing for c
    ])
    return tmp_path, f1


def test_a_cell_comes_back_as_rest_returned_it():
    assert bh.typed("tradeable", "True") is True and bh.typed("tradeable", "False") is False
    assert bh.typed("best_bid", "0.29") == 0.29 and bh.typed("bid_levels", "9") == 9
    assert bh.typed("band_volume", "64") == 64 and isinstance(bh.typed("band_volume", "64.0"), float)
    assert bh.typed("band_volume", "") is None and bh.typed("raw_book", "") is None
    assert bh.typed("market_state", "LIVE") == "LIVE"
    assert bh.typed("observed_at", "2026-09-20T09:00:00.517+00:00") == "2026-09-20T09:00:00.517+00:00"
    assert bh.typed("raw_book", '{"asks":[{"price":0.33,"size":10}]}') == {"asks": [{"price": 0.33, "size": 10}]}
    # a file written before archive_observations._cell held a Python repr
    assert bh.typed("no_book", "{'bids': [], 'asks': None}") == {"bids": [], "asks": None}
    with pytest.raises(ValueError):
        bh.typed("tradeable", "yes")


def test_never_pruned_it_is_the_database_call_and_opens_no_file(desk, monkeypatch):
    root, _ = desk
    d = _Desk([_db("a", "2026-09-19T23:10:00+00:00")])
    monkeypatch.setattr(wh, "archive_files", lambda *a, **k: pytest.fail("opened the archive"))
    rows = bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))
    assert rows == d.rows
    assert d.calls[0] == ("book_as_of", {"p_band_ids": ["a"], "p_as_of": NOON.isoformat()})


def test_from_the_midnight_after_the_cut_the_database_answers_alone(desk, monkeypatch):
    root, f1 = desk
    d = _Desk([_db("a", "2026-09-19T23:10:00+00:00")], cut_before="2026-09-19", cut_file=f1)
    monkeypatch.setattr(wh, "archive_files", lambda *a, **k: pytest.fail("opened the archive"))
    # cut 19 Sep 02:37: a pruned row is older and not its day's last, and the
    # day's last stays - so from 20 Sep 00:00 no pruned row can be the answer
    assert bh.complete_from(wh.prune_boundary("books", rest_fn=d.rest)) == _utc("2026-09-20T00:00:00+00:00")
    assert bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root)) == d.rows


def test_below_the_cut_the_archive_answers_where_its_row_is_newer(desk):
    root, f1 = desk
    d = _Desk([_db("a", "2026-09-19T23:10:00+00:00"), _db("b", "2026-09-20T10:00:00+00:00")],
              cut_before="2026-09-20", cut_file=f1)
    got = {r["band_id"]: r for r in bh.as_of(["a", "b", "c", "a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest,
                                             root=str(root))}
    assert got["a"]["observed_at"] == "2026-09-20T12:00:00+00:00" and got["a"]["best_bid"] == 0.32
    assert got["a"]["tradeable"] is True and "snapshot_id" not in got["a"]
    assert got["b"] is d.rows[1], "the database's row is newer: it stands"
    assert got["c"]["observed_at"] == "2026-09-20T07:00:00+00:00", "no database row: the archive's"
    assert d.calls[0][1]["p_band_ids"] == ["a", "b", "c"], "one call for the ladder, each band once"
    assert bh.stats["from_archive"] == 2 and bh.stats["bands"] == 3


def test_a_tie_is_the_databases_row(desk):
    root, f1 = desk
    d = _Desk([_db("a", "2026-09-20T12:00:00+00:00", bid=0.5)], cut_before="2026-09-20", cut_file=f1)
    [row] = bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))
    assert row is d.rows[0]


def test_nothing_after_the_instant(desk):
    root, f1 = desk
    d = _Desk([], cut_before="2026-09-20", cut_file=f1)
    early = _utc("2026-09-20T08:59:59+00:00")
    [row] = [r for r in bh.as_of(["a"], early, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))]
    assert row["observed_at"] == "2026-09-19T23:10:00+00:00"
    assert bh.as_of(["a"], _utc("2026-09-19T23:09:59+00:00"), rpc_fn=d.rpc, rest_fn=d.rest,
                    root=str(root)) == []


def test_a_checkout_without_the_cuts_file_refuses(desk):
    root, _ = desk
    d = _Desk([], cut_before="2026-09-20", cut_file="data/archive/books/books-2026-09-20-to-2026-09-21.csv.gz")
    with pytest.raises(wh.StaleCheckout):
        bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))


def test_only_files_that_can_hold_a_newer_row_are_opened(desk):
    root, f1 = desk
    old = _write(root, "books-2026-09-10-to-2026-09-12.csv.gz", [_cell("a", "2026-09-11T10:00:00+00:00")])
    later = _write(root, "books-2026-09-21-to-2026-09-22.csv.gz", [_cell("a", "2026-09-21T10:00:00+00:00")])
    d = _Desk([_db("a", "2026-09-19T23:10:00+00:00")], cut_before="2026-09-21", cut_file=later)
    bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))
    assert set(bh._index) == {str(root / f1)}, "older than the database's answer, or after the instant"
    # a band the database had nothing for may be answered by any earlier file
    bh.as_of(["a", "z"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))
    assert set(bh._index) == {str(root / f1), str(root / old)}


def test_a_file_that_starts_on_the_instants_day_or_ends_on_the_answers_day_is_read(tmp_path):
    same_day = _write(tmp_path, "books-2026-09-20-to-2026-09-20.csv.gz", [_cell("a", "2026-09-20T11:00:00+00:00")])
    _write(tmp_path, "books-2026-09-15-to-2026-09-19.csv.gz", [_cell("b", "2026-09-19T23:30:00+00:00")])
    d = _Desk([_db("a", "2026-09-19T23:10:00+00:00"), _db("b", "2026-09-19T23:10:00+00:00")],
              cut_before="2026-09-20", cut_file=same_day)
    got = {r["band_id"]: r["observed_at"] for r in bh.as_of(["a", "b"], NOON, rpc_fn=d.rpc, rest_fn=d.rest,
                                                              root=str(tmp_path))}
    assert got == {"a": "2026-09-20T11:00:00+00:00", "b": "2026-09-19T23:30:00+00:00"}


def test_a_date_read_across_the_prune_is_not_banked(desk):
    root, f1 = desk
    d = _Desk([], cut_before="2026-09-20", cut_file=f1)
    bh.confirm_cut(d.rest)                              # nothing read yet: nothing to confirm
    assert d.calls == []
    bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))
    bh.confirm_cut(d.rest)                              # the same cut: the date stands
    bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))
    d.log = [{"finished_at": "y", "detail": {"archived_through": "2026-09-21T02:37:13+00:00",
                                             "file": "data/archive/books/books-2026-09-20-to-2026-09-21.csv.gz"}}]
    with pytest.raises(wh.StaleCheckout, match="Queue the run again"):
        bh.confirm_cut(d.rest)
    # the next run in the same process reads under the new cut, with its file
    _write(root, "books-2026-09-20-to-2026-09-21.csv.gz", [_cell("a", "2026-09-20T10:00:00+00:00")])
    bh.as_of(["a"], NOON, rpc_fn=d.rpc, rest_fn=d.rest, root=str(root))
    bh.confirm_cut(d.rest)


def test_the_runner_reads_every_ladder_through_it_and_confirms_before_banking():
    src = (ROOT / "scripts" / "backtest" / "runner.py").read_text()
    assert src.count("book_history.as_of(") == 1 and 'rpc("book_as_of"' not in src
    body = src[src.index("def run("):src.index("def _simulate_date(")]
    assert body.index("book_history.confirm_cut(rest)") < body.index('insert("backtest_trades"'), (
        "a date is confirmed before its trades are written")


def test_every_archive_files_name_is_its_observed_dates():
    """The reader picks files by the dates in their names."""
    for f, t, path in wh.archive_files("books", str(ROOT)):
        with gzip.open(path, "rt", newline="") as fh:
            days = {r["observed_at"][:10] for r in csv.DictReader(fh)}
        assert (min(days), max(days)) == (f, t), path
