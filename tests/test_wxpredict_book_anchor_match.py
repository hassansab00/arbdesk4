"""A book-anchored dump (tools/wxpredict/book_anchor.py) prices one ladder
snapshot: the book's own mid at that snapshot plus the correction of an hourly
decision at most an hour before it. The backtests matched it as an hourly
decision, so a book an hour on took the previous anchor, its stale price and
a correction up to two hours old: 263 of the 549 exploratory taker trades
(Codex on #376). Each book is judged only against its own snapshot now
(tools/wxpredict/snapshot_match.py, which ask_backtest.py and
maker_backtest.py call)."""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "wxpredict"))

import snapshot_match as sm  # noqa: E402

MAX_AGE_S = 3600                                     # ask_backtest.MAX_AGE_S
T0 = 1_759_500_000 - 1_759_500_000 % sm.SNAP_S       # the start of a 10-minute window

BANDS = ["a", "a", "a", "b", "b"]
TS = [T0 + 1, T0 + 120, T0 + 3590, T0 + 2, T0 + 3600 + 5]


def test_only_a_bands_first_book_in_its_window_is_the_one_priced():
    assert sm.first_in_snapshot(BANDS, TS) == [True, False, True, True, True]
    assert sm.first_in_snapshot(["a", "a"], [T0 + 300, T0 + 5]) == [False, True], "the first in time, not in order"


def test_a_book_is_judged_only_against_its_own_snapshot():
    firsts = sm.first_in_snapshot(BANDS, TS)
    keep = sm.own_snapshot_mask(TS, [T0] * 5, [T0 - 600] * 5, firsts, MAX_AGE_S)
    assert [t for t, k in zip(TS, keep) if k] == [T0 + 1, T0 + 2], \
        "a second book in the window, and books an hour on, do not take the anchor's price"


def test_the_weather_under_the_anchor_is_at_most_an_hour_before_the_book():
    keep = sm.own_snapshot_mask([T0 + 1, T0 + 1], [T0, T0], [T0 + 1 - MAX_AGE_S, T0 - MAX_AGE_S],
                                [True, True], MAX_AGE_S)
    assert keep == [True, False]
