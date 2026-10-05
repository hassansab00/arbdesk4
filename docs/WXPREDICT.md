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
| `data/training/wxpredict/station_daily.csv.gz` | one row per station and local day, 2019-12-31 - 2026-10-05 (temperature only before June 2025) | 123,452 (1.8 MB) | `tools/wxpredict/fetch_obs.py climate` |
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
- **Venue rows** are the listed events. The filters match the study's:
  - the city is active;
  - the event is closed, with exactly one winner;
  - a "main" listing wins over an "arch-" twin;
  - the ladder is contiguous;
  - the settlement station's reports are present.
- **Station rows** are the city-days the venue did not list, from 15 Jul 2025: the
  weather model learns from every whole station day. They have no ladder and no
  market.

#### What a row may know at decision time t

| input | known when | measured / assumed |
|---|---|---|
| a station report | valid + 20 min <= t | measured against IEM itself (below) |
| an hourly forecast value for hour H (`_previous_day1`) | H - 17 h <= t | P2.9's stated assumption: a run started >= 24 h before H is out within 7 h; not verified run by run |
| a daily forecast row (lead L, window ending at hour E of D) | D E:00 - (24 L - 7) h <= t | the same assumption; lead-1 00-17 known from D 00:00, lead-1 whole day from 06:00, lead-2 whole day from D-1 06:00 |
| a market price | stamped <= t | the series stamps every point 0-59 s past the hour (all 6,473,670 points), so a decision at hh:01 sees that hour's snapshot and nothing later |
| a day's station maximum (yesterday, a forecast's past error, climatology) | the whole local day ended 20 min before t | as a report |

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
the winning bucket on 9,410 of 9,586 listed days (98.2%). Of the 176 that disagree:
- 118 are Shenzhen's, Mar - Aug 2026 (none in Sep - Oct);
- Seoul has 24, Moscow 11 and Denver 6.

`label_unit`, the weather model's target, is the station's maximum
moved into the winning bucket on those days, so the venue's truth wins.. A day whose reports stop before it ends has no
station label.

#### Columns

**Built 5 Oct 2026** (`table_meta.json`, sha256 `354f5ade56f6...`). Two builds
gave identical bytes. Each build takes about 7 minutes.

- **Rows:** 686,112, from 21,441 events in 48 cities, local days 15 Jul 2025 - 4 Oct 2026.
- **Venue events:** 9,586 (306,752 rows).
- **Station days:** 11,855 (379,360 rows).
- **Units:** C 16,528 events, F 4,913.
- **Left out of the venue's events:**
  - city not active 553;
  - not closed 136;
  - an "arch-" twin of a main listing 46;
  - not exactly one winner 3;
  - a ladder with a gap 1.
- **Market:** the whole ladder was priced at the decision on 247,751 rows, 80.8% of the venue rows.

The table has 118 columns:

| group | columns | filled |
|---|---|---|
| identity and clock | event, source (venue / station), city, station, date, unit, ladder, decision time (UTC and local), D-1 or D, local hour, weekday, day of year | 100% |
| labels | `winner` (venue rows), `label_unit`, the station's maximum (unit and C), whether they agree | `label_unit` 100% |
| the station now | age of the newest report, temperature, dew point, humidity, wind, gust, direction, pressure, visibility, sky, ceiling, five present-weather flags, 1 h / 3 h changes, precipitation | 100% (sky 75%, the rest per the table above) |
| the station today (D rows) | running maximum (C, unit, its bucket, its age), 6-hour-group maximum, today's minimum, 06:00 reading; yesterday's maximum | running maximum 71.1% (D rows with reports), yesterday 93.7% |
| day-ahead forecasts | which daily row was known (lead 1 whole day / 00-17, lead 2), best_match max, the seven models (each, mean, spread, range), best_match cloud, sunshine, wind, rain, dew point | 99.9-100% (UKMO 93.6%) |
| the hourly forecast | known hours, the day's forecast max and its hour, now, the rest of the day's max, the next 3 h, the rest of the day's cloud / sunshine / wind / rain; observed minus forecast now and 3 h ago (temperature, dew point, pressure) | 75-90% |
| the station's past | climatology of the day of year (mean, spread, count, typical peak hour, every year before), the day-ahead forecast's error over the last 7 and 30 whole days (best_match and the models' mean), its MAE | 99.9-100% |
| the market | whole ladder priced, overround, implied mean / spread / top bucket / its price / entropy, price of the running maximum's bucket and of everything at or above it, the implied mean's change over 1 / 3 / 6 h, the top's change over 3 h, newest price's age, bucket-hours that moved in the last 1 / 6 h, every bucket's price and age | priced on 80.8% of venue rows |

## Gaps (Phase 1)

- **Market activity is a proxy.** The venue's hourly price history has no volume. "Activity" is the number of
  bucket-hours whose price moved by more than half a cent in the last 1 h and 6 h. The trade
  prints in the database start 3 Oct 22:36Z, and the archive's trades are partial.
- **Hourly forecasts are best_match only.** The seven models are daily maxima, not hourly curves.
- **The forecast publication delay is assumed, not measured run by run** (P2.9's 7 h).
