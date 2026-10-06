# Seasonal Focus 10: pre-registration (6 Oct 2026)

Hassan, 6 Oct 2026: "Record the shortlist and its start date now, then evaluate subsequent outcomes. That lets us establish whether focusing improves quality without choosing cities retrospectively because they happened to win."

This document is committed before any outcome it will be judged on. The same set is written to the database as `focus_sets` row `seasonal-2026-10-06` (migration `20261006150000`), which is append-only. Nothing below may be edited after its outcomes are known. A change is a new set with its own record.

## The set

- **Cities, in rank order:** lucknow, karachi, helsinki, wellington, tel_aviv, milan, chicago, moscow, miami, amsterdam.
- **Window:** 6 Oct - 30 Nov 2026.
- **Evaluated from target date 8 Oct 2026.** That is the first date every one of whose calls, the day-ahead call included, is made after this record. A 7 Oct day-ahead call was frozen on the evening of 6 Oct, local time, which for most cities had already passed.
- **Universe it is compared with:** every active city (48 on 6 Oct, `data/mirror/cities/cities-2026-10-06.csv.gz`).

## How it was chosen

- **Tool:** `tools/focus/season_predictability.py`. It reads committed files only, and its method and ranking rule were written in its docstring before any number was computed.
- **Output:** `data/eval/focus/season_predictability_2026-10-06.json`, sha256 `b02509634235cd25ef75310b8b89f2a046b178cf5c44946d7f95974f0d87655e`. Two runs give the same bytes.
- **The sealed test is untouched.** Nothing dated on or after 2026-09-01 is used, and the code asserts it on every row. The blinded challengers (rd3, da_floor, sd_corr) are not read.
- **Measure:** how often a bias-corrected day-ahead forecast of the station maximum lands in the venue's bucket. The analog window is 6 Oct - 30 Nov of past years.
  - The forecast is Open-Meteo best_match `_previous_day1`, hours 00-17. It is corrected by the city's own error over D-31 .. D-2.
  - The observation is the whole-day station maximum, read the venue's way.
  - Forecasts exist for 2025 only, so N = 51-56 days per city. Station day-to-day volatility uses 2020-2025.
- **Ranking rule:**
  - at least 40 scored days;
  - highest hit rate first, with ties to the steadier city;
  - a city whose error and day-to-day swing both rise into November by 0.5 °C or more is held back (none was).

| rank | city | unit | scored days | hits | hit rate % [95%] | error SD °C | bias °C | day-to-day SD °C | in the 10 under bootstrap | station = venue winner, pre-cutoff |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | lucknow | C | 51 | 31 | 60.8 [47-73] | 0.68 | -0.89 | 1.56 | 99% | 175/175 |
| 2 | karachi | C | 56 | 27 | 48.2 [36-61] | 0.81 | -0.17 | 1.45 | 88% | 138/138 |
| 3 | helsinki | C | 56 | 27 | 48.2 [36-61] | 0.86 | -0.14 | 2.59 | 94% | 150/150 |
| 4 | wellington | C | 56 | 26 | 46.4 [34-59] | 0.71 | -1.37 | 2.32 | 76% | 192/192 |
| 5 | tel_aviv | C | 56 | 25 | 44.6 [32-58] | 1.16 | -0.46 | 2.23 | 70% | 173/173 |
| 6 | milan | C | 56 | 25 | 44.6 [32-58] | 1.04 | -0.67 | 2.46 | 66% | 165/165 |
| 7 | chicago | F | 56 | 24 | 42.9 [31-56] | 1.21 | -0.56 | 4.38 | 55% | 192/192 |
| 8 | moscow | C | 56 | 23 | 41.1 [29-54] | 1.01 | -0.50 | 2.81 | 47% | 142/153 |
| 9 | miami | F | 56 | 22 | 39.3 [28-52] | 1.00 | -0.69 | 1.66 | 43% | 191/192 |
| 10 | amsterdam | C | 56 | 22 | 39.3 [28-52] | 1.10 | +0.24 | 2.14 | 42% | 150/150 |
| 11 | istanbul (not in the 10) | C | 56 | 22 | 39.3 [28-52] | 1.07 | -0.97 | 2.82 | 35% | 154/154 |
| 12 | paris (not in the 10) | C | 51 | 20 | 39.2 [27-53] | 1.19 | +0.31 | 2.47 | 41% | 134/134 |

The full ranking of all 48 cities, the sensitivity forecasts and each city's reasons in numbers are in the JSON. The climate notes (`tools/focus/climate_notes_2026-10-06.json`) are web summaries written after the ranking; they explain, they are not measured, and they were not inputs.

## What is known to be weak (stated before the outcomes)

- **One season of forecasts.** 2025's Oct - Nov may not repeat in 2026.
- **The places are close.** The 95% intervals are about ±12 points wide. Places 7-17 (42.9% down to 37.5%) overlap almost completely. In a block bootstrap, only Lucknow, Helsinki, Karachi, Wellington and Tel Aviv are in the 10 in 70% of draws or more.
- **Ties.** Amsterdam won tenth place from Istanbul on day-to-day swing (22/56 each). Amsterdam and Milan missed the November flag narrowly: their error SD rose by 0.495 and 0.476 °C.
- **Moscow's station.** Its station reading matched the venue's winner on only 142 of 153 pre-cutoff days. All 11 misses were May - Aug 2026, with the venue's bucket lower.
- **The forecast used matters.** On the seven-model mean, bias-corrected, the hit rate is higher in 35 of 48 cities (mean 38.9% against 32.2%), and the ranking changes: rank correlation with this one is 0.58, and 5 of its 10 are the same. That finding belongs to WXPredict's model work. It does not change this set.
- **No market skill in the window.** Market history starts 30 Dec 2025, so no Oct - Nov market was available before the cutoff.

## How it is evaluated (fixed now)

- **Rows.** Every settled call on target dates from 8 Oct to 30 Nov 2026, from the platform's frozen records. These are the day-ahead record (`v_city_hit_history`) and the checkpoint calls (`v_checkpoint_calls`, first call per city, day and checkpoint).
- **Groups.** The 10 against the full active universe, and the 10 against the other 38. Every comparison uses the same target dates, the same checkpoint and the same predictor version: a row counts only where both groups have rows for that date, checkpoint and version.
- **Separate readings.** Day-ahead (including the eve call), noon and the two pre-peak checkpoints are reported apart. Morning is reported on its own. Post-peak is reported separately and labelled after the peak: it is not evidence about forecasting.
- **Metrics:**
  - top-1 hit rate with a Wilson 95% interval;
  - mean probability on the winner;
  - the market's top-1 on the same rows.
- **The difference.** Focus minus universe, and focus minus the rest, is taken per date and averaged. Its 90% interval comes from whole dates resampled with `tools/fec_same_day.cluster_boot`, seed 11, 1,000 resamples.
- **Reading.** The Predictive page shows the comparison as it accrues. The formal reading is on 1 Dec 2026, over the whole window.
- **"Focusing improves quality"** is claimed for a checkpoint group only when the 90% interval of focus minus rest is above 0 at the formal reading. Anything else is reported as "not shown".
- **What is not claimed.** A higher hit rate in easy cities is not by itself a better predictor. The comparison says whether choosing cities this way picks days that are easier to call, on the same predictor.

## Rules that hold while the set is in use

- **Membership never moves a probability.** No pricing code reads `focus_sets` (pinned by `tests/test_focus_set.py`). Membership filters what is shown and what is scored, and directs analysis and model work.
- **Every city keeps its basic collection,** so the comparison keeps its universe.
- **Serving models, evaluation models and blinded challengers stay separate,** exactly as before.
- **The next set** (for the window after 30 Nov) is chosen by the same tool, or a successor committed before it is run, on data dated before its own record. It must still exclude the sealed test window until G4 has scored it. It is recorded before its window starts.
