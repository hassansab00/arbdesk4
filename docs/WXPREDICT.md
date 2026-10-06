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
| 2 | The market's price fetched fresh at each decision in the tick | todo |
| 3 | The model: A weather, B buckets, C market fusion, D calibration | next |
| 4 | Walk-forward proof against the market on every listed city-day | next, with phase 3 |
| 5 | Nightly cycle, shadow, the board; Hassan decides serving | todo |

## Phase 1: the data

### Sources (all committed, all rebuilt by code)

| source | what | rows | tool |
|---|---|---|---|
| `data/training/market_history/` | the venue's record: every event, its ladder, its winner, its settlement station, every bucket's hourly price | see its README (refreshed 5 Oct) | `tools/market_history.py` (`seed` added) |
| `data/training/wxpredict/station_reports/<ICAO>.csv.gz` | every routine and special report of the 50 settlement stations, 1 Jun 2025 - 5 Oct 2026 13:00Z | 902,238 (9.6 MB) | `tools/wxpredict/fetch_obs.py reports` |
| `data/training/wxpredict/station_daily.csv.gz` | one row per station and local day, 2019-12-31 - 2026-10-06 (temperature only before June 2025; the last days partial), with each day's longest stretch without a reading (`max_gap_h`, below) | 123,452 (1.9 MB) | `tools/wxpredict/fetch_obs.py climate` (`daily` from the cache) |
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
`data/training/wxpredict/table/wxpredict_table.csv.gz`. It is not committed: it
rebuilds from the sources above. `data/training/wxpredict/table_meta.json` is
committed, with the row count, every column's fill and the content's sha256.

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

**Answer: `p` is the book's midpoint.**
- On stable hours it matches the mid at the 90th percentile exactly. The bid, the ask and the last trade are all further off.
- The all-hours figure (0.015 at the 90th percentile) is the price moving in the 25 min between `p`'s point and the snapshot, not a different definition.
- The live reader (2.2) therefore reads each bucket's **midpoint at the decision instant**.
- **One open case.** When the spread is over 0.10 (271 of the 54,686 stable book pairs, and 13 tick pairs), the mid matches less well: 84.9% within 0.01, 90th percentile 0.02. The last trade does not explain it either (on the 13 tick pairs the mid is closer than the last trade). The cause is not measured. These books are rare and, at a spread over 0.10, not ones a strategy would trade into.

## Gaps (Phase 1)

- **Market activity is a proxy.** The venue's hourly price history has no volume. "Activity" is the number of
  bucket-hours whose price moved by more than half a cent in the last 1 h and 6 h. The trade
  prints in the database start 3 Oct 22:36Z, and the archive's trades are partial.
- **Hourly forecasts are best_match only.** The seven models are daily maxima, not hourly curves.
- **The forecast publication delay is assumed, not measured run by run** (P2.9's 7 h).
