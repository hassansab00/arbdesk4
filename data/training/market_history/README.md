# The venue's own record (plan v2.4 P3.10)

Every "Highest temperature in <city>" event Polymarket has listed, with its
ladder, its winner, its settlement station and the hourly price of every
bucket. It lets every model be judged against the market on thousands of
city-days. The database holds whole priced ladders only from 22 Sep 2026 (older
edges and books were pruned into `data/archive`, and those are partial).

Research input only: nothing prices or trades from these files. They live in
the repository, not Postgres, because the database is over its size target
(P1.6).

## Source

Fetched on 28 Sep 2026 by `tools/market_history.py`:

- **The index:** Polymarket's Gamma API `/events`, by tag (`daily-temperature`,
  `highest-temperature`), in 10-day windows of `end_date` (the API refuses deep
  offsets). Only events titled "Highest temperature in ..." are kept.
- **The prices:** the CLOB `/prices-history` for each bucket's YES token,
  `fidelity=60` (hourly), from the event's creation to 36 h after its
  `endDate`. Measured on the first 6,000 buckets, the gap between points has a
  median of 1.0 h, a 99th percentile of 2.0 h and a maximum of 3.0 h.

```bash
MARKET_HISTORY_CACHE=<dir> python tools/market_history.py index
MARKET_HISTORY_CACHE=<dir> python tools/market_history.py prices   # resumable
MARKET_HISTORY_CACHE=<dir> python tools/market_history.py write
```

The price fetch took 4,920 s for 106,868 buckets, with 0 failed requests.

## Files

| file | rows | one row per |
|---|---|---|
| `events.csv.gz` | 9,977 | event: `event_id, slug, city_slug, city_key, date, unit, resolution_source, station_icao, station_text, created_at, closed, listing` |
| `bands.csv.gz` | 108,841 | bucket: `event_id, band_index, label, band_lo, band_hi, open_low, open_high, token_yes, winner` |
| `prices.csv.gz` | 6,230,133 | hourly price: `event_id, band_index, t` (unix seconds), `p` (the YES price) |

- **Dates:** events run from 2025-12-30 to 2026-09-30.
  - 9,834 were resolved when fetched.
  - The winner is `1` on 9,832 buckets, `0` on 97,419, and empty on the 1,590 buckets of unresolved events.
- **Units:** C 7,677 events, F 2,300.
- **Buckets** follow the half-open convention of `v_canonical_bands`: `[band_lo, band_hi)` in whole units of the market's unit.
  - "16°C or below" is `(, 17)` with `open_low`.
  - "60-61°F" is `[60, 62)`.
  - Band 0 is the lowest bucket.
- **Station:** `station_icao` is the ICAO named by the event's settlement page. When the event has no `resolutionSource`, the page is taken from the first wunderground or weather.gov link in its rules.
  - 9,772 events name one.
  - The rest are Hong Kong and Taipei (retired), which settle on a national service.
  - The station can differ from the city's station today: Paris settled on LFPG until April 2026.
- **`listing`:** `arch` marks the 149 slugs Polymarket prefixed `arch-`. 48 of them have a `main` twin for the same day, and the `main` one is preferred.
- **`p`:** the venue's quoted price series, not an executable bid or ask.
  - On buckets nobody is trading, it can be a stale quote. On 269 post-peak rows it was 0.077 below the archived books' real ask on average.
  - A comparison with a model must take the first price at or after the model's information, not the last one before it. The newest price before a post-peak decision was a median 59 min old (`docs/S10_AFTER_COSTS_2026-09-28.md`).

## Used by

- `tools/market_vs_model.py` → `docs/MODEL_VS_MARKET_2026-09-28.md`: the day-ahead model against the market.
- `tools/p310_replay_inputs.py` → the S10 checkpoint replay's inputs → `docs/S10_VS_MARKET_RECORD_2026-09-28.md` and `data/replay/s10_market_record_2026-09-28.csv.gz`.
  - The inputs file itself is not committed: the builder rewrites it from these files.
  - That report prices the market BEFORE the decision, which leaks. The corrected run, `--market-at after`, is `docs/S10_VS_MARKET_RECORD_AFTER_2026-09-28.md`, with rows in `data/replay/s10_market_record_after_2026-09-28.csv.gz`.
- `tools/p310_reaction.py` → `reaction_events.csv.gz`, `reaction_prices.csv.gz` and `reaction_meta.json` (one-minute prices, fetched 28 Sep) → `docs/MARKET_REACTION_2026-09-28.md`.
  - Scope: 3,000 new daily maxima sampled from 28,079 (seed 7), plus 2,587 random-minute controls.
  - Each sampled event carries 30 min of prices before the reading and 120 min after.
- `tools/p310_s10_after_costs.py` → `docs/S10_AFTER_COSTS_2026-09-28.md`: S10's post-peak trades after the spread and the fee, including the archived books' real asks.
