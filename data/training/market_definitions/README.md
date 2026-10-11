# Market definitions the mirror does not hold yet

`scripts/mirror_to_repo.py` copies a market and its bands into `data/mirror`
9 days after the market's date (`CLOSED_AFTER_DAYS`), because a market row
changes until it settles. Research on recent dates still needs to know which
band is which bucket: on 11 Oct the mirror held markets through 2 Oct, so the
books of 3 and 4 Oct could not be matched to their ladders
(`tools/wxpredict/coverage.py`).

Each night the mirror also writes here the columns fixed when a market is
listed, for every market dated after the mirror's newest:

- `markets-recent.csv.gz`: `market_id, city_key, resolution_date, unit,
  band_count, condition_id, min_order_size, min_tick_size, first_seen_at`
- `bands-recent.csv.gz`: `band_id, market_id, band_index, band_label,
  band_lo, band_hi, open_low, open_high, condition_id, token_yes, token_no`

Both files are rewritten each night (same rows, same bytes), counted against
the live table before they are kept. They are research input, not the
record: the mirror's own rows are the record, and the tools
(`tools/wxpredict/ask_backtest.read_definitions`) read these only for
markets the mirror does not hold, and say so in their results
(`definitions_recent`).
