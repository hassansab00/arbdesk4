"""Keep only the book snapshots the engine replay can read (plan v2 P5.12 part 2).

A decision at t reads each of its market's bands' newest snapshot in
[t - BOOK_MAX_AGE_S, t] (replay_engine.as_of). Every other snapshot is dead
weight in the repository. This keeps exactly the ones some decision can
return, then checks that every (band, decision) lookup returns the same
snapshot from the trimmed file as from the full one.

    python tools/p512_trim_books.py FULL.json.gz TRIMMED.json.gz --from 2026-09-12 --to 2026-09-25
"""
import argparse
import datetime as dt
import gzip
import json
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "scripts"))

from backtest import replay_engine as re_  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("full")
    ap.add_argument("out")
    ap.add_argument("--inputs", default="data/replay/inputs_2026-09-26/replay_inputs.json.gz")
    ap.add_argument("--from", dest="date_from", default="2026-09-12")
    ap.add_argument("--to", dest="date_to", default="2026-09-25")
    a = ap.parse_args(argv)
    inp = re_._json(a.inputs)
    markets = {mid: {"market_id": mid, "city": c, "date": dt.date.fromisoformat(d), "winner": w, "unit": u, "tz": tz}
               for mid, c, d, w, u, tz in inp["markets"]}
    bands_of = defaultdict(list)
    for bid, mid, *_ in inp["bands"]:
        bands_of[mid].append(bid)
    peaks = {(c, int(mo)): h for c, mo, h in inp["peaks"] if h is not None}
    every = {mid: [(0, {})] for mid in markets}          # plan every market; the replay skips those unpriced
    events = re_.plan(markets, peaks, dt.date.fromisoformat(a.date_from), dt.date.fromisoformat(a.date_to), every)
    times_of = defaultdict(list)
    for t, _o, kind, mid, _cp in events:
        if kind == "decide":
            for b in bands_of[mid]:
                times_of[b].append(t)
    full = re_._json(a.full)
    cols = full["columns"]
    books = re_.index_books(full["rows"], cols)
    keep = set()
    for b, ts in times_of.items():
        if b not in books:
            continue
        for t in ts:
            s = re_.as_of(books[b], t, re_.BOOK_MAX_AGE_S)
            if s is not None:
                keep.add((b, s["epoch"]))
    rows = [r for r in full["rows"] if (r[0], r[1]) in keep]
    trimmed = re_.index_books(rows, cols)
    checked = 0
    for b, ts in times_of.items():
        for t in ts:
            x = re_.as_of(books[b], t, re_.BOOK_MAX_AGE_S) if b in books else None
            y = re_.as_of(trimmed[b], t, re_.BOOK_MAX_AGE_S) if b in trimmed else None
            assert x == y, (b, t)
            checked += 1
    with gzip.open(a.out, "wt") as f:
        json.dump({"columns": cols, "rows": rows}, f, separators=(",", ":"))
    print(json.dumps({"full_rows": len(full["rows"]), "kept": len(rows), "lookups_checked": checked,
                      "bytes": os.path.getsize(a.out)}))


if __name__ == "__main__":
    main()
