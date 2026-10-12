"""The same-day YES pilot's entry rule (tools/wxpredict/sdyes.py,
docs/SAME_DAY_YES_PREREG.md): every level bought is at least THETA below the
probability after the fee, the ticket and the slippage cap stop the walk, the
minimum order is kept, and one entry per city-day is the first decision's
best edge."""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import sdyes  # noqa: E402


def fee(p):
    return 0.05 * p * (1 - p)


LEVELS = [{"price": 0.30, "size": 10.0}, {"price": 0.32, "size": 20.0}, {"price": 0.40, "size": 100.0}]


def test_a_level_is_bought_only_while_the_fee_leaves_theta():
    # p 0.45, theta 0.10: 0.30 + fee 0.0105 and 0.32 + fee 0.0109 pass; 0.40 + fee 0.012 does not
    shares, usd, f = sdyes.walk_yes(LEVELS, p=0.45, theta=0.10, ticket_usd=1000, min_shares=5)
    assert shares == pytest.approx(30) and usd == pytest.approx(3.0 + 6.4)
    assert f == pytest.approx(10 * fee(0.30) + 20 * fee(0.32))
    assert 0.45 - (usd + f) / shares >= 0.10
    # the fee alone can fail a level the bare price passes: 0.45 - 0.34 = 0.11 but 0.34 + fee 0.0112 leaves 0.0988
    assert sdyes.walk_yes([{"price": 0.34, "size": 50.0}], p=0.45, theta=0.10) is None


def test_the_touch_must_qualify():
    assert sdyes.walk_yes(LEVELS, p=0.35, theta=0.05) is None
    assert sdyes.walk_yes([], p=0.9, theta=0.05) is None


def test_the_ticket_and_the_slippage_cap_stop_the_walk():
    shares, usd, _ = sdyes.walk_yes(LEVELS, p=0.95, theta=0.05, ticket_usd=10.0)
    assert usd == pytest.approx(10.0) and shares == pytest.approx(10 + 20 + 0.6 / 0.40), \
        "the third level takes the last 60 cents"
    wide = [{"price": 0.10, "size": 10.0}, {"price": 0.40, "size": 100.0}]
    shares, usd, _ = sdyes.walk_yes(wide, p=0.95, theta=0.05, ticket_usd=100.0, min_shares=5)
    assert shares == pytest.approx(10) and usd == pytest.approx(1.0), "0.40 is 30 cents past the touch"


def test_below_the_minimum_order_nothing_is_bought():
    thin = [{"price": 0.30, "size": 4.0}]
    assert sdyes.walk_yes(thin, p=0.6, theta=0.05) is None
    assert sdyes.walk_yes(thin, p=0.6, theta=0.05, min_shares=1)[0] == pytest.approx(4)


def test_the_touch_stress_costs_more_but_buys_the_same():
    a = sdyes.walk_yes(LEVELS, p=0.45, theta=0.10, ticket_usd=1000)
    b = sdyes.walk_yes(LEVELS, p=0.45, theta=0.10, ticket_usd=1000, touch_penalty=0.01)
    assert b[0] == pytest.approx(a[0]) and b[1] == pytest.approx(a[1] + 10 * 0.01)


def test_one_entry_per_city_day_the_first_decisions_best_edge():
    q = [{"t": 200, "bucket": 3, "edge": 0.30}, {"t": 100, "bucket": 5, "edge": 0.06},
         {"t": 100, "bucket": 4, "edge": 0.12}, {"t": 100, "bucket": 2, "edge": 0.12}]
    assert sdyes.choose(q) == {"t": 100, "bucket": 2, "edge": 0.12}, "earliest, then largest edge, then lowest bucket"
    assert sdyes.choose([]) is None


def test_held_to_settlement():
    e = {"shares": 30.0, "usd": 9.4, "fee": 0.3}
    assert sdyes.settle(e, True) == pytest.approx(20.3)
    assert sdyes.settle(e, False) == pytest.approx(-9.7)


def test_the_candidate_is_the_frozen_one():
    """docs/SAME_DAY_YES_PREREG.md: a change to any of these is sdyes-v2 on later dates, never an edit."""
    assert sdyes.VERSION == "sdyes-v1"
    assert sdyes.CITIES == ("jeddah", "istanbul") and sdyes.WINDOW_LOCAL_H == (10, 17)
    assert (sdyes.MODEL, sdyes.THETA, sdyes.TICKET_USD, sdyes.MIN_SHARES, sdyes.MAX_SLIP, sdyes.FEE_RATE) == \
        ("p_weather", 0.10, 10.0, 5.0, 0.25, 0.05)
    assert (sdyes.LATENCY_S, sdyes.BOOK_WITHIN_S, sdyes.FIRST_FORWARD_DATE) == (60, 300, "2026-10-13")
    assert (sdyes.FIRST_LOOK_DATES, sdyes.SECOND_LOOK_DATES) == (20, 40)


def test_the_frozen_model_is_the_committed_file():
    import gzip
    import hashlib
    import json
    raw = gzip.decompress((ROOT / sdyes.MODEL_FILE).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == sdyes.MODEL_SHA256
    meta = json.loads((ROOT / "data" / "models" / "sdyes" / "sdyes-v1_remaining.json").read_text())
    assert meta["model_sha256"] == sdyes.MODEL_SHA256 and meta["fit_last"] < sdyes.FIRST_FORWARD_DATE


def test_the_freeze_names_the_values():
    doc = (ROOT / "docs" / "SAME_DAY_YES_PREREG.md").read_text()
    for v in (sdyes.VERSION, sdyes.MODEL_SHA256, "THETA 0.10", sdyes.FIRST_FORWARD_DATE):
        assert v in doc, v


# the forward decisions (WP5 step 3)
POLLS = [
    {"source": "awc", "stations": ["OEJN"], "answered_at": 1070, "status_code": 200, "error": None},
    {"source": "clob_books", "stations": ["OEJN"], "answered_at": 1050, "status_code": 200, "error": None},
    {"source": "clob_books", "stations": ["LTFM"], "answered_at": 1070, "status_code": 200, "error": None},
    {"source": "clob_books", "stations": ["OEJN", "LTFM"], "answered_at": 1080, "status_code": 200,
     "error": "answer short: 21 of 22 books"},
    {"source": "clob_books", "stations": ["OEJN", "LTFM"], "answered_at": 1125, "status_code": 200, "error": None},
    {"source": "clob_books", "stations": ["OEJN", "LTFM"], "answered_at": 1190, "status_code": 200, "error": None},
]


def test_a_decision_is_judged_on_the_first_clean_books_after_the_latency():
    assert sdyes.fresh_answer(POLLS, 1000, "OEJN") == 1125, \
        "1050 is before t + 60 s, 1070 is reports or another city, 1080 was short"
    assert sdyes.fresh_answer(POLLS, 1000, "LTFM") == 1070
    assert sdyes.fresh_answer(POLLS, 1000, "OEJN", within_s=100) is None, "no fresh book"
    assert sdyes.fresh_answer([], 1000, "OEJN") is None


BOOK = {"bid": 0.2, "ask": 0.3, "bids": [[0.2, 50]], "asks": [[0.3, 10], [0.32, 20], [0.4, 100]], "min": 5}


def test_a_focus_book_is_walked_as_recorded():
    assert sdyes.ask_levels(BOOK) == [{"price": 0.3, "size": 10.0}, {"price": 0.32, "size": 20.0},
                                      {"price": 0.4, "size": 100.0}]
    assert sdyes.ask_levels(None) == [] and sdyes.ask_levels({"asks": []}) == []
    assert sdyes.min_order(BOOK) == 5.0 and sdyes.min_order({"min": None}) == sdyes.MIN_SHARES
    assert sdyes.min_order({"min": "15"}) == 15.0


def test_the_market_control_prices_each_book_as_the_price_record():
    books = [{"bid": None, "ask": 0.02}, {"bid": 0.6, "ask": 0.7}, {"bid": 0.2, "ask": None}]
    q = sdyes.market_probs(books)
    raw = [0.01, 0.65, 0.6]
    assert q == pytest.approx([r / sum(raw) for r in raw])
    assert sdyes.market_probs([books[0], None]) is None, "a bucket without a book"
    assert sdyes.market_probs([{"bid": None, "ask": None}]) is None


def test_each_bucket_is_judged_or_given_its_reason():
    thin = dict(BOOK, min=50)
    rows = sdyes.judge(1000, [0.45, 0.45, 0.2, None, 0.45, 0.45],
                       [BOOK, thin, BOOK, BOOK, None, {"bid": 0.1, "asks": []}], theta=0.10)
    assert [r["reason"] for r in rows] == [None, "below the minimum order", "below theta", "no probability",
                                           "no book", "no ask"]
    r = rows[0]
    assert r["qualified"] and r["shares"] == pytest.approx(30) and r["touch"] == 0.3
    assert r["cost"] == pytest.approx((3.0 + 6.4 + 10 * fee(0.3) + 20 * fee(0.32)) / 30)
    assert r["edge"] == pytest.approx(0.45 - r["cost"]) and r["edge"] >= 0.10
    assert [x["bucket"] for x in rows] == list(range(6)) and all(x["t"] == 1000 for x in rows)


def _decision(t, skip=None, probs=(0.45, 0.30, 0.25), city="jeddah", date="2026-10-13"):
    return {"city": city, "date": date, "event_id": "e1", "t": t, "icao": "OEJN",
            "band_ids": ["b0", "b1", "b2"], "probs": list(probs) if probs else None, "skip": skip}


def test_the_ledger_records_every_decision_and_skip_and_one_entry_a_city_day():
    cheap = {"bid": 0.2, "ask": 0.3, "asks": [[0.3, 10], [0.32, 20]], "min": 5}
    dear = {"bid": 0.5, "ask": 0.6, "asks": [[0.6, 100]], "min": 5}
    books = {"b0": cheap, "b1": dear, "b2": dear}
    polls = [{"source": "clob_books", "stations": ["OEJN"], "answered_at": a, "status_code": 200, "error": None}
             for a in (1065, 4665)]
    decisions = [_decision(1000), _decision(4600), _decision(8200), _decision(9000, skip="no running maximum"),
                 _decision(5000, probs=None)]
    rd1 = {"b0": 0.10, "b1": 0.80, "b2": 0.10}
    rows, entries = sdyes.ledger(decisions, polls, lambda at: books, lambda c, d, t: rd1 if t >= 4000 else None)
    by_reason = {r["reason"] for r in rows if r["who"] is None}
    assert by_reason == {"no fresh book", "no running maximum"}, "8200 has no poll in 60-300 s; 5000 neither"
    cand = entries[("candidate", "jeddah", "2026-10-13")]
    assert (cand["t"], cand["band_id"]) == (1000, "b0"), "the first decision, the bucket that qualified"
    assert ("market", "jeddah", "2026-10-13") not in entries, "the market's own price leaves no edge"
    assert entries[("rd1", "jeddah", "2026-10-13")]["band_id"] == "b1" and \
        entries[("rd1", "jeddah", "2026-10-13")]["t"] == 4600, "rd1 only from its first checkpoint"
    no_rd1 = [r for r in rows if r["who"] == "rd1" and r["t"] == 1000]
    assert len(no_rd1) == 1 and no_rd1[0]["reason"] == "no rd1 checkpoint"
    assert all(r["version"] == sdyes.VERSION for r in rows)
    c = sdyes.counts(rows, entries)
    assert c["decisions"] == 5 and c["valid_dates"] == ["2026-10-13"]
    assert c["decisions_skipped"] == {"no fresh book": 2, "no running maximum": 1}
    assert c["entries"] == {"candidate": 1, "market": 0, "rd1": 1}
    assert not any(k in c for k in ("pnl", "profit", "won")), "before the first look only counts are read"


def test_a_decision_without_a_model_row_still_judges_the_controls():
    books = {"b0": {"bid": 0.1, "ask": 0.2, "asks": [[0.2, 100]], "min": 5}, "b1": None, "b2": None}
    polls = [{"source": "clob_books", "stations": ["OEJN"], "answered_at": 1100, "status_code": 200, "error": None}]
    rows, _ = sdyes.ledger([_decision(1000, probs=None)], polls, lambda at: books, lambda c, d, t: None)
    reasons = {(r["who"], r["reason"]) for r in rows if r["bucket"] is None}
    assert reasons == {("candidate", "no model row"), ("market", "no market price on every bucket"),
                       ("rd1", "no rd1 checkpoint")}
