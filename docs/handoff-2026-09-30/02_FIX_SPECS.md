# Fix specifications — written 30 Sep 2026

Each fix has: the problem as Hassan sees it, the root cause with its evidence, the design, the files and lines, the tests, the acceptance, and how to prove it. Line numbers are from `main` at `ac51db2`; re-check them before editing. Every number cites its source. The queries are in `04_VERIFY_QUERIES.sql`.

Rules that apply to all three:
- **Both test suites before every push** (`CLAUDE.md`). Web changes also need `tsc`, `test:routes` and `next build`.
- **No new scheduled workflow** (Rule 7). Add work to a run that already exists, and measure its minutes.
- **Never update or delete a banked row.** `fact_band_outcome` is append-only; databank upserts with ignore-duplicates.
- **Update `docs/PLAN_PROGRESS.md`** in every PR: rows P4.7 and P6.5, and the audit table.

---

## F1. US city-days appear in the hit/miss table the same day

### What Hassan sees
- On /predictive, "Hit and miss, day by day", a US city's newest row is two days old.
- On 30 Sep at 08:4xZ, as `anon`, every US city's latest `for_date` was 28 Sep and every Celsius city's was 29 Sep.
- The table is sorted newest first, so the US cities look missing.

### Root cause — three parts, all measured

**1. The table's data is written once a day, before US days end.**
- `/predictive` reads `v_city_hit_history` (`sql/ad4_85_city_hit_history.sql`).
- That reads stored rows in `mv_city_hit_history` (`sql/ad4_89_page_cache.sql` line 28), built from `fact_band_outcome`.
- `fact_band_outcome` is written only by `scripts/databank.py` (`bank_bands`, lines 262–458; upsert at line 646).
- databank runs only in `.github/workflows/pipeline_daily.yml` (line 136: `python scripts/databank.py --days 7`).
- n8n dispatches `pipeline_daily` at **04:36Z** (`n8n/P6.1_clock.template.json`, `"hours_utc": [4]`) since 25 Sep.
- A US local day ends 04:00Z (New York) to 07:00Z (Los Angeles, San Francisco, Seattle).

**2. The venue confirmation for US markets also waits for that daily run.**
- `bank_bands` freezes a ladder only when `v_venue_market_resolution` says `confirmed` and every band is confirmed with exactly one winner (lines 353–369).
- Measured, `markets.resolution_verified_at` against the local day end:

  | Days | Unit | Median verified after day end | Verified by |
  |---|---|---|---|
  | 25–28 Sep | °F | 22.8–24.1 h | the next 04:4xZ daily sweep, except 1–2 markets a day |
  | 25–28 Sep | °C | 10.3–13.0 h | the 04:4xZ daily sweep |
  | 29 Sep, at 08:5xZ on 30 Sep | °F | 0 of 11 verified | — |
  | 29 Sep, at 08:5xZ on 30 Sep | °C | 35 of 37 verified | — |

**3. The one step meant to confirm US days in the morning is starved.**
- P4.7 (`scripts/confirm_recent.py`, inside the hourly tick at 06–17Z) runs `paper_settlement.cycle(budget_seconds≤12, days_back=2, unconfirmed_only=True)`.
- Measured, `ingest_log` job `P4.7_confirm_recent`, 29 Sep 06:36Z to 30 Sep 08:36Z (15 runs):
  - 4 were skipped with a budget under 1 s;
  - the others reached 2–34 of about 150 candidate bands (123–156 unreached);
  - evidence captured was 0–8 a run;
  - `venue_has_no_record` was 2–21 a run.
- The venue answers about 1 band a second (`pipeline_intraday.yml` comment). A 12 s budget cannot cover 121 US bands (11 markets × 11 bands).

**Why it was never fixed.** P4.7's own note (PLAN_PROGRESS row P4.7, 26 Sep) said "The day-ahead record (`fact_band_outcome`, databank) still banks US days on the next daily run; this step does the checkpoints." Nobody acted on it.

**When it broke.** `fact_band_outcome.captured_at` against the day end (22–29 Sep) shows the change on 25 Sep:
- Before 25 Sep, US days were banked a median of 4.4–6.2 h after the day ended, when the daily run happened at ~09:24–11:14Z.
- From 25 Sep, 24.0 h every day.

### What is not known yet — measure before building
- **When the venue itself resolves a US market.** `venue_has_no_record` means the venue had no answer yet when asked. Until that time is measured, any schedule is a guess.
  - Ask the venue (the same calls `paper_settlement` makes) at 08:36Z and 12:36Z about yesterday's 11 US markets.
  - Record how many are resolved. Do it for 2–3 days.
- **How long a US-only sweep and a databank pass take.** Run them once by hand in a dispatch and read the step timings.

### Design (cheapest correct option first)

**1. `scripts/paper_settlement.py`: expose what `cycle()` already supports.**
- The CLI (lines 325–341) has `--holdings-only` but no `--unconfirmed-only`.
- Add `--unconfirmed-only`, passed to `cycle(..., unconfirmed_only=True)`, plus the existing `--days-back`.
- With both, the sweep asks only about markets whose day ended in the window and whose `resolution_verified_at` is null (line 146).

**2. `.github/workflows/pipeline_intraday.yml`: two steps after "Venue-confirmed paper settlement" (line 160), before "Refresh the stored page rows" (line 168).**

```yaml
- name: Recently ended ladders, confirmed (P4.7 day-ahead half)
  if: ${{ !cancelled() }}
  timeout-minutes: 4
  run: python scripts/paper_settlement.py --unconfirmed-only --days-back 2 --budget-seconds 150 --max-evidence 300
- name: Bank the ladders just confirmed (P4.7 day-ahead half)
  if: ${{ !cancelled() }}
  timeout-minutes: 4
  run: python scripts/databank.py --days 2 --proof-days 2
```

- `pipeline_intraday` runs at 00:36, 04:36, 08:36, 12:36, 16:36 and 20:36Z (P6.1 clock). The 08:36Z and 12:36Z runs are the ones that matter.
- Restricting the two steps to those hours by checking the UTC hour inside the script is fine. That is not a new schedule.
- The existing "Refresh the stored page rows" step then refreshes `mv_city_hit_history`, so the page shows the new rows at once.
- **databank cost.** `bank_bands` first reads every `band_id` in `fact_band_outcome` (lines 273–280; 22,022 rows on 30 Sep). Measure that read. If it is slow, restrict it to the window's markets rather than reading the whole table. Keep the "freeze the whole ladder or nothing" rule untouched.
- **Minutes.** Measure both steps on a real run and add them to `MEASURED_MINUTES` in `tests/test_github_actions.py`. If the monthly total would pass `SCHEDULED_MINUTE_BUDGET`, stop and ask Hassan. Only P6.1 may change the budget, with the reason written into the constant.

**3. Alternative, if the venue resolves US markets only late in the day.** Run the same two steps in the 16:36Z intraday run only. Decide from the measurement, not before.

**4. Do not move `pipeline_daily`** away from 04:36Z to fix this. The archive (02:36Z), the morning learners and the 05:30Z check-ins are staggered around it (PLAN_PROGRESS P6.1 row).

### Tests (`tests/`)
- `paper_settlement` CLI: `--unconfirmed-only` reaches `cycle(unconfirmed_only=True)`, and `--days-back` is honoured.
- databank on a fixture:
  - a market confirmed with every band confirmed and one winner is banked;
  - a market with one band unconfirmed is not banked;
  - a second run banks nothing new;
  - an already banked band is never rewritten.

  Some of this exists; find it with `grep -rn bank_bands tests/`.
- `tests/test_github_actions.py`: the new steps are in `pipeline_intraday.yml`, no new `schedule:` key appears, and `MEASURED_MINUTES` is updated.

### Acceptance
Three consecutive days, measured with query F1-a in `04_VERIFY_QUERIES.sql`:
- US days banked within a lag set from the venue measurement (for example ≤ 10 h after the day ends if the venue resolves by 08:36Z). State the number measured, not a target invented beforehand.
- As `anon`, every US city's latest `for_date` in `v_city_hit_history` equals yesterday by the end of the first run after the venue resolves (query F1-c).
- No `pipeline_intraday` run fails or overruns its timeout because of the new steps.

Then mark P4.7 accepted in PLAN_PROGRESS.

---

## F2. Every city in "Hit rate, per city, per lead"

### What Hassan sees
The table lists a handful of cities; most US cities are absent.

### Root cause (measured 30 Sep, as `anon`)
- `web/app/predictive/page.tsx` line 264 fetches `v_prediction_scorecard_all` filtered to the active cities with `.limit(4000)`, and **no `.order()`**.
- Line 1016 renders `(scoreQ.data ?? []).slice(0, 120)`.
- The view returns **797** rows for the 48 active cities: 555 °C and 242 °F, one per city × model × lead with enough days.
- The first 120 rows, in whatever order Postgres returns them, covered **6 °C cities and 2 of 11 US cities**. The other 40 cities were cut, with no warning on the page.
- The view is defined in `sql/ad4_62_settled_history_ungated.sql`.

### Design
- **Query** (line 263–267): add `.order("city_key").order("model").order("lead_days")`. Order in the query, not in the browser, so the cap and the order agree.
- **Rendering** (line 984 onwards): use the page's existing city selector state (`active`, `setCity`) or a second selector.
  - Show every row for the chosen city, or all 797 rows grouped under a city heading.
  - Never `slice` without saying so. If a cap stays, show "showing N of M" as `DataState`'s `truncated` prop does elsewhere on this page.
- **"Actual against predicted"** (line 820; `accuracy.byCity.slice(0, 8)` at line 929): label it "the 8 cities with the largest bias", or show all cities. It is a deliberate cut but unlabelled.
- Units: the table header says "Within 1 °C". Check what `hit_rate_pct` and `within_1c_pct` mean for a °F city in `ad4_62` before labelling them, and label them honestly.

### Tests and checks
- `cd web && ./node_modules/.bin/tsc --noEmit && npm run test:routes && ./node_modules/.bin/next build`.
- `test:routes` runs `tests/operator-auth.test.cjs` and `tests/city-cards.test.cjs`. There is no predictive page test yet.
- If the city grouping is a pure function, add a small test beside `city-cards.test.cjs` proving all 48 cities come out, not 8.

### Acceptance
- On the built page, or by the test, every active city appears, including all 11 US cities.
- Query F2-a in `04_VERIFY_QUERIES.sql` returns 48 distinct cities from the ordered query.

---

## F3. Then improve the prediction itself

Nothing below is proven to beat the market day-ahead. Measure before switching anything on. Rule 11 applies: prior, bounds, minimum sample, maximum step, version, and never evaluated on its own training data.

**1. °F: the forecast tournament's champion beats the live engine** (P3.8 part 2).
- Evidence, `derived_hit_summary` at 30 Sep 05:06Z, as-of lane, 121 days:
  - °F: log loss 1.670 against the engine's 1.785, gain +0.115 [+0.026, +0.251];
  - against the market still −0.282 [−0.339, −0.197];
  - °C: −0.022 [−0.068, +0.060], so no gain.
- The per-city gate needs 20 settled days against the live engine. The most any city had was 12 (`derived_hit_recipe`, 0 of 49 would pass). The earliest pass is about 8 Oct.
- Build, in `scripts/hit_tournament.py` and `scripts/probability_engine.py`:
  - a recipe's `state` goes `shadow` → `live` only through the gate (today it is hard-coded `'shadow'`, around line 520);
  - the engine prices a city from its `live` recipe;
  - `recipe_version` is written on every `band_probabilities` row;
  - the engine's reasons are stored **once per ladder**, not per band row. That is 517 ladders against 5,687 rows in the 24 h to 30 Sep ~08:30Z, which matters while the database is over its cap.

**2. The width when the forecasts disagree.**
- Hassan's San Francisco case of 24 Sep (PLAN_PROGRESS P3.8): Open-Meteo said 80–83 °F against NWS's 73.4 °F, and the engine's sigma of about 1 °C put 0.1% on the 74–75 °F bucket the market favoured.
- The engine has a disagreement multiplier (`v_forecast_divergence`; `scripts/probability_engine.py` around lines 645 and 1410). It is **not applied** on the post-processed path, nor when the width is "measured": the reasons record `measured_width_kept:…_not_applied_to_sigma`.
- Measure first how many live ladders take each path; count the reasons text over the last few pricing runs.
- Then score, on settled days, whether applying the multiplier on those paths improves winning-bucket log loss, before changing it.

**3. After the peak (S10).**
- `docs/S10_REPLAY_2026-09-30.md`: postpeak_1h top-1 against the market +15.6 pts [+11.2, +22.2], 23 days.
- The morning is the market's: −9.1 pts [−14.7, −3.5].
- #242 found no edge after trading costs post-peak; a leak was corrected there.
- S10 runs in shadow. Its model is `rd1:2026-09-25:f5372ebb05` from 30 Sep 08:36Z.
- The scheduled S10 refit is not built. It needs input builders, a Rule 11 maximum step and measured minutes (PLAN_PROGRESS P7.2).

**Background for every change here:** `docs/MODEL_VS_MARKET_2026-09-28.md`.
- On 8,406 venue city-days, the engine's centre is further from the winner than the market's: 0.800 against 0.671 buckets.
- The width is roughly calibrated.
- The model adds almost nothing to the market's own price (walk-forward weight 0.02 → 0.00).
- A change that does not move the centre toward the winner will not close the gap.
