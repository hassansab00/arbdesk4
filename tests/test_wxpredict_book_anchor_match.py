"""A book-anchored dump (tools/wxpredict/book_anchor.py) prices one ladder
snapshot: the book's own mid at that snapshot plus the correction of an hourly
decision at most an hour before it. The backtests matched it as an hourly
decision, so a book an hour on took the previous anchor, its stale price and
a correction up to two hours old: 263 of the 549 exploratory taker trades
(Codex on #376). Each book is judged only against its own snapshot now."""
import pathlib
import sys

import pandas as pd

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import ask_backtest as ab  # noqa: E402

T0 = 1_759_500_000 - 1_759_500_000 % ab.SNAP_S      # the start of a 10-minute window


def _books():
    b = pd.DataFrame({"band_id": ["a", "a", "a", "b", "b"],
                      "t": [T0 + 1, T0 + 120, T0 + 3590, T0 + 2, T0 + 3600 + 5]})
    b["first_in_snap"] = ab.first_in_snapshot(b)
    return b


def test_only_a_bands_first_book_in_its_window_is_the_one_priced():
    assert _books().first_in_snap.tolist() == [True, False, True, True, True]


def test_a_book_is_judged_only_against_its_own_snapshot():
    m = _books()
    m["t_dec"] = [T0, T0, T0, T0, T0]                      # every book matched to the anchor at T0
    m["hourly_decision_utc"] = T0 - 600
    kept = ab.own_snapshot(m)
    assert kept.t.tolist() == [T0 + 1, T0 + 2], \
        "a second book in the window, and books an hour on, do not take the anchor's price"


def test_the_weather_under_the_anchor_is_at_most_an_hour_before_the_book():
    m = pd.DataFrame({"band_id": ["a", "b"], "t": [T0 + 1, T0 + 1], "t_dec": [T0, T0], "first_in_snap": True,
                      "hourly_decision_utc": [T0 + 1 - ab.MAX_AGE_S, T0 - ab.MAX_AGE_S]})
    assert ab.own_snapshot(m).band_id.tolist() == ["a"]


def test_an_hourly_dump_is_matched_as_before():
    m = _books()
    m["t_dec"] = T0
    assert ab.own_snapshot(m) is m
