# Plan progress — `docs/AD4_IMPROVEMENT_PLAN.md` (v2)

This file is where a new session resumes. Keep it accurate. Update it in every
PR that advances a step.

- **Statuses:** `todo` · `doing` · `done` · `skipped` (the finding no longer
  holds; the reason is in the notes) · `blocked` (the reason is in the notes).
- **Done** means the step's acceptance check passed against the **live**
  database or the live Actions API, and the query and its output are in the PR
  description. A green workflow is not enough.
- The plan was written against `main @ 908427c`. Its line numbers are hints.
  Re-verify every finding in the code before changing anything.
- Every number in this file names the query or command it came from.

## Gates (🔶 = stop and ask Hassan)

| Gate | Question | Status |
|---|---|---|
| P0.2 | Repo private? | ✅ decided; the GitHub API reports `private: true` (see the session log) |
| P0.3 | Retire the existing desks? | ✅ decided: retire all of them, keep all history |
| P6.1 | Where does hourly work run? | ✅ decided: GitHub Actions, one minimum-cost tick |
| P5.9 | Fixed safety rails | 🔶 open. Build with the defaults (5% daily loss, 3% per city-day, 8% per cluster-day, price bound 0.97), marked `pending_approval` in `risk_rails.py` |
| P5.10 | Activate the portfolio account, and its bankroll | 🔶 open |
| P8.1 | Consolidate the nine strategies into S10 / S11 / S12 / S2 / S13 | 🔶 open |
| P7.7 | Which S10 variant(s) go to the portfolio | 🔶 open |

## Merge rules (from the kickoff)

Claude merges a PR itself when CI is green and the acceptance check passes.
Hassan merges any PR that:
- changes grants, auth or security;
- deletes or rewrites existing rows;
- touches paper accounts or money settings;
- changes Actions schedules or budgets beyond what P6.1 specifies.

**In practice (session 1):** Claude merged #104 itself; the auto-mode guard
flagged it. Hassan then told Claude to merge #105–#110 in order, which Claude
did. **Hassan, 23 Sep, after #113: "yes, u can merge from here … keep that in
mind for later."** Claude merges its own PRs once CI is green. The categories
above still get Hassan's eye first: say so in the PR and ask.

## Checklist

| Step | Title | Status | PR | Acceptance result | Notes |
|---|---|---|---|---|---|
| — | Progress file and `CLAUDE.md` pointer | done | [#104](https://github.com/hassansab00/arbdesk4/pull/104) | n/a | Also adds the plan itself as `docs/AD4_IMPROVEMENT_PLAN.md`. |
| **P0.1** | Capture the baseline | done | [#105](https://github.com/hassansab00/arbdesk4/pull/105) | `docs/baseline_2026-09-23.md` holds size, A1, A2, A4–A7 and decision-time hit rates | Decision-time hit rates are a proxy (B1 in `tools/audit_baseline.sql`) until P4 writes checkpoint rows. Noon: model 23.3%, market 52.4%. |
| **P0.2** | Repo goes private | done | [#105](https://github.com/hassansab00/arbdesk4/pull/105) | `GET /repos/hassansab00/arbdesk4` → `"private": true` | `compute_budget.md` and the budget comment in `test_github_actions.py` say private, 2,000 min. **Not verified:** the anonymous `git ls-remote` (this container's proxy authenticates GitHub requests). The n8n webhook-path rotation belongs after P1.2. |
| **P0.3** | Retire every existing paper desk | doing | [#106](https://github.com/hassansab00/arbdesk4/pull/106) | migration applied live 23 Sep; `v_paper_desks` re-created and checked (4 rows, EXCEPT ALL 0/0 on the 20 old columns). **Pending:** `retire_desks.yml` must be started by Hassan, because this session's GitHub token cannot dispatch workflows (403 "Resource not accessible by integration"). | Plan findings that did not hold: `export_paper_trades.py` exports only closed `paper_trades`; `web/public/archive/index.json` carries no sha256; `paper_accounts` had no `status`; `queue_plan` never read `archived_at`. |
| **P1.1** | Revoke PUBLIC execute on destructive functions | done | [#108](https://github.com/hassansab00/arbdesk4/pull/108) | A1 = **0** (was 4). SECURITY DEFINER functions in `public` executable by PUBLIC: 0. A2 (all anon-executable SECURITY DEFINER): 23 → 13. service_role keeps all four. `prune_trades` dry runs, before → after: cutoff 31 days ago 13,020 → 13,020; default 13,047 → 13,047; `p_before => now()` 96,312 (the whole table) → 13,339 (= rows older than the 30-day floor). | Includes `alter default privileges for role postgres revoke execute on functions from public` (Hassan: "do what you find necessary"). |
| **P1.2** | Put UI writes behind auth | doing | [#109](https://github.com/hassansab00/arbdesk4/pull/109) + plan/p1-sign-in-off | pending: apply the RPC revoke, then A2 | Every browser write goes through a server route using the service key; `settings` no longer shows `operators` or `n8n_webhooks` to anon (applied live 23 Sep). **Hassan (23 Sep): no sign-in for now - he is the only user.** Sign-in is off by default (`OPERATOR_SIGN_IN=required` turns it back on); the site's boundary is Vercel's deployment protection, as for reads. Remaining: revoke the 13 write RPCs from anon and authenticated, then A2 must be 2 (the allowlist). |
| **P1.3** | Align archiver floors with the SQL prune floors | doing | [#102](https://github.com/hassansab00/arbdesk4/pull/102) + [#107](https://github.com/hassansab00/arbdesk4/pull/107) | pending: the next archive run must log research, resolution and trades `ok` | A preflight refusal now logs `status='error'` with the function's reason. The last run before #102 (08:16Z, 23 Sep) logged `attention` with `preflight.ok=false` for all three. |
| **P1.4** | Stop backfills flooding `research_captures` | done | [#107](https://github.com/hassansab00/arbdesk4/pull/107) | Live, in a rolled-back transaction on `prob_id` 72427: captures 82,350 before; **82,350** after an update of `pricing_block_reason` (non-pricing); 82,351 after a `calibrated_prob` change (the positive control). | |
| **P1.5** | Guard the prune on a confirmed push | doing | [#107](https://github.com/hassansab00/arbdesk4/pull/107) | pending: the next archive run | Code merged; its proof is a real run. |
| **P1.6** | Get under the cap | todo | | | 622.4 MB = 124.5% of the 500 MB tier (`select storage_pressure()`, 23 Sep 08:56Z). |
| **P2.1** | Read every METAR | merged, backfill pending | [#112](https://github.com/hassansab00/arbdesk4/pull/112) | pending: the 30-day backfill (`observations.yml`, `days=30`), then A4 ≥ 97% | Step 1 verified live 23 Sep. IEM `report_type=3` returns one routine report an hour; `3+4` (routine + specials) reproduces the venue's WRH maximum in every case checked: EGLC 17 Sep 24 rows/21 °C → 48/22 °C (WRH 22); EHAM 12 Sep 24/21 °C → 48/22 °C (WRH 22); EHAM 17 Sep 47/17 °C (WRH 17); LGA 20 Sep 24/71 °F → 37/72 °F, with the 01:04Z special (WRH 72); DAL 13 Sep 99 °F both (WRH 99). `report_type=1` returned nothing for EGLC and EHAM. For US stations, 3+4 adds only specials, not the five-minute feed. Step 2 done: `scripts/ingest_observations.py`, the n8n template, and the live n8n P1.6 workflow (published 23 Sep, version 745c623e) all send 3 and 4. Step 3 (WRH fallback) was not needed. |
| **P2.2** | Fix `v_station_day_max` / the agreement view | doing | plan/p2-station-agreement | pending: merge, apply `sql/ad4_82`, re-run A4 | Finding confirmed (Dallas 13 Sep: NWS KDAL 100.4 °F at :55 leaked past the ±4-minute window; the routine report was 99.0 °F). The hourly columns now read the primary source (`obs_primary_source()`, `'IEM'` until P2.1) with no minute window; `venue_round(value_c, unit)` rounds to whole degrees in the canonical unit. Live, 23 Sep, 638 settled ladders with a reading: 579 agree today → 587 with this change; the 51 left are 46 below and 5 above (P2.1's missing reports). New PGlite harness `settlement-agreement.cjs` with the Dallas, NYC and London fixtures, mutation-checked. |
| **P2.3** | Rebuild `observation_trust` from venue evidence | blocked | | | Refits on the corrected source, so it waits for P2.1. |
| **P2.4** | Keep market state current | done (live), PR open | plan/p2-close-ended-days | **A5 = 0** (was 380), live 23 Sep 16:03Z | **Hassan decided (23 Sep):** a market whose local day has ended is closed; if its temperature was not settled, that is a real issue. `20260923140000_a_market_whose_day_ended_is_closed.sql`: `market_day_ended(city, date)` (resolution_date < the city's local today); a BEFORE INSERT/UPDATE trigger on `markets` that closes an ended day and stamps new `closed_time` / `closed_reason`, so a P0.2 re-poll (which posts Gamma's `closed=false` until UMA rules) can no longer reopen it; `refresh_market_state()` closes the rest and copies the venue's confirmed winner into `winning_band_id` / `resolved_band_id` / `resolution_verified_at` / `resolution_source_used` where all are null. It runs hourly inside the existing `ad4_refresh_venue_band_resolution` pg_cron job (no new schedule). `sql/ad4_87` `v_market_settlement_gaps`: ended markets in active cities with no temperature in any of `v_station_day_max` / `fact_band_outcome` / `fact_forecast_outcome` (3 h grace, from the city's collection start), a disputed venue settlement, or a stored winner the venue disagrees with. Before, 16:02Z: 1,855 markets, 1,350 closed, 425 open with an ended day, A5 380, 0 with a winner, 851 venue-confirmed. After, 16:03Z: 1,775 closed (425 by the rule), open with an ended day 0, A5 0, 851 with a winner, 0 disagreeing with the venue, 80 open (all not ended), gaps view 0 rows (444 ms). The 34 no-temperature markets found are all in retired cities (Jinan, Hong Kong, Taipei), so the view leaves them out. Not yet verified: the first hourly cron run (17:07Z). The plan's Gamma re-poll was not needed: the venue evidence `paper_settlement.py` already collects (Gamma + CLOB, both closed and resolved) is the source. |
| **P2.5** | One bucket convention everywhere | done | [#114](https://github.com/hassansab00/arbdesk4/pull/114), plan/p2-canonical-for-the-browser | live 16:38Z: the three browser views read the canonical bucket; anon reads all of them and still cannot read `v_canonical_*` or the corrections | Measured 23 Sep: raw bounds differ from canonical on 9,183 of 20,161 bands and the raw unit on 667 of 1,855 markets, all dated 2025-01-20 to 2026-09-05 (every band since 6 Sep is already half-open with the right unit). Live pg_depend: 5 views read raw bucket columns (`v_band_price_history`, `v_opportunities`, `v_prediction_ladder_bands`, `v_city_day_readiness`, and `v_opportunities_candidate`, which is in no repo file and read by nothing); 0 functions. **Scripts switched:** `databank.py` (freezes `fact_band_outcome` from `v_canonical_bands`), `paper_exits.py` (bands and unit), `weather_outcomes.py` (the rule's unit from `v_canonical_markets`); checked as `service_role` live. `tests/test_one_bucket_convention.py` lists every raw read in `scripts/` and every raw bucket statement in `sql/`, mutation-checked. **Incident, 23 Sep 16:23:22–16:26:05Z:** the three owner-rights views were switched to the canonical views live and every anon read of them (and of `v_band_ladder`, `v_trade_plan`, `v_prediction_ladder`, …) failed with `permission denied for table proprietary_data_corrections`: `v_canonical_*` are `security_invoker`, which checks the base tables as the querying role even through an owner-rights view. The equivalence checks had run as the owner. Reverted in 2 min 43 s; anon reads re-checked (40,781 / 1,760 / 17,611 rows). A test now pins the rule. **Hassan chose (a), 23 Sep:** `20260923150000_the_browser_reads_the_canonical_bucket.sql` sets `security_invoker = false` on `v_canonical_bands`/`_markets` (still granted to `service_role` only), and `v_band_price_history`, `v_opportunities` and `v_prediction_ladder_bands` read them. Rehearsed as anon in a rolled-back transaction, then applied; as anon at 16:38:36Z: 41,154 / 1,760 / 17,611 rows, dependents all readable, `has_table_privilege(anon, …)` false on both views and on the corrections; 7,340 ladder rows (21 Aug–5 Sep) now carry corrected bounds. `paper-contracts.cjs` asserts both halves and fails with the incident's error without the migration. The options were: (a) set `security_invoker = false` on `v_canonical_bands`/`v_canonical_markets` (anon still has no grant on them or on the corrections, but owner-rights views could read through them), or (b) leave the anon views raw. Cost of (b), measured: `v_band_price_history` and everything on `v_opportunities` read only today onward, where raw = canonical (40,781 and 880 rows, EXCEPT ALL 0 both ways); `v_prediction_ladder_bands` (and `v_prediction_hindsight` on it) shows raw bounds on 7,340 of 17,611 rows, 21 Aug–5 Sep. `v_city_day_readiness` is invoker itself and today-onward, so it stays raw either way. |
| **P2.6** | Real forecast issue times | done (live), PR open | plan/p2-forecast-issue-times | live 16:55Z: `v_forecast_issued` over 77,183 rows; 2 day-ahead rows issued on the local target day now left out of skill | Measured 23 Sep, `run_at` per writer: NWS (2,609 rows) = its `updateTime`, 2.28 h before our fetch; Open-Meteo live (41,744) = our fetch time; previous-runs backfill (32,830) = synthetic midnight UTC of for_date − lead, on 10,920 rows not even the stated lead in local time. `20260923160000_when_was_the_forecast_issued.sql`: a view (not a 77k-row backfill: the DB is at 131% of the tier) giving `issued_at` + `issued_at_source` (`provider_update_time` / `ingest_time` / `ingest_time_true_issue_unverified`), plus `issued_local_date`, `issued_lead_days`, `same_day_issue`, which are null where the issue time is only an ingest bound. As-of reads (`regime._forecasts_for_date`, `_history_disagreement`, `probability_engine._forecast_for`) cut on `issued_at`; live pricing still reads the table unchanged. `measure_skill.py` drops lead ≥ 1 rows issued on the local target day. **Rehearsal caught** a derived same-day flag on all 32,830 backfill rows, which would have emptied day-ahead skill; fixed before applying. **Deliberate consequence:** backtest city-days with a forecast knowable by local midnight drop from 5,076 to 1,164 (first 6 Sep), because the backfill's true issue time is unverified (open-meteo.com unreachable here; P7.2 verifies it and one CASE branch restores them). Backtests run only when queued by hand. Not yet verified: the 04:00 UTC `measure_skill` run on 24 Sep reading the view. |
| **P2.7** | Label live-weather sources | done (live), PR open | plan/p2-label-live-weather | live 17:05Z, as anon: 0 of 37 model-fed cities have a floor above their station series (was 10); station cities unchanged (0 of 11) | Measured 23 Sep: `live_weather` is Open-Meteo **model** output for 37 of 48 active cities. `refresh_live_weather_timing()` folded that value into `running_max_c` (and kept it all day), and `v_city_observation_health` fed it into the floor: 10 cities' floors sat above everything their station measured, Seoul 23.2 vs 21.0 °C. Fixed at the source (the timing function folds only `source_kind = 'station'` values; kept extremes too), in the view (`latest_temp_today_c`, `stored_running_max_c`, the basis and the note are station-only; `v_city_running_max` gains `live_source_kind`), and behind both: `probability_engine.measured_floor()` caps any floor at the station series unless the live row is a station, and `signal_engine` falls back to `live_weather.running_max_c` only for a station. Rehearsed live in a rolled-back transaction: 11 station cities 0 changed; 10 model floors fell to the station series (Seoul 23.2→21.0, Toronto 17.9→16, Amsterdam 21.6→20, Tel Aviv 31.2→30, Cape Town 21.2→20, Karachi 36.9→36, Jeddah 36.8→36, Warsaw 15.7→15, São Paulo 16.5→16, Moscow 17.1→17). `observation-health.cjs` gains the model cases (mutation-checked: fails with the Seoul message on the old SQL). `live_weather.py` (manual fallback) sent UTC dates only, an empty window whenever the 3 h fell in one UTC day; now sends sts/ets. **Metadata, evidence from the venue's current rules text:** all 48 active cities' rules cite `weather.gov/wrh/timeseries?site=<own ICAO>`, none Wunderground. 34 stale Wunderground `resolution_url` values set to the cited WRH URL (the old URLs stay rebuildable from `wu_path`, checked in the same transaction); the three wrong `wu_path` values (Toronto `cn/jinan/ZSJN`, Zhengzhou `id/jakarta/WIHH`, Ankara `tr/`) set from each row's own URL; `nws_station_id` = ICAO for the 11 US cities. Root cause of the null station ids: n8n P1.3 never loaded `icao` and PATCHed `nws_station_id = null` every run over what P1.2 wrote; template and live workflow fixed and published (version f80be4ad). Not yet verified: the next P1.3 run leaving the station ids in place. |
| **P3.1** | Fix the floor atom | todo | | | |
| **P3.2** | Stop pricing after the local day ends | todo | | | |
| **P3.3** | Gate the trajectory on fresh readings | todo | | | |
| **P3.4** | Forward-only fits with a significance gate | todo | | | Hassan asked 23 Sep whether each city learns why it misses. Measured then: priced day-ahead MAE 1.12 °C over 49 cities (raw 1.27); the equal-weight bias correction made 21 of 49 cities worse (Chicago raw −0.6 → priced −1.6 °C); picking each city's best model + bias on older days and scoring the last 20 gave 1.23 vs 1.21 for the plain model mean (no gain, 12 of 35 cities under 1 °C). Two gaps to add here: per-city model weighting (the engine prices whichever of 3 models ran last), and a bounded recent-bias term on the day-ahead path (P7.2 has one only for the same-day model). |
| **P3.5** | Fix the skill sample size | todo | | | Also measured 23 Sep (for this step, not changed in P2.6): `one_row_per_run_key` keeps the newest run per key, so lead-0 skill is scored on runs fetched at 21.9 h local on average (MAE 0.91 °C over 582 city-days), while runs fetched before 09:00 local miss by 1.20 °C. Lead-0 sigma is therefore measured on forecasts sharper than a morning price sees. Choose the lead-0 run by the time pricing happens. |
| **P3.6** | Re-measure calibration after the fixes | todo | | | Needs 7 days of P3.1–P3.3 running. |
| **P3.7** | Small engine fixes | todo | | | |
| **P4.1** | `prediction_checkpoints` table | todo | | | |
| **P4.2** | Checkpoints on the city's local clock | todo | | | The writer is `tick.py` (P6.1). |
| **P4.3** | Freeze facts at a declared cutoff | todo | | | |
| **P4.4** | Quarantine pre-9 Sep facts | todo | | | |
| **P4.5** | Bank what was missed, keep proof on the row | todo | | | |
| **P4.6** | Decision-time scoreboard view | todo | | | |
| **P5.0** | Prerequisite fixes | todo | | | |
| **P5.1** | Account model: shadow ledgers and a portfolio account | todo | | | Paper accounts, so Hassan merges it. |
| **P5.2** | Strategy lifecycle states | todo | | | |
| **P5.3** | Belief layer | todo | | | |
| **P5.4** | Execution cost model | todo | | | |
| **P5.5** | Holdings solver | todo | | | |
| **P5.6** | Timing: act now or wait | todo | | | |
| **P5.7** | Order manager and fill simulator | todo | | | |
| **P5.8** | Nightly learning loop | todo | | | |
| **P5.9** | Risk layer 🔶 | todo | | | Build with the defaults, `pending_approval`. |
| **P5.10** | Meta-allocator 🔶 | todo | | | |
| **P5.11** | Decision log | todo | | | |
| **P5.12** | One engine for live and replay | todo | | | |
| **P5.13** | Archive what research needs | todo | | | |
| **P6.1** | Hourly tick on Actions | todo | | | Start as soon as P3 is merged. |
| **P6.2** | n8n under 2,000 executions a month | todo | | | |
| **P6.3** | Watchdog | todo | | | |
| **P6.4** | Secure webhooks and the dispatch token | todo | | | |
| **P6.5** | Slow and broken board views | todo | | | |
| **P6.6** | Docs clean-up | todo | | | |
| **P7.1** | S10 contract and eligibility | todo | | | Can start once P2 is merged. |
| **P7.2** | Remaining-day model | todo | | | |
| **P7.3** | Replay harness | todo | | | |
| **P7.4** | S10 inside the tick | todo | | | |
| **P7.5** | S10 view and variants | todo | | | |
| **P7.6** | Shadow mode | todo | | | |
| **P7.7** | S10 into the portfolio 🔶 | todo | | | |
| **P7.8** | Board changes for S10 | todo | | | |
| **P7.9** | End-to-end chain | todo | | | |
| **P8.1** | Consolidate the strategies 🔶 | todo | | | |
| **P8.2** | Implementation per strategy | todo | | | |
| **P8.3** | Suite-level acceptance | todo | | | |
| **P8.4** | Conflict and interaction rules | todo | | | |

## Measured minutes (Actions)

`MEASURED_MINUTES` in `tests/test_github_actions.py` holds the per-workflow
figures. Record the A11 projection here after P6.1.

| Date | 7-day billable minutes | Monthly projection | Command |
|---|---|---|---|
| — | not measured yet | | A11 |

## Session log

### 23 Sep 2026 — session 1 (kickoff)
- **Access checked:**
  - Supabase: the MCP SQL tool connects as `postgres` to `jittmxhzgqpifitwupss`.
  - GitHub: the MCP tools and the REST API work as `hassansab00`. `GET /repos/hassansab00/arbdesk4` returns `private: true`, and the Actions runs API answers. There is no `gh` CLI in the container, so A11 is run with `curl` against the same endpoints.
  - n8n: the MCP tools work (55 workflows visible).
  - Supabase service-role key: **not** in the container environment. Scripts that talk to PostgREST with the service key can't be run locally, so those steps go through the MCP SQL tool or a `workflow_dispatch`.
  - Vercel: the MCP tools are listed; not exercised yet.
- **Live DB:** `select storage_pressure()` at 08:56Z returned `db_mb 622.4`, `tier_mb 500`, `pct_of_tier 124.5`.
- **`main` has moved** past the plan's base: `908427c` → `9fcb411` (PRs #102 and #103). #102 already fixed the P1.3 floors.
- **End of session 1 — state for the next session:**
  - Open PRs, none merged. Each needs Hassan's merge (the auto-mode guard blocked Claude's merges after #104):
    - [#105](https://github.com/hassansab00/arbdesk4/pull/105) P0.1/P0.2 docs;
    - [#106](https://github.com/hassansab00/arbdesk4/pull/106) P0.3 retire desks (stacked on #105);
    - [#107](https://github.com/hassansab00/arbdesk4/pull/107) P1.3–P1.5;
    - [#108](https://github.com/hassansab00/arbdesk4/pull/108) P1.1;
    - [#109](https://github.com/hassansab00/arbdesk4/pull/109) P1.2;
    - [#110](https://github.com/hassansab00/arbdesk4/pull/110) P2.2.
  - Several PRs append a block before `await db.close()` in `tests/database/paper-contracts.cjs` and edit its PASS line. Expect merge conflicts there once the first lands. Resolve by keeping every block.
  - Nothing has been applied to the live database yet. Each PR says what to apply after merge and how to check it.
  - Blocked: P2.1 and P2.3 on network access (see P2.1). P2.4 on the decision above. P1.2 on Vercel/Supabase Auth settings (see P1.2).
  - Live DB still over the cap: 622.4 MB at 08:56Z. The 03:00 UTC archive run on 24 Sep is the first with #102's floors.


### 23 Sep 2026 — session 1, continued
- **Merged (Hassan authorised the order #105 → #106 → #107 → #108 → #110 → #109):** #105–#110, then [#111](https://github.com/hassansab00/arbdesk4/pull/111) (sign-in off: Hassan is the only user; writes still go only through the server) and [#112](https://github.com/hassansab00/arbdesk4/pull/112) (P2.1).
- **Applied live:** P0.3, P1.1, P1.2, P1.4, P2.2, and P2.4 (this PR). Their acceptance results are in the rows above.
- **Needs Hassan (this session's token cannot dispatch workflows, 403):**
  - `observations.yml` with `days=30`, which is the P2.1 backfill. Then re-run A4.
  - The 03:00 UTC archive run on 24 Sep. It is the first with the P1.3/P1.5 guards; P1.6 is checked after it.
- **To check next session:** the first hourly `refresh_market_state()` run (cron job `ad4_refresh_venue_band_resolution`, :07). Check it in `cron.job_run_details`, and check that A5 is still 0.
