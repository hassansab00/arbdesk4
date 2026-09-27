# Inputs of the first engine replay (plan v2 P5.12 part 2), 27 Sep 2026

`scripts/backtest/replay_engine.py` reads these with two inputs already in the
repository: `data/replay/inputs_2026-09-26/replay_inputs.json.gz` (the P7.3
markets, their bands, venue winners and peak hours) and the engine's own
pricings in `data/mirror/band_probabilities/` (the repository mirror of
`band_probabilities`, 12-26 Sep).

| file | what | from |
|---|---|---|
| `books.json.gz` | 35,515 book snapshots: `band_id, epoch, best_bid, best_ask, no_best_ask`, and the cumulative USD depth within 1/2/5/10/25c of the best ask and of the best bid | built 27 Sep from 287,011 snapshots of 11-26 Sep - 101,860 read from `book_snapshots` (`tools/p512_replay_books.sql`) and 185,151 from `data/archive/books/*.csv.gz` that the database no longer holds - then trimmed by `tools/p512_trim_books.py` to the snapshots some decision of 12-25 Sep can read (newest per band in the 3 h before a decision); all 44,682 (band, decision) lookups return the same snapshot from the trimmed file as from the full one |

Exact ladders (`raw_book`) are not here: the hourly prune had already emptied
them for these days (P5.13). The replay's fills walk the depth tiers instead
(the FILL note in `replay_engine.py`).
