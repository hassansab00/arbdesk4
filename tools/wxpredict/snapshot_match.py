"""Which books a book-anchored decision may be judged against.

book_anchor.py prices one ladder snapshot: each band's first book in a
10-minute window, with the correction of an hourly decision at most an hour
before it. ask_backtest.py and maker_backtest.py judge such a row only against
that same book. Plain Python, so the rule is tested where numpy and pandas are
not installed (tests/test_wxpredict_book_anchor_match.py; the research tools'
own dependencies stay out of CI).
"""

SNAP_S = 600                   # book_anchor.py's ladder snapshot, 10 minutes


def first_in_snapshot(band_ids, ts):
    """For each book (band, whole seconds), whether it is its band's first in
    its 10-minute window: the one book_anchor.py prices. Read over every book,
    before any filter."""
    first = {}
    for k, t in zip(band_ids, ts):
        key = (k, int(t) // SNAP_S)
        if key not in first or t < first[key]:
            first[key] = t
    return [first[(k, int(t) // SNAP_S)] == t for k, t in zip(band_ids, ts)]


def own_snapshot_mask(ts, t_decs, t_hourlies, firsts, max_age_s):
    """For each book matched to a book-anchored row (the row's snapshot time
    t_dec, its hourly decision's time t_hourly): keep it only if it is its
    band's first book, in the row's own 10-minute snapshot, and the hourly
    decision is at most max_age_s before it."""
    return [bool(f) and int(t) // SNAP_S == int(td) // SNAP_S and t - th <= max_age_s
            for t, td, th, f in zip(ts, t_decs, t_hourlies, firsts)]
