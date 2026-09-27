# The market beside S10's replay, read the engine's way (27 Sep 2026)

`tops.json.gz`: top of book `[band_id, epoch seconds, best_bid, best_ask]` for
every band of the P7.3 replay (`data/replay/inputs_2026-09-26`, 17,171
bands, 1,561 city-days of 24 Aug-25 Sep): 261,518 snapshots, 23 Aug 16:34Z -
25 Sep 21:26Z.

- 203,185 rows from `data/archive/books/*.csv.gz` (the 7 files there), taken
  for those bands: 210,564 read, deduplicated on (band, observed_at) across
  files that overlap;
- 58,333 rows from `book_snapshots`, read 27 Sep ~14:50Z for the markets
  resolving 21-25 Sep, observed 20-27 Sep (65,161 rows in 4 chunks, the
  ones on the replay's bands kept). No (band, time) was in both.

Used by `scripts/backtest/replay_checkpoints.py --tops ... --out-ladders ...`,
which writes, per checkpoint, S10's full ladder and the market ladder the
engine would have read then (`market_anchor.market_probs` on each band's
newest top of book no older than 3 h; none unless every band is quoted). The
replay's own rows and report are unchanged by it (checked byte for byte
against `docs/S10_REPLAY_2026-09-26.md` and `data/replay/s10_replay_2026-09-26.csv.gz`).

The replay's older `mids` input used only two-sided mids; half of the
snapshots are ask-only (5,636 of 11,317 snapshots in the database's last day, read 27 Sep ~14:40Z), which the
engine reads as half the ask, so the engine's market needs the bid and the
ask, not the mid.
