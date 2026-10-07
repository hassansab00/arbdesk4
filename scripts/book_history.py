"""Book snapshots the database no longer holds, for the backtest (plan v2
P1.6 phase 3, step 3.2).

The backtest prices each band on its newest book at or before one instant:
12:00 UTC, `evaluation_lead_days` before the date it trades
(backtest.engine.evaluation_instant). book_as_of answers that in the
database. The archive's books prune (sql/ad4_79) takes every snapshot that is
not its band's last of the UTC day, beyond the keep, after exporting it to
data/archive/books. Its header says the backtest loses nothing by that. It
does: at 12:00 UTC the newest book is an intra-day one, and once that is
pruned book_as_of answers with an older row, usually the band's closing book
of the day before.

as_of() answers book_as_of as if nothing had been pruned: the database's row
for each band, or the archive's newest row at or before the instant when that
one is newer.

  * The cut is the newest logged books prune (weather_history.prune_boundary:
    ingest_log, job archive_books, `archived_through`). A pruned row is older
    than its cut and is not the last of its UTC day, and the last row of every
    band-day stays in the database (each prune logs band_days_before ==
    band_days_after). So an instant at or after the first UTC midnight past
    the cut is answered by the database alone: then as_of() IS the rpc call,
    unchanged, and opens no file (but a decided band's last ladder, below).
  * Below that, the cut's file must be in this checkout, or the read refuses
    (StaleCheckout), exactly as for forecasts.
  * Only the files that can hold a row newer than the database's answer are
    opened (by the observed_at dates in their names), once per process.
  * The database wins a tie. The archive's tied pairs (two snapshots of a
    band at one instant: 7,379 keys on 29 Sep) are identical in all 29
    columns, and the database no longer holds a tied pair (0 keys, 29 Sep).
  * Values come back as the REST API returns them: the archive stored str()
    of what rest() returned (jsonb as JSON), so numbers json.loads back to
    the same int or float, True/False to booleans, '' to None, and text stays
    text. An archived row has no snapshot_id or ladder_archived_at (the
    archive does not export them); nothing in the backtest reads either.
  * A long run can cross the nightly prune (backtest.yml has two hours at any
    hour). confirm_cut() asks the log again before the runner banks a date,
    and refuses if the cut moved while that date was being read.
  * A decided band's last book loses its ladder once data/archive/ladders
    holds it (WXPredict build 2.A, 7 Oct: prune_dead_book_detail, after the
    archive's ladders dataset). book_as_of then answers that row with
    raw_book and no_book null where it used to carry them; a lead-0 run at
    12:00 UTC reaches 147 of the 8,145 such books there were on 7 Oct.
    with_ladder() puts the ladder back from the file, by snapshot_id, so the
    backtest reads what it read before. Only a decided book (DEAD_LOSER /
    DEAD_WINNER) stamped ladder_archived_at: a trading book's ladder was
    nulled at 48 hours before this and the backtest has never read it, and a
    decided book that is not its band's last is nulled at 6 hours without an
    archive. A stamped ladder no file in this checkout holds refuses
    (StaleCheckout), as a missing cut file does.

tools/p16_step32_proof.py proves it against the live database.
"""
import csv
import datetime as dt
import gzip
import os
from bisect import bisect_right

import weather_history as wh

ROOT = wh.ROOT
DATASET = "books"
LADDERS = "ladders"
DECIDED = {"DEAD_LOSER", "DEAD_WINNER"}
TEXT = {"band_id", "observed_at", "market_state"}
BOOL = {"True": True, "False": False, "true": True, "false": False}

_index = {}                        # path -> {band_id: ([instant], [row])}
_ladders = {}                      # path -> {snapshot_id: (raw_book, no_book)}
_used = {}                         # the cut this process's reads used
stats = {"reads": 0, "bands": 0, "from_archive": 0, "files_opened": 0,   # bands: answered
         "ladders_restored": 0}


def _ts(text):
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))


def typed(name, text):
    """A CSV cell back to the value rest() returned before it was written."""
    if text is None or text == "":
        return None
    if name in TEXT:
        return text
    if name == "tradeable":
        if text not in BOOL:
            raise ValueError(f"book_history: tradeable holds {text!r}")
        return BOOL[text]
    return wh._value(text)


def _file_index(path):
    """{band_id: (sorted instants, rows)} for one archive file. Once per process."""
    if path not in _index:
        by_band = {}
        with gzip.open(path, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                row = {k: typed(k, v) for k, v in r.items()}
                by_band.setdefault(row["band_id"], []).append((_ts(row["observed_at"]), row))
        index = {}
        for band, pairs in by_band.items():
            # A stable sort keeps a tied pair in file order (snapshot_id order).
            pairs.sort(key=lambda p: p[0])
            index[band] = ([p[0] for p in pairs], [p[1] for p in pairs])
        _index[path] = index
        stats["files_opened"] += 1
    return _index[path]


def newest_in_file(path, band_id, at):
    """(instant, row) of the band's newest row at or before `at` in one file."""
    entry = _file_index(path).get(band_id)
    if not entry:
        return None
    i = bisect_right(entry[0], at)
    return (entry[0][i - 1], entry[1][i - 1]) if i else None


def _ladder_index(path):
    """{snapshot_id: (raw_book, no_book)} for one ladders file. Once per process."""
    if path not in _ladders:
        out = {}
        with gzip.open(path, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                out[int(r["snapshot_id"])] = (typed("raw_book", r["raw_book"]), typed("no_book", r["no_book"]))
        _ladders[path] = out
        stats["files_opened"] += 1
    return _ladders[path]


def with_ladder(row, root):
    """The row as book_as_of returned it before its ladder went to the
    repository: a decided band's last book, stamped and stripped, gets its
    raw_book and no_book back from data/archive/ladders. Any other row is
    returned as it is."""
    if (row.get("market_state") not in DECIDED or not row.get("ladder_archived_at")
            or row.get("raw_book") is not None or row.get("no_book") is not None):
        return row
    day = _ts(row["observed_at"]).astimezone(dt.timezone.utc).date().isoformat()
    sid = int(row["snapshot_id"])
    for f, t, path in wh.archive_files(LADDERS, root):
        if f <= day <= t:
            hit = _ladder_index(path).get(sid)
            if hit is not None:
                stats["ladders_restored"] += 1
                return {**row, "raw_book": hit[0], "no_book": hit[1]}
    raise wh.StaleCheckout(
        f"snapshot {sid}'s ladder was moved to data/archive/{LADDERS} (stamped "
        f"{row['ladder_archived_at']}) and no file in this checkout holds it. Pull main.")


def complete_from(cut):
    """The first instant the database answers alone, or None when it always does."""
    if cut is None:
        return None
    day = dt.date.fromisoformat(cut["before"]) + dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(0), tzinfo=dt.timezone.utc)


def as_of(band_ids, at, *, rpc_fn, rest_fn, root=None):
    """What rpc_fn("book_as_of", ...) returns for the whole ladder, as if the
    books had never been pruned."""
    band_ids = list(dict.fromkeys(band_ids))
    rows = rpc_fn("book_as_of", {"p_band_ids": band_ids, "p_as_of": at.isoformat()}) or []
    stats["reads"] += 1
    root = root or ROOT
    rows = [with_ladder(r, root) for r in rows]
    cut = wh.prune_boundary(DATASET, rest_fn=rest_fn)
    _used.setdefault(DATASET, cut)
    start = complete_from(cut)
    if start is None or at >= start:
        stats["bands"] += len(rows)
        return rows                                        # the read it replaces

    wh.check_checkout(cut, root)
    have = {r["band_id"]: r for r in rows}
    # A file can only matter if it reaches past the database's oldest answer,
    # or holds any row at all for a band the database had nothing for.
    floor = None
    if all(b in have for b in band_ids):
        floor = (min(_ts(r["observed_at"]) for r in rows).astimezone(dt.timezone.utc).date().isoformat()
                 if rows else None)
    last = at.astimezone(dt.timezone.utc).date().isoformat()
    files = [p for f, t, p in wh.archive_files(DATASET, root)
             if f <= last and (floor is None or t >= floor)]

    out = []
    for band in band_ids:
        best = have.get(band)
        best_at = _ts(best["observed_at"]) if best else None
        for path in files:
            hit = newest_in_file(path, band, at)
            if hit and (best_at is None or hit[0] > best_at):
                best_at, best = hit
        if best is not None:
            stats["from_archive"] += best is not have.get(band)
            out.append(best)
    stats["bands"] += len(out)
    return out


def confirm_cut(rest_fn):
    """Before a date is banked: the prune the reads were answered below is
    still the newest. A run that crossed the nightly prune read some books
    from a database that had just lost them."""
    if DATASET not in _used:
        return                                             # no book was read
    used = _used.pop(DATASET)                              # the next date starts afresh
    now = wh.prune_boundary(DATASET, rest_fn=rest_fn, fresh=True)
    if now != used:
        raise wh.StaleCheckout(
            f"the books prune ran while this date was read (cut {(used or {}).get('before')} "
            f"-> {(now or {}).get('before')}); its books may be missing rows. Queue the run again.")


def reset():
    """Forget what this process looked up (tests)."""
    _index.clear()
    _ladders.clear()
    _used.clear()
    for k in stats:
        stats[k] = 0
    wh._boundary.pop(DATASET, None)
