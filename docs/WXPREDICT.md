# WXPredict, the predictive core engine

Named by Hassan on 5 Oct 2026. The plan was approved the same day ("WE NEED TO
BEAT THE MARKET ... APPROVED, PROCEED TO PHASE 1").

The goal is to price each city's "Highest temperature in <city> on <day>" ladder better than
the market prices it, judged on the venue's winner. It is city-specific: each
city's own weather and its market's activity by hour and date. It reads every
weather field the station reports, follows the day's rise and fall, and uses
the market itself as one more input, taken fresh at the decision.

Research only until Hassan decides serving (phase 5). Nothing here prices or
trades.

## The phases

| phase | what | state |
|---|---|---|
| 1 | Clean, refresh, one training table where every row knows only what was known | this PR |
| 2 | The market's price fetched fresh at each decision in the tick | 2.1 and 2.4 measured; the rest waits on wave 2's checkpoints |
| 3 | The model: A weather, B buckets, C market fusion, D calibration | first cut 11 Oct; the remaining day (3A) and the market residual (3C) the same day (below) |
| 4 | Walk-forward proof against the market on every listed city-day | the market residual beats the market's prices in every month and hour group (-0.0083 nats); no rule yet makes money at the real prices: test 2 (taker), test 3 (maker) and H1 lose (below) |
| 5 | Nightly cycle, shadow, the board; Hassan decides serving | not started: waits on a rule that makes money on dates it was not found on |

## Phase 1: the data

### Sources (all committed, all rebuilt by code)

| source | what | rows | tool |
|---|---|---|---|
| `data/training/market_history/` | the venue's record: every event, its ladder, its winner, its settlement station, every bucket's hourly price | see its README (refreshed 5 Oct) | `tools/market_history.py` (`seed` added) |
| `data/training/wxpredict/station_reports/<ICAO>.csv.gz` | every routine and special report of the 50 settlement stations, 1 Jun 2025 - 10 Oct 2026 23:58Z (extended 11 Oct: 9,716 reports after the committed span appended; the 14 committed reports a refetch spelled differently were kept, see below) | 911,954 | `tools/wxpredict/fetch_obs.py reports` |
| `data/training/wxpredict/station_daily.csv.gz` | one row per station and local day, 2019-12-31 - 2026-10-11 (temperature only before June 2025; the last days partial), with each day's longest stretch without a reading (`max_gap_h`, below). Rebuilt 11 Oct: 276 days added, 49 partial days of 5-6 Oct completed, none removed; a station no longer asked (Zhengzhou, ZHCC) keeps its 2,470 days | 123,728 | `tools/wxpredict/fetch_obs.py climate` (`daily` from the cache) |
| `data/training/previous_runs/` | what the public forecasts said before the day (P2.9) | see its README (hourly file extended 5 Oct) | `scripts/honest_record.py`, `tools/wxpredict/fetch_forecasts.py` |

The station reports carry every field IEM's ASOS service gives:
- temperature, dew point, humidity, wind, gust and precipitation;
- the altimeter and sea-level pressure, visibility, three cloud layers and present weather;
- from the METAR's remarks, the T group (tenths of a degree C, US stations) and the 6-hour maximum and minimum groups.

The old observation archive held sky cover in 1.8% of rows and pressure in none. It
started 22 Jul 2025, with one station per city as the cities table names it
today. These files follow the station each event settled on: Paris on LFPG to
18 Apr 2026, Denver on KDEN for five days in March.

Share of the 902,238 reports carrying each field:

| field | share |
|---|---|
| temperature, dew point, humidity, wind speed, visibility, altimeter | 100% |
| wind direction | 90.6% |
| a cloud layer (1st / 2nd / 3rd) | 73.2% / 34.1% / 12.2% |
| present weather | 19.3% |
| sea-level pressure; T group (tenths) | 17.7% (the US stations) |
| gust | 4.7% |
| 6-hour maximum and minimum | 2.6% (US synoptic hours) |

Pressure is the sea-level pressure where the report has one, else the altimeter setting in hPa. Sky cover is the most covered layer in oktas, and the ceiling is the lowest broken or overcast layer.

### The training table

`python tools/wxpredict/build_table.py` writes
`data/training/wxpredict/table/wxpredict_table.csv.gz`. It is not committed.
`data/training/wxpredict/table_meta.json` is committed, with the row count, every
column's fill, the content's sha256, and every source file the build read or
listed, each named by its git blob (`sources`, R47).

The sources move after a table is recorded: the nightly mirror extends the
forecast record in `previous_runs`, and the builder takes its cities from the
newest mirror of `public.cities`. So a plain build reads the files as they are
now, and `python tools/wxpredict/build_table.py --recorded` rebuilds the recorded
table from exactly its own sources, taken from git, and fails unless the
content's sha256 and the files read are the recorded ones.

- **Rows.** One per (event, decision time). Decisions are taken every hour of the
  event's local day D and every three hours of D-1, each 60 s after the hour,
  on the venue's own snapshot of the market.
  - The hours are real instants: a spring-forward day has 23, and a fall-back day has 25, with the repeated hour once per instant.
  - `decision_local` carries the offset.
- **Venue rows** are the listed events. The filters match the study's:
  - the city is active;
  - the event is closed, with exactly one winner;
  - a "main" listing wins over an "arch-" twin;
  - the ladder is contiguous;
  - the settlement station's reports are present.
- **Station rows** are the city-days the venue did not list, from 15 Jul 2025: the
  weather model learns from every whole station day (below). They have no ladder and no
  market.

#### What a row may know at decision time t

| input | known when | measured / assumed |
|---|---|---|
| a station report | valid + 20 min <= t | measured against IEM itself (below) |
| an hourly forecast value for hour H (`_previous_day1`) | H - 17 h <= t | P2.9's stated assumption: a run started >= 24 h before H is out within 7 h; not verified run by run |
| a daily forecast row (lead L, window ending at hour E of D) | D E:00 - (24 L - 7) h <= t | the same assumption; lead-1 00-17 known from D 00:00, lead-1 whole day from 06:00, lead-2 whole day from D-1 06:00 |
| a market price | stamped <= t | the series stamps every point 0-59 s past the hour (all 6,473,670 points), so a decision at hh:01 sees that hour's snapshot and nothing later |
| a day's station maximum (yesterday, a forecast's past error, climatology) | the whole local day ended 20 min before t, and the day is whole (below) | as a report |

The tests (`tests/test_wxpredict_table.py`) plant a later report, a later
forecast hour and a later price, and check that no feature moves.

**When a report is known.** On 5 Oct, 12:54-13:57Z, IEM's service was polled
every 51 s for the 50 stations' newest report, and each report's first
appearance was timed against its valid time. The 75 reports that first
appeared during the window appeared 10.9-11.8 min (median) after their valid
time, 12.2 min at the 95th percentile and 15.6 min at the slowest (MMMX). IEM loads them in
batches. The table uses a report from valid + 20 min, which covers the slowest seen with
a margin. One hour on one day is a small sample: phase 2 reads IEM at the tick and logs
each report's arrival, which will measure this every hour. The platform's own hourly ingest
adds more: `weather_observations` IEM rows were stored a median 38 min (90th
percentile 66 min) after their valid time over the 7 days to 5 Oct.

#### The label

The label is the venue's winning bucket, which is the only truth. The station's own maximum of D, in the
market's unit with the venue's rounding, is kept beside it as the weather
model's regression target and as a check. On venue rows the station maximum falls in
the winning bucket on 9,382 of the 9,556 listed days whose station day is whole (98.2%). Of the 174 that disagree:
- 118 are Shenzhen's, Mar - Aug 2026 (none in Sep - Oct);
- Seoul has 24, Moscow 11 and Denver 6.

`label_unit`, the weather model's target, is the station's maximum
moved into the winning bucket on those days, so the venue's truth wins. A station day that is not whole (below) has no
station label: the row keeps the venue's label alone.

#### A whole station day

A station day is whole when its reports leave no gap over 3 h, counting from
local midnight to the first report and from the last report to the next
midnight (`common.max_gap_h`, `WHOLE_DAY_MAX_GAP_H`), and the station's reports
run past the day's end. It is one rule wherever a day's maximum is read: the
label, the unlisted station days, yesterday's maximum, climatology and the
forecast's past error. Before the review of #314, the label and the unlisted
days checked only the station's newest report, so an old outage passed (VILK,
7 Nov 2025: no report for 14 h), and the past-day reads checked 12 reports, or
nothing for yesterday.

- `station_daily.csv.gz` carries each day's `max_gap_h`, regenerated on 6 Oct
  from a fresh fetch of the climate windows. The fresh fetch rebuilt the old file
  byte for byte (md5 `47213c91...`), and the new file differs from it only in
  the added column, on all 123,452 rows.
- 686 of the 123,452 station days have a gap over 3 h. From 1 Jun 2025 to
  4 Oct 2026: 81 of 24,542, and 8 more days have no report at all.
- No day passes the rule that failed the old 12-report check; 589 that passed
  it fail the rule.
- In the table, all 9,586 venue events are kept. On 30 of them the
  station's day is not whole, so they carry no station maximum: Denver 9,
  Panama City 4, Lucknow 3, Milan 3, Busan 3, Sao Paulo 2, Tel Aviv 2, and one each in
  Manila, Wellington, Austin and Paris. Of the unlisted days, 38 are left out
  for a gap over 3 h, 8 for having no report, and 1 for not being reported to
  its end.

#### Columns

**Built 6 Oct 2026** (`table_meta.json`, sha256 `d03b73e88b75...`). Two builds
gave identical bytes. A build took 6 min 57 s.

- **Rows:** 684,990, from 21,406 events in 48 cities, local days 15 Jul 2025 - 4 Oct 2026.
- **Venue events:** 9,586 (306,738 rows).
- **Station days:** 11,820 (378,252 rows). The whole-day rule left out 35 that
  the 5 Oct build kept (686,111 rows then).
- **Units:** C 16,505 events, F 4,901.
- **Left out of the venue's events:**
  - city not active 553;
  - not closed 136;
  - an "arch-" twin of a main listing 46;
  - not exactly one winner 3;
  - a ladder with a gap 1.
- **Market:** the whole ladder was priced at the decision on 247,736 rows, 80.8% of the venue rows.

The table has 118 columns:

| group | columns | filled |
|---|---|---|
| identity and clock | event, source (venue / station), city, station, date, unit, ladder, decision time (UTC and local: the decision instant, hh:01, which parses back to the UTC on every row), D-1 or D, local hour, weekday, day of year | 100% |
| labels | `winner` (venue rows), `label_unit`, the station's maximum (unit and C), whether they agree | `label_unit` 100% |
| the station now | age of the newest report, temperature, dew point, humidity, wind, gust, direction, pressure, visibility, sky, ceiling, five present-weather flags, 1 h / 3 h changes, precipitation over 3 h (each routine period's total, summed) | 100% (sky 75%, the rest per the table above) |
| the station today (D rows) | running maximum (C, unit, its bucket, its age), 6-hour-group maximum, today's minimum, 06:00 reading; yesterday's maximum (a whole day only) | running maximum 71.1% (D rows with reports), yesterday 93.5% |
| day-ahead forecasts | which daily row was known (lead 1 whole day / 00-17, lead 2), best_match max, the seven models (each, mean, spread, range), best_match cloud, sunshine, wind, rain, dew point | 99.9-100% (UKMO 93.6%) |
| the hourly forecast | known hours, the day's forecast max and its hour, now, the rest of the day's max, the next 3 h, the rest of the day's cloud / sunshine / wind / rain; observed minus forecast now and 3 h ago (temperature, dew point, pressure) | 75-90% |
| the station's past | climatology of the day of year (mean, spread, count, typical peak hour, every year before), the day-ahead forecast's error over the last 7 and 30 whole days (best_match and the models' mean), its MAE | 99.9-100% |
| the market | whole ladder priced, overround, implied mean / spread / top bucket / its price / entropy, price of the running maximum's bucket and of everything at or above it, the implied mean's change over 1 / 3 / 6 h, the top's change over 3 h, newest price's age, bucket-hours that moved in the last 1 / 6 h, every bucket's price and age | priced on 80.8% of venue rows |

## Phase 2.1: what the record's price is (measured 6 Oct)

The training table reads the venue's hourly `prices-history` point `p` as "the market at the decision". Before the live reader can read "the same thing", this measures what `p` is. The tool is `tools/wxpredict/what_is_p.py`; it reads committed files only, and every number below comes from it.

**Method.**
- Each archived book snapshot (`data/archive/books`, every 2 h at odd UTC hours, taken about 25 min past the hour) is matched to its bucket's `p` at the same hour (`data/training/market_history/prices`, stamped 0-59 s past the hour). The match goes through `band_id`, then `token_yes` (`data/mirror/bands`), then the record's bucket.
- The snapshot is about 25 min after `p`'s point, so a moving price blurs the comparison. The **control** keeps only the hours where `p` did not move between hh:00 and hh+1:00.
- The tick's own books (`data/mirror/prediction_checkpoints`: bid, ask and last at about hh:36) are checked the same way, to test the last trade too.

| `p` against | pairs | median | 90th percentile | within 0.005 | within 0.01 |
|---|---|---|---|---|---|
| book mid, all hours | 146,494 | 0.0 | 0.015 | 75.3% | 85.0% |
| **book mid, stable hours** | 54,686 | 0.0 | **0.0** | 95.6% | **98.5%** |
| book best bid, stable hours | 54,686 | 0.005 | 0.015 | 53.4% | 77.5% |
| book best ask, stable hours | 54,686 | 0.005 | 0.015 | 50.3% | 75.1% |
| tick mid, stable hours, spread ≤ 0.10 | 669 | 0.0 | 0.005 | 90.4% | 95.7% |
| tick last trade, stable hours, spread ≤ 0.10 | 669 | 0.005 | 0.99 | 50.2% | 61.6% |

**Answer: for spreads up to 0.10, `p` is the book's midpoint.**
- On stable hours with a spread up to 0.10 (54,415 pairs), it matches the mid at the 90th percentile exactly (98.6% within 0.01). The bid, the ask and the last trade are all further off.
- The all-hours figure (0.015 at the 90th percentile) is the price moving in the 25 min between `p`'s point and the snapshot, not a different definition.
- The live reader (2.2) reads each bucket's **midpoint at the decision instant**, and stores the spread with it, so the case below can be found live.

**Still open: spreads over 0.10.** These are 271 of the 54,686 stable book pairs (0.5%) and 13 tick pairs.
- Against the mid: 84.9% within 0.01, 90th percentile 0.02. That misses this step's tolerance (0.01 at the 90th percentile).
- **The venue's display rule (the last trade when the spread is over 0.10) does not explain it.** Of the 271 pairs, 25 have an archived trade print at or before `p`'s own stamp (`data/archive/trades` is partial: the other 246 have none; 94 August prints carry no token id and cannot be placed). On those 25:
  - the last trade is within 0.01 of `p` on 5 (20%), with a median gap of 0.079 and a 90th percentile of 0.15;
  - the mid is within 0.01 on 15 (60%), with a median of 0.005 and a 90th percentile of 0.065;
  - 5 pairs match neither.
- On the 13 tick pairs, the mid is also closer than the last trade.
- **The cause is not measured, and no definition tested meets the tolerance on these books.** The mid is the closest one measured, so 2.2 uses it for every bucket.
- 2.3's parity test reports the buckets with a spread over 0.10 on their own, with each difference listed. A definition for them waits on that test. G2 sees the result.

## Phase 2.4: market activity from the past (measured 10 Oct)

The table has no volume (Gaps, below). This asks whether the venue still serves the trades of settled events, so that a volume series could be built for the past rather than waiting for live history. The tool is `tools/wxpredict/trade_history.py`; its output, `data/eval/wxpredict/trade_history_probe.json`, holds every number below.

**Method.**
- **The sample:** 10 events from every month of the market record (closed, one winner, the main listing), drawn with a fixed seed: 108 events, 1,092 buckets, 30 Dec 2025 - 7 Oct 2026.
- **The trades:** each event's trades come from Polymarket's data API `/trades` (the endpoint `scripts/ingest_trades.py` reads every hour), paged to the end.
- **The check:** each bucket's summed trade sizes are compared with Gamma's own lifetime `volume` for that bucket. A match is agreement between two of the venue's own figures, not proof that every trade came back. Gamma's figure is itself off on some buckets (below), and missing and extra trades could net to it (Codex on #364). The one completeness check that does not lean on Gamma is the comparison with our own capture, further down.
- **The cap:** the API refuses an offset over 10,000. It also filters by `start`/`end` (one NYC bucket: 603 + 185 = its 788 trades), so a bucket over the cap can be asked in halves. No sampled event reached the cap.

**What came back.** 246,108 trades, 416 requests, 620 s, one request at a time. Two 500s and one 429 were each answered on the next try. Every bucket had at least one trade, and no trade was later than the day after the event's date. 158 trades are stamped before the event's `created_at`; this is not explained.

| bucket | count | months |
|---|---|---|
| sizes sum to Gamma's volume (within 0.01 shares): a volume match | 863 | all of Dec - Feb and May - Jun; 50 of 106 in Mar; most of Jul - Oct |
| Gamma gives no volume, so it cannot be checked | 56 | March only |
| differs from Gamma's volume | 173 | all of April; 13 in Jul, 32 in Aug, 16 in Sep, 2 in Oct |

**On the 173 that differ:**
- Size divided by Gamma's volume: median 0.991, range 0.506-1.222.
- **62 are above 1**: the API serves more than Gamma counts. So Gamma's figure is not exact truth either.
- Net, they fall 74,386 shares short. That is 0.91% of the sample's 8,172,025 shares of Gamma volume.
- Asking for every fill (`takerOnly=false`) does not explain the gap. On one Houston bucket of 4 Apr it returned each trade from both sides (285 fills, 9,207 shares, about twice the 116 taker trades' 4,542), and neither figure is Gamma's 4,665.
- The cause is not measured.

**Against our own capture.** `scripts/ingest_trades.py` has stored the same API's trades every hour, and `data/archive/trades` has them without a break from 27 Sep 22:34Z. The 10 sampled events dated 1-4 Oct traded entirely inside that span. Each of their trades the API serves today was matched on the archive's own key (condition, second, price, size, wallet):
- **The API serves 15,726 trades; we hold 13,113 rows for them, 2,613 short (16.6%).** That is two things:
  - **2,506 trades (15.9%) were never stored**: their key is in neither the archive nor the database;
  - **107 more were merged**: they share their key (condition, second, price, size, wallet) with a trade we did store, and the dedupe key keeps one row per key. 119 of the API's trades share a key with another, 12 of them among the never-stored.
- Of the 13,113 we stored, the API still serves every one. This is the one check here that does not lean on Gamma: for these events, the API has lost nothing we saw live.
- The ones never stored are not spread evenly across the hour. In minutes 10-19, 1,263 of 4,742 are missing (27%); in every other ten minutes, 8-14%.
- **So the live record undercounts trades, and the API's backfill is the more complete source.** The cause, measured the same day (R46 in `docs/WXPREDICT_BUILD.md`): 2,484 of the 2,506 never stored were traded before our market discovery first saw the market. The hourly ingest reads from its newest stored trade, less an hour, so it never asked for them. They are now backfilled, in the intraday pipeline, for every new market.

**Answer: a past volume series exists.** The venue serves the trades of settled events back to the oldest in the record (30 Dec 2025). Where Gamma has a volume, the trades' sizes match it exactly on 863 of the 1,036 buckets. For the 10 events of 1-4 Oct, the API serves every trade we captured live. **That every past trade is served is not proven:** a volume match is not completeness, and the live comparison covers 10 events. Per the plan, it enters the table only in a later version, with its own leak test: trades are stamped to the second, so a decision at t sees only trades before t.

**The size of a full backfill**, scaled from the sample (not measured):
- about 24.6 million trades over the record's 10,033 events (each month's sample mean times its events);
- at the sample's 5.74 s an event, about 16 h of requests;
- the raw trades are too large for the repository; the table would hold volume by bucket-hour.

## Phases 3-4, first cut: against the market (11 Oct)

`tools/wxpredict/walkforward.py` (research only; it needs numpy, pandas, scipy
and lightgbm, which `requirements.txt` does not carry). Built on the table of
10 Oct's sources (670,686 rows, gzip sha256 `bd16da92...`); the result is
`data/eval/wxpredict/walkforward_2026-10-11.json`. LightGBM runs
deterministic, so the same table and seed give the same numbers.

- **Weather model:** LightGBM on every row before the test month, the day's
  maximum in C from what was known at the decision; a spread model fitted on
  the 60 days before the month from a model that had not seen them, each
  calibration day's spread from a fold that had not seen it; each bucket's
  mass of that distribution in the market's unit, above the maximum already
  reported.
- **Fusion:** p proportional to q^a w^b, q the market's normalised prices, w
  the weather model's, a and b per decision hour group, fitted on the 60 days
  before the month.
- **No look-ahead at the cutoff** (Codex on #372): every fit leaves out the
  day before its cutoff, since a D-1 decision is made while that day runs.
- **Scored:** each month May-Oct 2026 on its venue decisions with every bucket
  priced, against the venue's winner: 216,055 decisions on 157 dates.

| mean log-loss per decision | |
|---|---|
| the market's prices | 0.9486 |
| the market's prices, tempered (q^a alone) | 0.9452 |
| the weather model alone | 1.2343 |
| fusion | 0.9414 |

- **Fusion against the market:** -0.0072 (90% interval -0.0091 to -0.0053,
  resampling whole dates). Below zero in every month; in May the interval
  spans zero.
- **Half of it is the market's own calibration:** tempering the market's
  prices alone gives -0.0034 (-0.0047 to -0.0019).
- **The pick does not change:** the most likely bucket wins 60.86% of the time
  by the market's prices and 60.84% by the fusion. The weather's fitted weight
  is 0.04-0.37 against the market's 0.85-1.66.
- **The weather model alone** is 0.29 worse than the market (MAE 0.757 C).

## Test 2: the fusion at the real asks (11 Oct; Hassan: "yes run test 2")

`tools/wxpredict/ask_backtest.py`, result
`data/eval/wxpredict/ask_backtest_2026-10-11.json`. The rule was fixed before
the run (the tool's docstring): at each order-book snapshot of a bucket, the
newest scored decision of that city-day at most an hour before it; buy one
share of YES at the best ask when the fusion's probability is at least 5
points above it, or of NO at its ask when 1 - p is; one share per bucket, side
and day, the first snapshot that qualifies; settled on the venue's winner; no
fee. The books are the repository's (the archive's pruned and the mirror's
kept snapshots), so the window is 22 Aug - 25 Sep: 59,628 snapshots matched on
28 dates; 93 ladders whose lower edges differ from the scored event's were
left out.

| rule | trades | payoff per share | 90% interval | total |
|---|---|---|---|---|
| fusion, 5 points (the test) | 1,738 | -0.0175 | -0.0309 to -0.0042 | -30.48 |
| fusion, 2 points | 3,529 | -0.0182 | -0.0270 to -0.0095 | -64.11 |
| fusion, 10 points | 695 | -0.0514 | -0.0845 to -0.0224 | -35.71 |
| market's own prices (control), 5 points | 1,397 | -0.0309 | -0.0481 to -0.0142 | -43.15 |
| market tempered, 5 points | 1,372 | -0.0314 | -0.0449 to -0.0172 | -43.10 |

At 5 points, YES -0.0321 (727 trades) and NO -0.0071 (1,011, interval -0.023
to +0.009); before 13h on the day -0.0041 (interval -0.030 to +0.022), from
13h -0.0493. No side, hour or threshold makes money; the control loses too,
so a price that looks cheap against the hourly record is mostly a book that
has already moved.

**Read:** the fusion's small improvement in probability does not survive the
ask. The market's favourite at the ask returns its price (above). Test 2
fails. The stop that followed was withdrawn the same day (Hassan, 11 Oct:
"DO NOT FREEZE DO NOT ABANDON ... WE NEED TO BUILD A SOLID SYSTEM"): the test
judged one model, not the idea; the next builds are below.

**The three angles:**

| angle | measured | price paid | result per share |
|---|---|---|---|
| a better forecast (the fusion, 5 points over the ask) | test 2 above, 22 Aug - 25 Sep | the book's best ask at the snapshot | -0.0175 (90% -0.0309 to -0.0042) |
| the market's own calibration (the favourite at the ask) | above, 22 Aug - 10 Oct | the book's best ask at the snapshot | about zero in every hour group |
| speed (buy the bucket a new daily maximum enters, 2 min after the reading's valid time) | `docs/MARKET_REACTION_2026-09-28.md`, 2,964 events on 207 dates | modelled, not executable: the one-minute price plus the hourly books' median half-spread, plus the taker fee | -0.0110 (90% -0.0203 to -0.0015); later entries lose more |

The speed row is not a payable price (Codex on #373): the study adds a
median half-spread to a quoted price. Its entry is also earlier than anyone
reading the public report could act: reports reached IEM 8-16 min after their
valid time when measured on 5 Oct (`REPORT_LAG_S`,
`tools/wxpredict/features.py`), and the study buys 2 min after it. It loses
even so. An executable test would need the order book at the minute the
report is published, which the hourly books do not have.

**The market's favourite at the real ask** (`book_snapshots`, 22 Aug - 10 Oct,
settled bands, the bucket with the highest mid at each hourly snapshot, bought
at its best ask): the outcome minus the ask per share is +0.020 (774 buys,
D-1), -0.001 (1,515, D before noon), +0.007 (438, noon-6pm), -0.055 (58, after
6pm), each within two of its standard error (a naive one, which understates
the clustering by date). The favourite wins as often as its ask says.

## Phase 3, the next builds (11 Oct, after the stop was withdrawn)

Every model below is scored the same way as the first cut: fitted only on
decisions dated before the eve of each test month, scored on the month's
venue decisions with every bucket priced, against the market's own
normalised prices, 90% intervals from resampling whole dates. The money tests
use the books and trade prints in the repository. Results in
`data/eval/wxpredict/*_2026-10-11.json`; the decision dumps they read are
rebuilt from the committed code and the table (sha256 `bd16da92...`).

### In probability

| model (tool) | what it is | minus the market, nats per decision | 90% interval |
|---|---|---|---|
| fusion, first cut (`walkforward.py`) | log pool of the market and the weather model | -0.0072 | -0.0091 to -0.0053 |
| fusion with the remaining day (`walkforward.py --remaining`, 3A) | on the day from 09h, the weather side is a multiclass model of how many buckets above the running maximum the day ends (`remaining.py`) | -0.0081 | -0.0099 to -0.0062 |
| market-aware, from scratch (`market_aware.py`) | the remaining-day model handed the market's prices as features, July-Oct | **+0.1172** (worse) | +0.1031 to +0.1323 |
| **market residual** (`market_residual.py`, 3C) | starts every bucket at the market's own price (logit) and boosts corrections only; the number of trees chosen on the last 14 training days | **-0.0083** | -0.0098 to -0.0069 |
| market residual with the weather (`market_residual.py --weather`, the 3A walk-forward's dump) | the same, each bucket also given the weather model's out-of-sample probability and its log ratio to the market's | **-0.0106** | -0.0126 to -0.0087 |

- The remaining day improves the weather model alone from 1.2343 to 1.1236
  (it is still 0.175 behind the market, 0.166 to 0.185).
- Learning the whole distribution from scratch loses what the price already
  knows (market-aware: worse in every month and hour group). Starting at the
  price and learning only where it is wrong is the first model better than
  the market in every hour group (D-1 -0.0124, D 00-08h -0.0101, D 09-12h
  -0.0082, D 13-16h -0.0070, D 17-23h -0.0022) and in every whole month (June
  -0.0114, July -0.0072, August -0.0075, September -0.0076; October's four
  days -0.0050, interval -0.0108 to +0.0024). The most likely bucket changes
  little: it wins 62.1% of the time by the market, 62.3% by the residual.
- With the weather model's probability added the residual gains most on the
  day from midnight to 16h (-0.0131, -0.0121, -0.0124) but less in the newest
  months: September -0.0056 (against -0.0076 without it), October's four days
  -0.0001 (-0.0071 to +0.0092).

### At the real prices

| test | rule (fixed before the run, in the tool) | window | market residual | the market's own prices (control) |
|---|---|---|---|---|
| test 2, taker (`ask_backtest.py --model-col p_model`) | buy one share at the best ask when p is 5 points above it | 22 Aug - 25 Sep, 1,651 trades | -0.0131 (-0.0256 to +0.0024) | -0.0309 (-0.0481 to -0.0142) |
| test 2 after the taker fee | the venue charges takers 0.05 p (1 - p) a share on weather markets (`scripts/cost_model.py`); the rule left it out | the same | **-0.0225** (-0.0349 to -0.0070) | -0.0400 (-0.0572 to -0.0234) |
| test 3, maker (`maker_backtest.py`) | rest one share at the best bid (offer) when p is 5 points better; filled only by a print through the price before the next snapshot | 11-25 Sep (trades carry their token from 11 Sep), 396 fills | **-0.0162** (-0.0363 to +0.0141) | -0.0372 (-0.0757 to -0.0093) |
| the residual with the weather, tests 2 / 2 after fee / 3 | the same rules | the same windows: 1,855 trades, 415 fills | -0.0013 (-0.0127 to +0.0112) / **-0.0107** (-0.0221 to +0.0017) / **-0.0096** (-0.0349 to +0.0084) | |

The residual's choices beat the market's own by about 2 cents a share in
both tests, and still lose: as a taker the spread and the fee are larger than
its edge; as a maker the orders that fill are the ones a better-informed
trader took (3% of the resting orders filled).

**Where the money goes.** Split by when the decision was made (after looking,
so not a result):

| | test 2 (taker, no fee) | test 3 (maker) |
|---|---|---|
| D-1 evening | +0.0328 (364 trades) | +0.0616 (32 fills) |
| on the day before 13h | -0.0091 (902) | +0.0305 (210) |
| on the day from 13h | -0.0659 (385) | -0.0961 (154) |

**H1, tested on dates it was not found on.** Fixed in `maker_backtest.py`
and committed (3793c19) before any date after 25 Sep was run: test 3's rule
on D-1 and the day before 13h pays more than zero. On 26 Sep - 4 Oct (7
dates; books from the archive, the mirror's kept books join in 16 days):
**-0.0019 a share (-0.0253 to +0.0222), 835 fills. H1 fails.** In the same
holdout, from 13h lost again (-0.0806, -0.1041 to -0.0570, 322 fills), and D-1
made money again (+0.0481, -0.0118 to +0.0966, 104 fills), but so did the
market's own prices at D-1 (+0.0386, 59 fills) and the tempered market
(+0.0582, 65 fills): resting orders on the evening before may pay whatever
the model. That is H2, for dates after 4 Oct; it is not claimed here.

### Decide at the book, not the hour (H3, 11 Oct)

Every test lost from 13h on the day, the market's own prices too: the
decision is the hour's (the venue's hourly price at hh:01), the trade is at a
book snapshot up to an hour later, and the reports move the book in between.
`market_residual.py --dump` now also writes `delta`, the trees' change to
each bucket's logit (rebuilding `p_model` from it matches on every row
checked; the rerun's `p_model` equals the first run's on all 187,479
decisions). `book_anchor.py` puts that change on the book's own mid at each
complete ladder snapshot (31,103 snapshots, 21,167 with a decision at most an
hour old), so the market's price in the decision is the one traded against;
the weather features stay the hour's.

**Each book against its own snapshot (Codex on #376).** The first runs
matched the book-anchored rows as the backtests match an hourly decision: the
newest row at most an hour before the book. A band's books came hourly from
12 to 25 Sep and every 2 hours from 26 Sep (per-band gap median 60.0 and
120.0 min; 5-7 hours until 3 Sep, and none archived 4-11 Sep, cause not
measured), so when the next snapshot had
no complete ladder, a book up to an hour on took the previous anchor's price
and a correction up to two hours old: 263 of the 549 exploratory taker trades
were 30-60 minutes after their anchor, and lost (-0.0447 after the fee). A
second book of a band in the same 10 minutes (2,044, 65-473 s after the
first) took a price the anchor had not priced. `ask_backtest.py` and
`maker_backtest.py` now judge a book-anchored row only against the band's
first book in its own snapshot, with the weather decision under it at most an
hour before the book (`own_snapshot`, `tests/test_wxpredict_book_anchor_match.py`);
an hourly dump is matched as before (rerun: test 2's results equal, test 3's
equal in every figure both files hold). This is what H3 said ("each book is
judged against its own snapshot's decision"), not a new rule, but it was
fixed after the holdout was seen, so the holdout below no longer stands alone:
the test that counts for H3a is the dates from 5 Oct, as already planned.
Both are kept (`*_book_anchor_*`, `*_h3_holdout_*` as run;
`*_own_snapshot_*` matched to their own snapshot).

| | taker after the fee (test 2's rule) | maker, through fills (test 3's rule) |
|---|---|---|
| hour-anchored residual, tests 2 and 3 | -0.0225 | -0.0162 |
| book-anchored, same windows (exploration: where the idea came from), as run | +0.0334 (+0.0055 to +0.0677), 549 trades | +0.0300 (+0.0117 to +0.0640), 320 fills |
| the same, own snapshot | +0.0796 (+0.0402 to +0.1203), 311 trades | +0.0272 (+0.0053 to +0.0668), 297 fills |
| the book's own prices, same windows, as run / own snapshot | -0.0140 / -0.1016 (37 trades) | -0.0190 / -0.0280 (228 fills) |
| **H3, 26 Sep - 4 Oct** (committed a83f4a6 before the run; 7 dates, books the archive's alone), as run | **+0.0525 (-0.0255 to +0.1284), 94 trades: not separable from zero** | **-0.0416 (-0.0727 to -0.0120), 721 fills: fails** |
| **the same, own snapshot** | **+0.0662 (-0.0104 to +0.1316), 89 trades: not separable from zero** | **-0.0410 (-0.0727 to -0.0104), 718 fills: fails** |
| the book's own prices, H3's window, as run / own snapshot | -0.1064 (20 trades) / -0.0433 (13) | -0.0389 (569 fills) / -0.0375 (568) |

**H3b (resting orders) fails; H3a (taking the ask) is not read either way**
and is tested again from 5 Oct, matched to its own snapshot. In H3a's window
D-1 made +0.1365 before the fee (46 trades; +0.1094 on 52 as run), found
after looking.

**Trade capture changes the maker test.** A resting order fills only on a
print through its price, so the fills depend on how many prints were
captured: the archive holds about 15,155 a day for 14-27 Sep and 23,480 to
32,426 a day from 27 Sep (`data/archive/trades`, prints over distinct days).
With fuller capture the same rule filled 721 times in 7 dates against 320 in
14, and the extra fills are the ones that lose. Test 3's and H1's earlier
windows undercount fills, for the model and the control alike.

**Ladder arbitrage** (`ladder_arb.py`, 22 Aug - 2 Oct). One bucket wins, so
YES on every bucket for less than 1, or NO on every live bucket for less than
the number of live buckets less 1, is riskless. The live YES bids summed over
1 on 524 of 24,606 ladder snapshots (353 markets), by a median 1 cent; the
taker fee on the legs is a median 3.2 cents, leaving 5 snapshots and $0.04.
All the YES asks summed under 1 on 39 snapshots, 3 after the fee. S2
(`scripts/strategies/s2_combination_arb.py`) already scans both baskets.

**Refreshed 11 Oct for H2.** The 14 committed reports a refetch spelled
differently are not corrections: each is a minute with two reports (a
routine and a special, or a correction), and `fetch_obs.py` keeps the one it
reads last, so IEM's order decides (KAUS 6 Jul 2025 13:55Z: 79 F committed,
blank on the refetch). Neither spelling is the record's, so the committed
rows stay and only reports after the committed span were appended. The
order-dependent choice is not fixed here.

### What each question has seen, and what a date holds (11 Oct, evening)

Two checks now stand before any result on new dates (the Codex action plan of
11 Oct, section 2):

- **The exposure ledger** (`data/eval/wxpredict/exposure_ledger.json`): every
  result in `data/eval/wxpredict` is registered under the question it answers,
  with the dates it scored, the rule's file and, for H1, H2 and H3, the commit
  that fixed the rule before its holdout. `tests/test_wxpredict_exposure_ledger.py`
  fails when a result is not registered, scores dates outside its entry, or
  scores a date the ledger calls untouched. **Every result ends by 4 Oct; the
  untouched dates start 5 Oct.** The sealed test of `WXPREDICT_BUILD.md`
  (1 Sep - 4 Oct) was scored by these research models and is not untouched.
- **The coverage report** (`tools/wxpredict/coverage.py`): per event date, the
  events, winners, decisions, books, complete ladders, prints, hourly
  forecasts and station days the scored events have, with the dates that miss
  any flagged. It scores nothing.

**It found a hole at once.** The mirror copies a market and its bands 9 days
after the market's date (`scripts/mirror_to_repo.py`, `CLOSED_AFTER_DAYS`),
because the market row changes until it settles. On 11 Oct it held markets
through 2 Oct, so no book after that could be matched to its ladder: 3 and
4 Oct had 1,504 decisions each and every winner, and no book and no print.
**H3's "26 Sep - 4 Oct" holdout scored 26 Sep - 2 Oct** (its 7 dates), and a
test "from 5 Oct" run on 12 Oct would have scored nothing. The nightly mirror
now also writes the columns fixed at listing (ids, edges, tokens, minimum
order and tick) of every market it does not hold yet to
`data/training/market_definitions/`. The tools read them only for those
markets (the mirror's row wins) and record when they did
(`definitions_recent`); the backtests' own ladder check (each market's lower
edges against the scored event's) still applies.

### At size: the execution and capacity audit (11 Oct, evening)

The one-share scores say whether a rule's average share pays. `tools/wxpredict/capacity.py`
asks how many dollars the same signals could have used, after costs, beside
them (never in place of them). Its rule was fixed before it touched any date
from 5 Oct and is in its docstring; the sizing and cash rules are plain Python
(`tools/wxpredict/execution.py`, `tests/test_wxpredict_execution.py`).

- **The ladder:** the archive keeps no full book (`raw_book` is stripped), only
  the dollars within 1, 2, 5, 10 and 25 cents of the touch. The ladder is rebuilt
  by the platform's own rule (`sql/ad4_13_reconcile.sql`, `ad4_synth_levels`),
  and walked with `cost_model.walk_ladder`. Within 1 cent of the best ask a
  late-September book holds a median $14.55 (10th percentile $1.77, 90th
  $147.68; 31,654 tradeable rows), within 5 cents $50.26.
- **NO** is bought on the YES bids at 1 - price: the venue's NO ask equals that
  complement within 0.001 on 99.93% of rows, and is present on 99.5% of
  late-September rows. Entries on books with no real NO ask are reported apart.
- **Size:** level by level while the probability is at least THETA above the
  level, up to a ticket ($10, $50, $250), the taker fee on every share.
  **The venue's minimum order is not recorded** (`markets.min_order_size` and
  `min_tick_size` are empty on all 2,708 markets; the stored books keep only
  bids and asks): 5 shares is assumed, 1 shown beside it.
- **Cash:** $1,000 shared, paid at entry, returned the day after the event
  date; at most 10% on one city-day. A simulation grid, not an allocation.
  Because the cap admits entries in time order, a dearer entry can change which
  later ones fit, so the stressed rows are not always lower. Entries at one
  instant are funded by band and side (`execution.funding_order`), never by
  probability or outcome. *As first run they were funded in the order the
  trades file listed them, every YES before every NO (Codex on #379); that
  order flattered the capped rows: the $50 exploration row read +$1,465.67,
  +$1,153.97 with the fixed order. The $10 rows and the holdout did not move.*
- **Latency:** at the snapshot (optimistic) and at the band's next snapshot
  within 3 hours (pessimistic; books are archived every 2 hours from 26 Sep,
  hourly 12-25 Sep). The live delay is not measured yet. *First written as
  "within 2 hours" when the cadence was believed hourly; the 2-hourly gaps run
  120-121 min, so it dropped entries whose next book came a minute late (33 of
  89 on the holdout). Widened before any date from 5 Oct was run.*
- **Stress:** the same entries with every first-tier share a cent dearer.
  *As first written, this put the whole first tier a cent past the touch and so
  refused entries the one-share rule had taken at the recorded best ask (58 of
  89 on the holdout); it was redefined before any date from 5 Oct was run, after
  that seen window had been looked at.*

The H3 corrections on the book (own snapshot), $10 tickets, at the snapshot:

| | entries | spent | net | per date (90%) | worst drawdown |
|---|---|---|---|---|---|
| exploration, 27 Aug - 25 Sep (23 dates; where H3 was found) | 295 | $2,678 | **+$469.77** | +$20.42 (+$7.17 to +$34.57) | $91 |
| the same, $50 tickets (mean entry $33: the depth runs out) | 261 | $8,643 | +$1,153.97 | +$50.17 (+$1.56 to +$102.73) | $361 |
| the same, stress +1c / next snapshot | 295 / 135 | | +$411.09 / +$228.02 | +$17.87 / +$16.29 (-$1.62 to +$34.85) | |
| holdout, 26 Sep - 2 Oct (7 dates) | 87 | $833 | **+$114.47** | +$16.35 (-$2.82 to +$35.15) | $33 |
| the market's own prices, holdout / exploration | 13 / 30 | | -$17.24 / -$99.75 | | |

**Read:** on the seen windows the one-share edge survives the walk down the
book, the fee and a cent of stress, and the control loses at size. The
exploration window is where the idea came from and is not evidence; the
holdout cannot separate it from zero. The test that counts is H3a from 5 Oct,
one-share and at size (`data/eval/wxpredict/capacity_*`).

### Reports the minute they arrive: the focused capture (WP4, 11 Oct)

The station reports reach the platform once an hour (the tick reads IEM at
:36 UTC; a median 36 minutes after the reading, p90 66, on 10-11 Oct). A
same-day trade on a new report is worth only what it is worth WHEN the report
can be known, and nothing recorded that. Hassan approved a poller (11 Oct:
"yes add the poller"): `supabase/migrations/20261011180000_reports_the_minute_they_arrive.sql`.

- **Pilots:** Jeddah (OEJN) and Istanbul (LTFM), 10:00-17:00 local
  (07-14 UTC), from `focus_obs_stations`, a row each, so the pilot can widen
  without a migration. Exploratory, not a claim that they pay best. Every other
  city is captured exactly as before.
- **Every minute** (pg_cron, inside the database, no GitHub minutes) one
  request to Aviation Weather's METAR feed for the open stations, through
  pg_net. Each distinct report is kept once, with the reading's time, Aviation
  Weather's own receipt time and **our first receipt** (the answer that first
  carried it). A correction (COR) is a new row beside the original. Every poll
  and its answer is kept, so a missed minute shows.
- **The Workflows page** switch `P2.11_focus_obs` (off or manual) stops the
  asking; the migration adds it to `settings.workflow_schedules` and the page
  lists it. One `ingest_log` row a UTC day (23:55Z): polls, failures (an
  answer that could not be read counts as failed, whatever its status),
  reports, corrections, and per station the median minutes from the reading
  and from the source's receipt to ours. It joins the watchdog after its
  first days.
- **The nightly mirror** copies the reports, the polls and the stations.
- **Not yet known:** whether Aviation Weather is earlier than NOAA's timeseries
  (the settlement source) or than the venue's prices move. That is what the
  record is for; the 28 Sep result (buying a new maximum's bucket 2 minutes
  after the reading lost -0.0110 a share) says speed alone has not paid.

## Gaps (Phase 1)

- **Market activity is a proxy.** The venue's hourly price history has no volume. "Activity" is the number of
  bucket-hours whose price moved by more than half a cent in the last 1 h and 6 h. The trade
  prints in the database start 3 Oct 22:36Z, and the archive's trades are partial. **Phase 2.4
  (10 Oct):** the venue serves the trades of settled markets, matching Gamma's volume on 863 of 1,036 checkable buckets, so a past
  series can be built; our own hourly capture holds 16.6% fewer rows than the API's trades (above).
- **Hourly forecasts are best_match only.** The seven models are daily maxima, not hourly curves.
- **The forecast publication delay is assumed, not measured run by run** (P2.9's 7 h).
