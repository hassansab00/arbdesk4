# ArbDesk4 — improvement and implementation plan

Prepared for Hassan · 8 September 2026 · Paper trading with real market data

This plan is based on the updated repository and direct, read-only inspection of its Supabase database. The goal is a working research and paper-trading desk whose recommendations, simulated fills, cash balances, settlements, and performance can be independently reconstructed.

**Updated scope:** improve `hassansab00/arbdesk4` directly. Preserve its existing design, pages, strategies, data, and workflow capabilities. Add a dedicated **Paper Trades** section supporting manual, approval-based, and automatic paper trading. GitHub Actions efficiency is an explicit deliverable, measured against retained coverage, freshness, correctness, and CI protection. This is an incremental improvement program, with compatible migrations and reviewable changes in the existing repository.

**The immediate priority is to restore valid market inputs and repair historical data.** Adding strategies before that would produce more calculations from unreliable inputs. The next priority is a complete paper-trade lifecycle; only then should model and strategy performance drive expansion.

This document specifies the work to build. No production code, database schema, settings, or n8n workflows were changed during this review. Importable workflow JSON and executable migrations are implementation deliverables described below, not files claimed to have been built already.

## 1. Scope and evidence

| Item | Reviewed state |
|---|---|
| Repository | `hassansab00/arbdesk4`, branch `main` |
| Commit | `eafc0bc3e21bd2f84a2b9038d033105bc54e83f0`, committed 7 September 2026 |
| Repository inventory | 256 tracked files; 33 differ from the earlier audit snapshot. Updated file contents were checked against Git blob hashes. |
| Supabase project | `jittmxhzgqpifitwupss`, PostgreSQL 17 |
| Database footprint | Approximately 352 MB, 49 public tables, 70 public views at inspection |
| n8n material | All 12 repository workflow templates, scheduling configuration, credential binder, validation scripts, and database ingestion records |
| Tests | GitHub CI for the reviewed commit reports 575 passed, 4 skipped. The latest relevant web CI reports successful typecheck and build. |
| Direct limitations | No authenticated n8n instance inspection, deployed-browser walkthrough, or external source-to-database end-to-end test was performed. The current local runtime lacks pytest; the test count above is from GitHub job logs, not a new local test run. |
| Implementation target | The existing `hassansab00/arbdesk4` repository and its connected database. No duplicated application or repository is planned. |
| GitHub Actions audit | All 11 workflow YAML files reviewed; schedules, trigger filters, continuation behavior, concurrency, and representative job/step durations examined. Actual account billing and a complete monthly invoice were not available. |
| UI audit | Existing routes, navigation, shared components, data hooks, and execution calculations reviewed in source. Deployed visual, interaction, accessibility, and mobile checks remain implementation acceptance work. |

Database results were collected through separate reads on 8 September, so they form an operational snapshot, not one transactionally consistent database export. Large table counts from statistics are estimates unless explicitly described as counted.

**What recent work already improved:** local-day grouping in skill measurement, databank fallback, and backtest observations; deterministic newest-run selection in the probability engine; a model-specific skill table and conditional use of it; repeated forecast-run deduplication for skill; trade city-key repair; faster inventory reads; clearer backtest outcome fields; and portable n8n credential binding. Preserve these changes. Some gaps remain in neighboring code and in previously stored rows.

**Corrections to the earlier assessment:** paper-only operation is intended. All current signal rows are system alerts, not trade recommendations. S2/S6 carry multiple band IDs inside one signal; S8/S9 emit separate leg signals and need coordinated execution for a different reason. Platt calibration already exists. Exported n8n templates being inactive does not establish that deployed workflows are inactive.

## 2. What the live system establishes

| Priority | Confirmed finding | Consequence and planned response |
|---|---|---|
| P0 | 943 markets; latest resolution date is 5 September. Zero markets are dated 8 September or later. Latest market `last_seen_at` is 3 September. | Discovery is not keeping the database current. Restore discovery and distinguish “no market published” from “collector failed.” |
| P0 | 35,918 book snapshots; newest is 3 September at 15:00:39 UTC. | Current executable prices cannot be inferred from this archive. Restore book capture and require a fresh snapshot before any paper fill. |
| P0 | 6,157 non-tail bands have equal lower and upper bounds. | Current half-open interval consumers interpret these as empty. Introduce canonical band definitions and reparse verified contract labels. |
| P0 | 697 markets are marked Fahrenheit while their city records say Celsius. London’s sampled contract text explicitly says Celsius, while its market row says F. | Units need reconciliation against each contract, not an unconditional copy from either table. |
| P0 | Of 5,605 band outcome facts across 531 city-days, 410 city-days have no winning band. | These are not a reliable settled-contract dataset. Quarantine them from calibration pending evidence-based reconstruction. |
| P0 | Among 1,207 outcome facts for Fahrenheit cities, 155 labels change if the observed Celsius maximum is first converted to Fahrenheit under the existing interval rule. | Confirms a unit-handling defect. This is a diagnostic disagreement count, not a claim that the converted result alone is the final correct settlement. Bounds and rounding also need repair. |
| P0 | 17 of 54 active cities lack coordinates; Hong Kong also lacks a timezone. Latest Open-Meteo run requested 37 cities. | Missing metadata directly limits coverage. Resolve station coordinates and timezone from authoritative station/contract records. |
| P0 | At the freshness check, 11 live-weather timestamps were more than five minutes in the future; 36 readings were over three hours old. | Repair timestamp conversion and reject future-dated observations from trading context. |
| P0 | 13 weather rows simultaneously carry `source=open-meteo` and `obs_source=NWS`. | Partial overwrites can leave stale provenance or derived fields. Separate source observations and rebuild the current-weather projection atomically. |
| P0 | All 998 signals are `system / ALERT`; 997 are pending approval, one dismissed. Zero paper trades and zero ledger entries. | There is no strategy-trading performance record to evaluate. Alerts need their own lifecycle instead of a trade-approval state. |
| P0 | `auto_approve=false`, bankroll amount is null, and `settlement_verified=false`. | These are configuration states, not all bugs. Keep them visible. A real user-selected paper bankroll is required before simulation can allocate money. |
| P0 | Live `approve_signal` updates approval flags and `acted_on`; it creates neither a trade nor a durable execution job. | Approval must queue a paper order, and “acted on” must follow a recorded execution result. |
| P0 | Two historical backtests failed writing `backtest_results`. The current writer sends `data`, while the live table still requires non-null `metrics` without a default. | Align writer and database contract, then rerun as new immutable runs. This mismatch remains despite the newly added outcome columns. |
| P0 | Latest daily run reports `settled=0 flagged=146 dry_run=0`, with resolution-source parsing failures. | Implement source adapters and contract-specific fallback rules. A green workflow run is not successful settlement. |
| P1 | Latest probability job records status `ok`, zero rows, zero city-days. Only 33 probability rows covering 11 bands exist, last computed 3 September. | Track expected coverage and useful work independently of process exit status. |
| P1 | All 52 live-weather rows lack `minutes_to_peak`. The Python strategy context reads that field directly rather than the existing timing view. | S7’s inputs are incomplete. Build one dated weather/timing context consumed by strategies and UI. |
| P1 | No ensemble rows, fitted weather models, or derived model forecasts. Latest model logs report insufficient usable data/no fitted model. | Show these components as unavailable or experimental. Do not imply an operational ensemble or trained proprietary forecast. |
| P1 | Model-specific skill has 700 rows for 50 cities, all under `open_meteo_best_match`; the live Open-Meteo template defaults to `open_meteo_forecast`. | Preserve distinct provenance and make the skill fallback explicit. Do not silently relabel different forecast products as equivalent. |
| P1 | `refresh_weather_peak` exists and the worker can execute it, but the daily log records an HTTP 400 calling it. | Capture the server response body and diagnose the actual query/contract error. Reinstalling the function blindly is not a diagnosis. |
| P1 | Supabase advisors flag 70 owner-privilege views, 27 mutable function search paths, and 14 privileged functions executable by anon. All public tables have RLS, but 11 have no policies. | Review effective access per object. Protect mutations and private settings while keeping intended UI reads working. No-policy internal tables may be intentional. |
| P2 | Four duplicate-index groups and 23 uncovered foreign keys are reported. | Inspect constraints and query plans before changing indexes; this is secondary to correctness. |

The database has substantial raw material: approximately 510,049 station observations, 330,441 forecast rows, and 127,585 observed market trades. Row count does not establish valid coverage, independent training samples, or trade profitability.

## 3. Product behavior to deliver

The desk should answer five questions clearly for every city and contract:

1. **What is available?** A real contract, its local target date, source rules, complete ladder, and current YES/NO books.
2. **What do we estimate?** A probability distribution with forecast identity, lead, calibration version, uncertainty, and evidence quality.
3. **What can this paper account do?** Affordable shares at executable prices, considering fees, existing exposure, and basket outcomes.
4. **What happened?** Approval, simulated order, partial or full fills, exits, and final settlement, with exact timestamps.
5. **Did the method help?** Performance after costs, compared with simple benchmarks on comparable data and unseen periods.

Retain the existing visual style, city selection, goals, board, campaigns, strategy controls, analytics, and workflow controls. Add the missing explanations and lifecycle views within that design. Production screens should show genuine data, empty states, or an explicit unavailable reason. Synthetic fixtures belong only in isolated tests.

### Ownership of logic

| Component | Responsibility |
|---|---|
| n8n | Fetch external feeds, validate transport/payload shape, preserve source metadata, submit ingestion batches, request computation, and report run outcomes. |
| Python engine | Forecast statistics, calibration, probability generation, strategy decisions, risk/scenario calculations, paper-fill simulation, and replay. Share the same production modules with backtests. |
| Supabase/Postgres | Canonical identities, source history, queues, account locks, transactional state transitions, ledger integrity, and fast read projections. |
| Next.js UI | Display authoritative results and submit user commands. Preview calculations must be labeled and reconciled with the authoritative engine. |
| GitHub Actions | CI, bounded historical ingestion, scheduled research, batch reconciliation, archives, and backtests. |

Avoid maintaining independent strategy rules in Python, SQL `would_fire` logic, and frontend calculators. SQL views should explain stored decisions; the UI should display the same trade plan the executor receives.

### Runtime decision

**Recovery can use the current stack.** Keep the consolidated Actions jobs while fixing feeds, contracts, and accounting. Display their actual processing delay. They currently price every four hours; that cannot support a claimed minute-level pre-peak strategy.

**The target for fast paper trading is a bounded Python worker reachable by n8n over authenticated HTTP.** Package the existing pure modules behind a small job interface, with a database queue and health endpoint. One request claims a bounded batch; the database owns leases and state. This preserves the existing strategy implementation and supports book-triggered evaluation and exits.

Before choosing deployment, inspect the actual n8n hosting/version and available compute. A self-hosted environment may already support a sidecar worker; n8n Cloud requires an external worker. Build the worker package and import instructions as part of implementation, without assuming a new paid service is available. A thin Supabase Edge Function can authenticate/enqueue UI commands, but model fitting and long replays remain outside it because of its runtime limits.

Preserve every timing-sensitive strategy and its controls. Expose its required input freshness and measured processing delay; permit automated execution only when those requirements are met. Otherwise show the explicit readiness reason and keep research/preview available. A delayed replay may be useful research but must not be labeled a forward trade decision.

## 4. Implementation sequence

Use six bounded batches with acceptance gates. Numbering indicates dependencies, not a promise that statistical validation can finish in six days.

| Batch | Deliverables | Completion gate |
|---|---|---|
| 1 — Feed, contract, and cost recovery | Source/roster audit; discovery repair; corrected market-date and band parsing; timestamp/source repair; working book capture; truthful health status; Actions cost baseline, bounded backfills, and corrected budget accounting. | Every selected city has either verified metadata and current coverage or a specific exclusion reason. No malformed ladder is admitted to pricing. Backfills retain resumable progress and cannot continue without a chain budget. |
| 2 — Database contracts and evidence | Versioned definitions/outcomes; backtest schema alignment; role policies; durable jobs; paper accounts and ledger schema; migration tests. | Old UI remains functional through compatibility views; new writes pass realistic database contract tests; repaired outcomes are versioned and auditable. |
| 3 — Paper Trades section and complete execution | New `/paper-trades` page; manual ticket; approval queue; configurable automatic mode; canonical plans; cash reservations; all basket legs; partial fills; exits; settlement; immutable activity history and account totals. | Manual, approved, and automatic real-data lifecycles reconcile from decision to settlement. Concurrent commands cannot duplicate fills or overspend; existing pages and strategy controls remain functional. |
| 4 — Research correctness | Point-in-time replay, model identity/lead handling, raw/calibrated prediction history, validation splits, baseline comparisons, model promotion rules. | Replays expose missing data; no future information enters decisions; the same inputs/version reproduce the same decisions and fills. |
| 5 — Workflow efficiency and rollout | Refined importable n8n files, shared error handling, worker orchestration, incremental Actions jobs, credential binding, schedules, import guide, and cost/coverage dashboard. | Manual imports pass, scheduled capture meets coverage/latency targets, and matched useful workloads show measured cost improvement without lost observations, decisions, or CI gates. |
| 6 — Product and strategy expansion | Decision cards, account reconciliation, replay inspection, model/strategy comparison, scenario-based allocation, selected new experiments. | UI totals reconcile to ledger; every recommendation names its evidence, costs, dependencies, and blocking reason. |

Batch 1 includes the minimal workflow fixes needed to restore capture. Batch 5 consolidates the final workflow package after the new database and worker contracts exist. Do not wait until Batch 5 to restart market-data collection.

## 5. Repository work packages

| ID | Change | Main existing files | Acceptance check |
|---|---|---|---|
| C01 | Add shared paginated REST reads using stable ordering and server response boundaries. Do not stop merely because the server returned fewer than a requested 10,000 rows. | `scripts/common.py`, `measure_skill.py`, `signals.py`, `databank.py`, `backtest/runner.py` | Deliberately cap API pages at 100/1,000 rows; outputs match an uncapped reference over the same data. |
| C02 | Restrict reads to relevant city-days and eligible market IDs before fetching bands, edges, and books. | `signals.py`, `edge_engine.py`, `probability_engine.py`, `regime.py` | Historical rows cannot push current rows beyond a REST limit; healthy data in one city cannot mask another city's stale book. |
| C03 | Centralize target-date and interval semantics: source unit, precision, rounding, inclusivity, and timezone. | Discovery parser, `probability_engine.py`, `settlement.py`, `databank.py`, SQL band helpers | Boundary cases agree across probability, contract matching, replay, and settlement, including negative temperatures, half steps, tails, and DST. |
| C04 | Complete the dated strategy context with actual lead, `mae_bands`, spread, source quality, timing, and forecast/skill IDs. | `signals.py`, `strategies/base.py`, timing views | S1 time mode and S3 can evaluate valid inputs; S7 reads measured timing; unknown values never become zero-quality evidence. |
| C05 | Create one versioned TradePlan consumed by preview, approval, execution, and replay. | `signals.py`, `paper_engine.py`, strategies, UI execution code | The UI approval targets an immutable plan ID/hash; execution can explain any rejection or changed price. |
| C06 | Resolve valid UUID model/cost versions and attach source IDs before writing trades. The current paper engine can fall back to a text cost label in a UUID column. | `paper_engine.py`, `signals.py`, `common.py` | Actual database insert succeeds with valid version FKs; every filled trade references its signal and book snapshot. |
| C07 | Replace approval flags alone with a durable, idempotent execution command. | SQL `approve_signal`, `SignalsPanel.tsx`, new worker/job module | Double-clicks produce one command. Approving an expired plan returns an explicit expiry/reprice result. |
| C08 | Implement paper-account cash and exposure updates with transaction locking. | `paper_engine.py`, new account/RPC layer | Concurrent fills cannot exceed available cash. Fee-inclusive affordability and pending reservations are enforced. |
| C09 | Execute and persist basket legs individually under one parent plan. | S2, S6, S8, S9; `signals.py`, `paper_engine.py` | Missing-leg and partial-fill cases preserve actual exposure; no incomplete basket is presented as guaranteed arbitrage. |
| C10 | Route exits to reductions of existing positions and walk the correct bid side. | `signals.py`, `paper_engine.py`, SQL `close_position` | Partial exits reduce holdings correctly and record exit fees. An EXIT never becomes a new entry row. |
| C11 | Normalize book data consistently and prefer directly captured token-side books. | `edge_engine.py`, `backtest/runner.py`, `web/lib/ladder.ts` | Raw integer level counts cannot be mistaken for ladders; YES and NO execution match recorded depth. |
| C12 | Make quote/fill costs agree across Python and UI, retaining per-level quantities and fees after share-step rounding. | `cost_model.py`, `web/lib/execution.ts`, `web/lib/costs.ts` | No infinite executable depth when depth is absent; cash reconciles after quantization; fees/spread are not charged twice. |
| C13 | Replace one generic settlement scraper with contract-specific source adapters and final-outcome reconciliation. | `settlement.py`, verification scripts, settlement RPCs | Completed market resolution is distinguishable from market closure, observation estimates, disputes, and fallback deadlines. |
| C14 | Separate immutable prediction captures from later outcome attachments. | `databank.py`, `calibration.py`, fact tables | A prediction without a final outcome remains pending, not permanently frozen as a null or false result. |
| C15 | Repair the backtest writer, checked status updates, durable job claims, chronological simulation, and portfolio mutation. | `backtest/runner.py`, `engine.py`, `metrics.py`, backtest SQL | A run completes against the installed schema; compounding changes only after realized cash events; repeated workers do not execute one run twice. |
| C16 | Align model evaluation with inputs actually available at decision time. | `measure_skill.py`, `weather_model.py`, `regime.py` | No completed-day cloud/wind/precipitation enters a morning decision; forecast-driven and observation-driven experiments are labeled separately. |
| C17 | Give system alerts their own deduplication and acknowledgment state. | `signals.py`, alert views/components | A stale-feed incident updates one active incident rather than generating endlessly changing approval items. |
| C18 | Make pipeline dependency results explicit, including zero-work and partial coverage. | Actions pipelines, `common.log_run`, `capacity.py` | Optional model failure can fall back explicitly; essential input failure blocks only dependent decisions; logs retain server error details. |
| C19 | Preserve UI design while adding compact formatting, tooltips, provenance, and reconciliation views. | Board, Live, Analytics, Backtest, Strategies, Workflows, Synthesis | Missing values differ from zero; money, prices, probabilities, and temperature use appropriate units and precision. |
| C20 | Replace competing SQL/client “would trade” calculations with projections of authoritative decisions. | `sql/ad4_34_trade_plan.sql`, campaigns/synthesis views, UI calculators | One decision ID produces the same action, legs, costs, and blocking reason everywhere. |
| C21 | Refine all 11 Actions workflows: incremental work, bounded continuation, correct exit propagation, scoped job claims, and measured budget reporting. | `.github/workflows/*.yml`, ingest scripts, `docs/compute_budget.md`, budget checks | Same eligible coverage and outputs on matched inputs; no duplicate backfill/queue work; budgets derive from actual YAML and rounded job duration. |
| C22 | Add the dedicated Paper Trades page, manual ticket, approvals, automation settings, positions, history, and trade detail. | New `web/app/paper-trades/page.tsx` and focused components; existing `NavTabs.tsx`, `SignalsPanel.tsx`, account read projections | All three paper modes use the same account/engine; every displayed fill links to its source evidence; all 17 existing navigation destinations remain available. |
| C23 | Improve shared UI reads, metadata joins, and status updates without changing page behavior. | `web/lib/useQuery.ts`, `SignalsPanel.tsx`, `DataState.tsx`, freshness hooks | Duplicate consumers share scoped results; approval updates appear promptly; background failures retain visibly dated data; missing band metadata never invents a Celsius unit. |

## 6. Database design and migration plan

### Preserve the source record

Keep raw observations, forecast captures, market identifiers, contract text, and book snapshots. Repair through versioned derived definitions and explicit superseding records. Do not erase incorrect history or silently overwrite old reported performance.

Upgrade the existing connected database in place through additive, backward-compatible migrations. Preserve existing identifiers, settings, historical records, and application reads. Version engine/configuration changes and use explicit worker ownership, with one authoritative collector per source. A canary paper account can isolate rollout tests without creating another application. Test migrations on a local/staging database first; do not assume a paid Supabase branch is required. Keep compatibility projections and entrypoints while internal implementations are improved.

### Proposed schema changes

Names below are design names. Generate actual migration filenames with the Supabase CLI when implementation starts.

| Area | Proposed structure or change | Integrity requirements |
|---|---|---|
| Market identity | Preserve `market_id`/`band_id`; add provider event/market identity and a condition/token identity crosswalk. | Detect duplicates before creating uniqueness constraints. A changed array position must not create a new contract identity. |
| Contract rules | Add `market_rule_versions` with market ID, raw text/hash, unit, timezone, precision/rounding, primary/fallback sources, revision/fallback deadlines, capture time, parser version, and verification status. | Each decision pins a rule version. Ambiguous parsing blocks eligibility and records a review reason. |
| Band definitions | Add versioned canonical bounds linked to raw labels and rule versions. | Closed intervals are converted according to the contract precision, not a blanket rule. Validate positive width, tails, order, overlap, and complete market coverage before publishing. |
| City/source readiness | Extend city metadata with verification time, authority reference, and per-source capability status. | Missing coordinates/timezone/station blocks only dependent jobs. Do not substitute city-center coordinates for the settlement station without recording that distinction. |
| Weather history | Retain source readings separately; extend with source identity, fetched time, QC status, unit, and raw/capture reference. | Uniqueness includes station/source/time; revisions are traceable. Cross-source duplicates do not inflate sample counts. |
| Current weather | Replace mixed partial writes with a rebuilt projection; create dated station-day state for running maximum and timing. | Day and source must match. Station-derived runmax cannot survive underneath a new model-only reading as if it had the same provenance. |
| Forecast runs | Separate `issued_at`, `captured_at`, `available_at`, target local date, product/model identity, and provenance class. | Backfilled lead archives are not automatically valid as-issued forecasts. Preserve whether issue time is known or reconstructed. |
| Books | Add token ID/side, source and received timestamps, source hash, actual ladder JSON, tick/minimum-size metadata, and quality status. | Positive sizes, valid prices, monotonic ladders, explicit empty books; capture both sides and linked snapshots as one batch where possible. |
| Decisions | Add `trade_plans` and `trade_plan_legs`, including input IDs, prices, shares, expiry, reasons, expected costs, rules/config/engine versions. | Immutable after emission; unique decision key; legs reference real bands/tokens. |
| Paper operation | Add versioned account automation policies, decision evaluations, and trade activity events linked to existing signals/strategies/campaigns. Record manual/assisted/automatic origin, actor, triggers, reasons, input times, approvals, and policy version. | Evaluation is distinct from order/fill. Record blocked, expired, rejected and canceled attempts. Account-scoped permissions; one economic command key across UI, n8n, and Actions. |
| Account state | Add `paper_accounts`, reservations, paper orders/fills, and position projections; retain `paper_trades` as compatible reporting where feasible. | Cash is numeric, quantities are numeric, currencies explicit. Reserve against the whole plan and release unused cash on terminal outcomes. |
| Ledger | Extend the existing event structure with linked transaction/posting records and compatible reporting. | Unique economic event key, balanced postings, immutable events, compensating corrections, and FKs connecting signal→plan→order→fill→position. |
| Jobs | Add `engine_jobs` with kind, scope, idempotency key, status, attempt count, lease owner/expiry, timestamps, result, and error detail. | Claim atomically with row locking; retries use the same economic command key. Expired leases are recoverable. |
| Settlement | Add `resolution_evidence` and versioned `market_outcomes`; attach raw source reference/hash, source role, observed value/unit, contract rule version, final status, provider result, and verification timestamps. | Weather estimates and final contract payouts remain distinct. Corrections trigger compensating accounting, not deletion. |
| Research facts | Separate prediction records from outcome links; retain raw probability, calibrated probability, market quote, horizon, source/engine versions, and eligibility. | Exclude invalid/quarantined rows from training by default. Store final payout outcome separately from profitable-after-costs. |
| Backtests | Reconcile `metrics` versus `data`, terminal timestamps, result uniqueness, progress, job ownership, and input manifests. | Completed runs immutable; reruns get new IDs; failed result writes cannot leave a false complete status. |
| Operations | Extend `ingest_log` or add run/detail tables and active incidents. | Count requested, fetched, rejected, duplicate, inserted, updated, and failed rows separately; a replayed upsert is not new coverage. |

### Transaction contracts

The implementation should expose a small set of authenticated commands rather than accepting browser-supplied fill prices as authoritative:

- **Approve plan:** lock/check the plan, verify owner/account, expiry and mode, create one command, reserve its bounded cash requirement, and return a job ID.
- **Claim job:** atomically select and lease eligible work using `FOR UPDATE SKIP LOCKED` or an equivalent atomic claim/update.
- **Commit paper execution:** verify the lease and command key, validate fills against approved limits and snapshots, write fills/postings/position changes, release unused reservation, and complete the command in one transaction.
- **Close/reduce position:** validate remaining shares, simulate the executable exit through the engine, then commit the reduction and cash movement atomically.
- **Apply final settlement:** apply one verified outcome revision, settle remaining shares once, and append corrections for any later accepted revision.

A transaction guarantees consistent accounting. It does **not** mean a multi-leg exchange trade would have filled atomically. The simulation must still represent legging risk.

### Access and performance

Use an owner-authenticated command path for strategy settings, paper approvals, backtests, deployments, and maintenance. The current anonymous privileged RPCs can alter the paper record even though no real money is involved. Keep service credentials in backend/worker credentials only.

Review all 70 views before switching them to invoker permissions: first install the policies and grants required by their intended readers. Eleven no-policy tables may be correctly internal; do not make them public just to silence an advisor. Fix mutable function search paths and revoke unnecessary `PUBLIC` execution. Test anonymous, owner, and worker access separately.

Tune indexes for actual reads: active contracts by city/date, latest book by token/time, forecasts by city/target/model/availability, queued jobs by status/next-attempt, and open positions by account. Review the four duplicate groups on signals, paper trades, deployments, and backtest runs with FK/constraint dependencies before removal. An “unused” index is not proof it should be dropped.

### Migration order and rollback

1. Record current schema, migration history, row counts, and source checksums; prepare a restorable backup/export of affected records.
2. Add rule/band versions, provenance, QC fields, and compatibility reads. Populate proposed repairs into staging tables.
3. Review differences against saved contract text/provider identifiers; promote verified definitions only. Keep unresolved cases quarantined.
4. Reconstruct eligible outcomes and recompute affected skill/probabilities/facts under new versions. Preserve previous outputs as superseded.
5. Add job/account/ledger structures and transactional RPCs; align the backtest contract.
6. Deploy compatible readers and owner authentication, then narrow grants and migrate writers.
7. Activate one canary account/worker, reconcile, and expand. Switch reads/writers back to previous versions if a gate fails; append reversals for recorded economic events rather than deleting them.

Do not rerun the entire legacy SQL installation order against production as a substitute for a migration. Some live repairs and definitions differ from a pristine install; compare them explicitly.

## 7. n8n workflow build specification

Deliver one versioned import package with **12 refined existing workflows**, **three new operational workflows**, and **one optional ensemble collector**. Keep the existing P-numbers where possible so the Workflows page and saved configuration remain understandable.

### Shared requirements for every workflow

- Stable workflow/job IDs, explicit schema/engine compatibility version, and documented dependencies.
- Use the existing credential binder; references contain credential IDs/names, never exported API secrets. Import copies are separate from portable templates.
- Manual validation mode that fetches and validates without publishing source rows or sending notifications. Then a controlled write test, then scheduled activation.
- A single persisted schedule policy. The actual trigger must run frequently enough to honor it. The current P1.5 template triggers every six hours, while its database policy says three hours; reconcile the deployed configuration before assuming either describes reality.
- A run/lease record with execution ID, scope, source timestamps, expected coverage, actual coverage, counts, elapsed time, and terminal outcome.
- Stable pagination and checkpointing; retain request identity so one failed city response cannot shift later responses onto the wrong city.
- Bounded retries for temporary network/rate-limit errors, respecting provider retry guidance; validation/authentication errors become actionable failures. Shared handling records exhausted failures without resending completed economic commands.
- Validate units, dates, identity, completeness, future timestamps, and payload types before ingestion. An empty valid book is different from a failed request.
- Never publish a partial market ladder as a complete arbitrage universe.
- Authenticated operational webhooks. Store only non-sensitive status/configuration in frontend-readable settings.
- No notification is marked delivered until the delivery node succeeds. Import/testing defaults keep outbound notifications disabled.

### Existing workflows to refine

| Workflow | Proposed flow and important changes | Database/engine dependency | Cadence and import acceptance |
|---|---|---|---|
| **P0.2 Market discovery** | Claim run → load verified city/alias roster → fetch complete relevant events → match contract local date → parse rules and all bands → validate ladder/tokens/units → transactional upsert → coverage report. Prefer explicit contract date over the current “UTC hour before noon means previous day” heuristic. Retain stable IDs when reparsing history. | Contract/rule/band version structures, ingestion RPC, roster readiness. | Recovery: every 6h plus manual refresh. Gate: a current-date universe or specific verified absence; no zero-width bands or unit conflicts admitted. |
| **P0.3 Book and volume snapshots** | Load eligible tokens plus open-position tokens → batch fetch YES/NO books → normalize and validate → capture source/received times and venue limits → append snapshots → enqueue scoped paper evaluation. Refresh an approved order’s book on demand. | Token-side book schema, ingestion batch ID, decision queue. | Recovery: hourly archive capture. Fast mode: targeted shorter intervals/event triggers only after worker and budget gates. Gate: actual depth, limits and timestamps persist; replayed batches do not duplicate snapshots. |
| **P0.4 Trade history** | Use condition/token cursors with overlap → fetch pages → map band→market→city → deduplicate by stable provider trade identity → refresh incremental volume rollups. Preserve the new city-key fix. | Provider trade identity/cursor, existing city-key trigger, rollup contract. | Initial every 6h; shorten for active markets only if measured coverage requires it. Gate: repeat capture adds no duplicates and known sample volumes reconcile. |
| **P0.5 Contract rules** | Fetch unresolved/recent contracts → preserve raw text/hash → parse a new rule version → compare sources, units and deadlines → flag semantic changes. Store fallback sources and no-data outcomes. | Rule versions, contract eligibility, active incident records. | Daily and on discovery/rule change. Gate: London/NYC/Hong Kong examples route to their own rules, not one global settlement assumption. |
| **P1.1 Weather alerts** | Consume validated incident/event IDs → format source, city, age and meaning → deduplicate/cooldown → optional delivery → acknowledge delivery. | Event identity and notification state. | Event-driven. Gate: retry does not duplicate an alert or mark an unsent notification delivered. |
| **P1.2 NWS monitor** | Discover/verify source capability → fetch the station series → normalize units/timestamps → store observations → rebuild station-day runmax/timing → emit events. Record unsupported or failed stations distinctly. | Verified station metadata, source-specific observations, dated weather state. | Initial every 2h. Gate: US station data is current; unsupported cities are excluded explicitly; peak calculations use distinct valid observations. |
| **P1.3 NWS forecast** | Resolve forecast endpoint → retain product issue/capture timestamps and hourly horizon → aggregate complete local days → write versioned forecasts → enqueue probability refresh. | Forecast run schema and source capability. | Initial every 6h / detected new issue. Gate: completeness and model identity retained; lead measured from the actual issue/decision horizon. |
| **P1.4 NWS gridpoint** | Fetch grid values → expand interval durations correctly → convert units → aggregate features with coverage flags → append feature-run records. | Forecast features keyed by model/run/target, completeness checks. | Initial every 6h, aligned with NWS forecast. Gate: missing gridpoints stay unknown and never become fabricated zeros. |
| **P1.5 Open-Meteo** | Resolve verified coordinates → request unambiguous timestamps → preserve model current conditions separately from station observations → aggregate full local-day forecasts → write source-consistent state → report skipped city reasons. Correct the raw `cur.time` write under `timezone=auto`. | Forecast provenance, source-separated weather projection, roster. | Initial every 3h with matching trigger. Gate: zero unexplained future timestamps, complete eligible-city coverage, no stale NWS labels inherited by model readings. |
| **P2.1 Relearn** | Validate scope/readiness → create a research job → dispatch bounded compute → poll/report job result. A successful dispatch is not a trained model. | Durable research jobs, forecast evidence manifest, promotion gates. | Manual initially; weekly only once evidence is valid. Gate: UI exposes blocked/failed/completed and the model version produced. |
| **P3.1 Digests** | Read a consistent account/health snapshot → distinguish paper P&L, weather accuracy, data gaps and pending settlements → render → optional deliver → log. | Ledger-backed reporting, operational status, notification settings. | Morning/EOD in an explicitly configured reporting timezone. Gate: totals equal the UI and no system-alert count is labeled trade activity. |
| **P4.1 Watchdog** | Compare expected active universe with per-city feed watermarks → inspect queue age, worker leases, pricing coverage, settlements and account reconciliation → update incidents → notify on change. | Run metrics, coverage projection, worker/account health. | Recovery every 6h; faster checks belong to the worker/operational mode when budget permits. Gate: one fresh city cannot hide an outage elsewhere, and green/zero-work is explained. |

### New workflow specifications

| Workflow | Exact role | Acceptance |
|---|---|---|
| **P2.2 Paper-cycle orchestrator** | On new book/forecast, approved plan, or periodic sweep: submit a scoped job → worker claims it → validate data/readiness → compute decisions or fills → transactional commit → record coverage and result. Coalesce repeated market events. Carry plan/job IDs through every node. | One approved command yields one execution result. Worker failure leaves a recoverable lease. No live exchange order endpoint is called. |
| **P2.3 Resolution collector** | Load due unresolved contracts → fetch the rule-specified primary/fallback evidence and provider resolution status → archive evidence → request settlement verification → publish final outcome → enqueue remaining paper-position settlement. | Closed-but-unresolved, disputed, missing-source, fallback-deadline, and final-settled states stay distinct. A retry never pays twice. |
| **P4.2 Shared error handler** | n8n Error Trigger → attach originating workflow/execution/job/source → redact sensitive fields → persist failure/incident → retry only through a bounded durable policy → optional notification. | Fetch failure, malformed payload, 429, DB rejection, and worker timeout all produce a recoverable, readable failure record. Manual validation failures are also visible in the parent workflow. |
| **P1.6 Ensemble capture — optional** | Fetch selected provider model members → preserve run/member/target identity → record availability and completeness → append member-level forecasts → expose experimental distributions. | At least one eligible source supplies identifiable members; partial ensembles are marked; no fitted ensemble is claimed before validation. |

### Packaging and your import sequence

The implementation package should include the portable JSON templates, credential-binding command, compatibility manifest, per-workflow inputs/outputs, example sanitized responses, validation results, and one short import checklist.

1. Apply the tested database prerequisites and deploy the matching worker/command layer.
2. Import P4.2, bind credentials, and verify that failure reporting works without sending notifications.
3. Import P0.2 and P0.5; manually verify current contracts and definitions.
4. Import P0.3 and P0.4; manually verify timestamped depth and trade capture.
5. Import P1.2–P1.5; verify source-specific weather and complete forecast days.
6. Import P2.2 and P2.3; run a small user-funded paper account through entry, exit, and settlement/reconciliation gates.
7. Import P4.1, P2.1 and the reporting workflows; activate schedules only after each manual test passes. Enable optional ensemble capture later.

You handle importing/configuring/activating the n8n package. I build/refine the files, tests, database contracts, and the mapping that tells you exactly which credentials/settings each import needs. Direct access to the deployed n8n version and execution logs remains necessary during rollout to confirm behavior beyond the repository templates.

### Budget and cadence guard

Do not promise minute-level monitoring on an unverified execution allowance. At the initial cadences above, independently scheduling books hourly, discovery/trades/NWS forecast/NWS grid every 6h, NWS observations every 2h, Open-Meteo every 3h, rules daily, digests twice daily and watchdog every 6h gives approximately **2,110 scheduled workflow starts per 30 days**, before new orchestrators, event alerts, or retries. Actual billable executions depend on the hosting plan and subworkflow accounting.

Measure the current allowance before activation, consolidate overlapping work where practical, and make the budget visible. Preserve city coverage; reducing city count is not the proposed cost solution and does not by itself reduce timer-triggered workflow starts. Fast mode should use a worker that handles event processing efficiently instead of dispatching a new GitHub runner for each book. Keep history/learning jobs batched and avoid duplicate ownership between n8n, Actions, and the worker.

### GitHub Actions audit and efficiency plan

All 11 workflow files are included below. Existing pip/npm caching, narrowed CI triggers, and CI cancellation already provide useful savings; preserve them. The next improvements target repeated processing, unbounded backfill chains, and incorrect success reporting. Keep every workflow capability and manual recovery entrypoint.

| Existing workflow | Current role/cadence | Refinement preserving its purpose |
|---|---|---|
| `pipeline_intraday.yml` | Six runs/day, every four hours; its four-times-daily comment is stale. | Scope pricing and signals to affected city-days using source/config versions. Batch reads and deduplicate repeated alerts. Record dependency outcomes, fallback paths, coverage and useful output. Keep timed reevaluation for pending orders, local peak windows, expiry, and existing positions even when feeds have not changed. |
| `pipeline_daily.yml` | Daily reconciliation, settlement, skill and derived processing. | Use watermarks for changed observations/outcomes and recompute affected partitions. Always service due settlements and open positions. Refit expensive models only with sufficient new eligible evidence; keep the same research capability and report why a fit is deferred. |
| `observations.yml` | Every six hours; repeatedly fetches a two-day window. | Track station/source watermarks, fetch a small revision overlap, upsert corrections, and retry only failed stations. Maintain a bounded catch-up path for gaps and late corrections. Compare results with the current full-window capture before adopting it. |
| `forecasts.yml` | Manual historical ingest with automatic continuation. | Persist missing-range cursors and a whole-chain time/request budget. Stop repeated continuation without progress. Resume only remaining gaps. Propagate Python failure through the `tee` pipeline and distinguish incomplete, failed, and complete outcomes. Fold continuation/report-only jobs into the ingest job where practical. |
| `weather_model.yml` | Weekly training, plus manual operation. | Build a versioned input manifest before expensive fitting; reuse identical results, require sufficient new valid evidence, and retain manual forced research runs within a declared budget. Preserve evaluation and promotion checks. |
| `archive_observations.yml` | Monthly archival. | Archive only newly eligible partitions with a resumable manifest and verified counts/checksums. Preserve current retention and historical research access; never prune data merely to reduce the bill. |
| `backtest.yml` | Manual and repository dispatch. | Declare and correctly route the requested `run_id`; the current manual trigger declares no input although the job reads one. Atomically claim queue work, deduplicate dispatch/sweeps, preflight data coverage, checkpoint long work, and bound retries. |
| `live_weather.yml` | Manual fallback/recovery. | Retain it, scope recovery to affected sources/cities where supported, and coordinate ownership with normal collectors to avoid concurrent duplicate writes. |
| `verify_resolution_source.yml` | Manual resolution-source verification. | Retain verification; reuse unchanged rule/source evidence with expiry and provide an explicit force-recheck option. Target failed or changed contracts instead of repeating unrelated checks. |
| `tests.yml` | Main pushes, PRs, manual; existing path/concurrency controls. | Preserve the full correctness suite and required status checks. Split pinned runtime dependencies from test dependencies for data jobs. Make path filters cover shared contracts/configuration as well as directly executed code. Cancel obsolete CI only, never a state-writing execution without recovery. |
| `web.yml` | Main/PR frontend changes and manual clean build. | Preserve the zero-Supabase-environment build, type safety, and route-prerender checks. Cache safe build artifacts; investigate redundant typecheck work only after proving equivalent coverage. Python setup appears unused by the current npm scripts/build config and can be removed after checking the complete build path. Never print environment-variable values when reporting a forbidden variable. |

**Verified schedule mismatch:** current YAML implies 180 intraday + 120 observations + 30 daily + 4–5 weekly training + 1 monthly archive = approximately **335–336 scheduled workflow starts per 30-day month**. The budget documentation claims 215, and its check allows 260. Generate the budget inventory from workflow YAML and update both documentation and checks. Do not reduce functional cadence to make the old number pass. Manual backfills, dispatches, retries, CI, and multiple jobs add further usage.

**Backfill exposure:** with initial depth 0 and continuation while depth is below 8, the forecast workflow can execute up to nine ingestion runs. At the script's approximately 330-minute soft deadline each, that is roughly **2,970 runner minutes for one chain**, plus overhead, if every run consumes its allowance. This is an upper-bound scenario, not measured spending. Its separate continuation/stopped jobs also incur runner overhead. Budget the complete logical backfill, preserve progress after each chunk, and require a new explicit resume request once its budget is exhausted.

Representative job timings retrieved from GitHub establish where to measure improvements:

| Job / run ID | Measured job duration | Indicative rounded job minutes | Observed expensive work |
|---|---|---|---|
| Daily / `34204739766` | 186 seconds | 4 | Settlement 66s, derived processing 36s, skill 30s, databank 23s. |
| Intraday / `34187220553` | 42 seconds | 1 | Signals 27s; pricing/model work currently minimal. |
| Observations / `34186819805` | 88 seconds | 2 | Observation ingest 81s. |
| Tests / `34144539851` | 16 seconds | 1 | Tests 9s; already below one minute. |
| Web / `34143652936` | 70 seconds | 2 | Build 40s, typecheck 10s, npm install 10s. |

GitHub rounds each job's usage up to a whole minute. These are illustrative timing samples, not invoice measurements; allowances, runner types and actual billing determine money charged. Saving seconds in an already sub-minute job may not reduce billed minutes. Crossing a minute boundary, avoiding a duplicate job, or preventing a long no-progress backfill is more consequential. Current jobs also include failed/no-op business processing, so their speed is not a valid performance target for a repaired, productive engine.

**Implementation order and measurement:**

1. Record runner/job/step durations, trigger, attempts, affected city-days, new/revised rows, decisions, failed coverage, and logical backfill ID. Establish actual account billing separately from these estimates.
2. Fix backfill exit status and chain limits first; then implement incremental observations and affected-partition computation with atomic checkpoints. Advance a watermark only after its batch commits successfully.
3. Use a database claim/lease shared by Actions, n8n, and worker commands. Coalesce redundant work without dropping a newly arrived source revision; a pending generation must run after an in-progress older generation.
4. Include source payload revisions, forecast/rule/config/model changes, order/position changes, and time deadlines in invalidation. A latest timestamp alone cannot detect corrected data. Keep periodic full reconciliation as a recovery mechanism.
5. Compare old/new processing against the same captured inputs and due timers in isolated replay. Verify city/source completeness, corrected observations, plans, fills, settlements, and CI coverage before replacing the existing processing path.
6. Track cost per useful city-day/update/backtest and total combined GitHub, n8n, worker, and database cost. Set a numerical savings target after the baseline; do not promise a percentage or move the bill elsewhere invisibly.

No proposed savings require deleting tests, reducing cities, dropping forecast providers, removing historical research, or making a private repository public. GitHub cron is best-effort and can be delayed; fast entry/exit decisions belong in the measured worker/event path, while Actions retains batch and recovery responsibilities.

## 8. Paper Trades section and execution rules

### Product purpose and three operating modes

**Paper Trades is a core addition:** it joins the platform's existing forecasts, live weather, predictive analysis, market books, strategies, and account controls into an actionable, auditable workflow. It simulates orders against real captured market data and records the complete lifecycle. It cannot promise the perfect entry time; it can apply explicit timing rules consistently and measure whether they improve results after costs.

| Mode | User experience | Execution contract |
|---|---|---|
| Manual | Select a market/band, YES or NO, buy or reduce, quantity/budget, and price limit. See current context, costs, liquidity and account impact before submitting. A personal thesis is optional. | Manual trades do not need a strategy signal. They still use verified contract metadata, real fresh books, account limits and the same fill engine. Advisory model disagreement is displayed, not silently converted into a prohibition. |
| Assisted | A strategy proposes a plan with reasons, costs, timing, expiry, and risk. Approve, reject, or edit into a new versioned manual plan. | Approval queues execution; it does not itself create a fill. Revalidate price, evidence, account limits and expiry on submission. Reapproval is required when the revised plan exceeds the approved bounds. |
| Automatic | Select allowed strategies/cities, allocation and exposure limits, timing/freshness rules, entry/exit conditions, and enable automatic paper execution for that account. | Only eligible plans within the saved policy execute. Record the policy version, decision reason and every resulting action. Preserve the existing default until the user configures automatic mode. |

Manual and automatic activity may share one paper account with a common cash reservation system. Clearly tag origin, strategy, actor, and campaign so comparisons are meaningful. Optional isolated research accounts may be added later. Existing campaign allocation and the new automation must submit to the same executor, preventing two systems from allocating the same money independently.

### Page structure and links to the existing platform

Add `web/app/paper-trades/page.tsx` and a **Paper Trades** navigation item using the existing visual language, city scope, formatting, tooltips and data-state components. Keep all existing pages. Build a useful initial page in Batch 3, then refine analytics in Batch 6.

| Surface | Content and actions |
|---|---|
| Account header | Paper-only label, selected account, mode, feed/worker freshness, free/reserved cash, equity, realized/unrealized P&L, fees, pending payouts, and visible pause state. |
| Opportunities and approvals | Eligible plans, waiting approvals, expiry countdown, expected net edge, book age, cost, sizing and blocking reasons. Include a manual trade button. |
| Open positions | Group by city/local resolution day and strategy; show each leg, remaining shares, entry cost, executable exit value, unrealized P&L and reasons to hold/reduce/exit. Support partial manual exits. |
| Orders and fills | Queued, working, partial, filled, rejected, canceled and expired orders; requested versus filled quantities, limits, actual simulated prices, slippage, fees and timestamps. |
| Closed history and activity | Realized results and settlement status, filters, export, and an immutable timeline of decisions, approvals, orders, fills, reductions and corrections. Rejected/expired evaluations remain inspectable. |
| Automation settings | Strategy/city scope, per-order and total exposure, price/freshness bounds, cooldown, timing windows, exit rules, and separate controls for pausing new entries and managing existing positions. Changes are versioned. |

Add contextual **Paper trade** links from Board, Opportunities, Predictive, Strategies, Synthesis and Campaigns as appropriate. Pass market/strategy/analysis IDs into the ticket instead of copying calculation rules into each page. Keep the existing alerts view, and link genuine trade approvals to this section. The current 998 system alerts are not historical paper trades; show a truthful empty trading history until actual simulations occur.

### Decision context: why this action, now?

Each evaluation uses the city and contract's local target day, with both source time and time received recorded. Store the complete context or immutable references sufficient to reconstruct it:

- **Forecast:** newest eligible run per identified provider/model, predicted daily maximum/distribution, forecast revision, horizon, calibration version and uncertainty. Missing proprietary/ensemble predictions are labeled unavailable.
- **Weather movement:** verified station readings, running maximum, recent trajectory, distance from forecast maximum, time to expected peak and remaining local-day opportunity. Keep observed weather distinct from model current conditions.
- **Market movement:** timestamped YES/NO bid/ask depth, spread, liquidity, price changes over declared windows and observed market trades. Do not infer available execution depth from trade volume alone.
- **Strategy and prediction:** matching strategy/version, its required evidence, calibrated probability, market-implied price, costs, net edge, alternative actions, and uncertainty. Explain fallback evidence explicitly.
- **Account and timing:** existing correlated exposure, available/reserved cash, order limits, cooldown, pending orders, market status, time window and freshness. A strong forecast alone does not justify a fill.

Fresh books, forecast revisions, material observation changes, approved/manual commands, policy changes and due timers trigger scoped evaluation. Coalesce bursts while retaining the newest committed input generation. Reevaluate open positions for configured exits as well as new entries. Log why no action was taken, with bounded aggregation of identical repeated evaluations and exact references to their input window; never discard actual commands or economic events.

### Orders, timing, and controls

The lifecycle is evaluation → immutable plan → manual submission/approval/policy authorization → queued order → fresh-book validation → partial/full fill or explicit rejection/expiry → position management → verified settlement. Keep evaluations, orders and positions as distinct state machines; an unfilled order is not a trade.

Initial execution should support a clearly defined marketable limit ticket and immediate fill-or-partial/cancel behavior against captured depth. A persistent resting-limit simulation is a later explicit capability requiring latency, queue-position and traded-volume assumptions; merely seeing the market touch a price is not proof the order filled. Display these simulation assumptions in trade details.

Set a measured source-to-decision and command-to-fill service target before enabling each timing-sensitive strategy. Refresh a book on demand when available and within budget. If the target cannot be met, show that reason and do not backdate an order to a more favorable quote. A paused new-entry mode must not silently stop reconciliation, monitoring, settlement, or separately configured exit management. Budget controls should defer expensive optional research before starving account maintenance; operational resource limits still produce explicit degraded status.

Every event records account, market, target local day, origin, actor, strategy/config/engine versions, trigger, plan/order/fill IDs, source references, submitted/processed times, quantity/price/cost, status and reason. Notifications are optional projections of this log. Compare manual, assisted and automatic results separately, including unfilled and rejected opportunities, so selection effects are visible.

### TradePlan contents

Each plan records: account, strategy and engine version; city/market/target local date; rule/band versions; all legs with token/side/action; requested shares and cash ceiling; source book IDs; forecast and skill IDs; raw/calibrated probability; fee/limit metadata; expected gross/net outcome; worst-case loss across market outcomes; approval expiry; quality gates; and the reason for entry or exit.

Approval uses the book available **when the simulated order is submitted**, after any modeled latency. Do not award a user a price from the older signal merely because they approved later. Preserve signal, approval, snapshot, simulated fill, and processing times separately. No suitable fresh book means queued/reprice/rejected, never an invented fill.

### Accounting and risk

- Require an explicit paper starting balance. Do not silently turn null into an invented bankroll.
- Enforce physical constraints: available cash, remaining shares, fee-inclusive cost, venue quantity/price increments, and valid executable depth.
- Apply the user’s configured band, city-day, total exposure and loss limits. Distinguish hard limits from explicitly chosen advisory warnings; Kelly is an estimate, not an automatic authority over the account.
- Reserve funds across all legs before execution; actual partial fills consume only actual cash. Release unused funds on failure/cancel/expiry.
- Model repeated orders against the same snapshot conservatively so the simulator cannot repeatedly consume the same displayed liquidity without a replenishment assumption.
- Calculate realized P&L from closed quantities. Show unrealized P&L at an executable exit valuation, with an unavailable/stale state when no fresh exit book exists.
- Implement compounding through the cash ledger and settlement timing. Winning an unsettled contract does not make its payout available to spend early.
- Reconcile cash, positions, fees, and equity after every command and on a scheduled sweep.

### Multi-leg strategies

S2/S6 must expand `payload.band_ids`; S8/S9 must group their separately emitted legs under one plan. Persist each leg’s side, snapshot, requested/filled quantity, price, fees and remainder. Preflight the whole basket and record what happens if only part fills. Any unwind must itself use a recorded executable book and incur its costs. “All-or-nothing” is permitted only as a declared experimental simulation mode with separate reporting, not an assumption about venue execution.

### Settlement

Resolve each market using its saved contract rules. The inspected data includes NWS/NOAA, Weather Underground and HKO sources; sampled London/NYC contracts include fallback/deadline rules, and Hong Kong has its own daily-extract and deadline logic. A daily station maximum alone is not universally the final payout authority.

Preserve observation evidence and official/provider final outcomes separately. A provider `closed=true` flag is insufficient to declare a winner. Keep unresolved/disputed positions visible, apply specified no-data fallback rules only when their conditions are verified, and settle remaining shares once. Do not simply flip the existing global `settlement_verified` setting to bypass parser failures.

## 9. Forecasting, calibration and backtest plan

### Forecast identity and timing

Retain the new model-specific skill table and newest-run selection. Finish the work by applying the same deterministic selection in regime classification, strategy contexts, UI and replay. Select the correct local target date, model/product, availability cutoff and horizon; never use a forecast captured after a decision unless explicitly running a separate reconstructed-history experiment.

The historical ingest synthesizes `run_at` from target date minus lead. Keep this dataset for appropriate forecast-error research, but label the provenance. A previous-runs hourly lead archive does not establish a single archived forecast publication at that synthesized midnight. Separate historical archive skill from verifiable as-issued forward performance.

Validate full-day coverage and station alignment. Score one eligible decision-time sample per model/city/day/horizon, with revisions treated consistently. The existing per-model fallback needs to disclose when it uses pooled skill; its current table only has the historical `open_meteo_best_match` product.

### Probability and calibration

Use the existing Normal model as a baseline, not a proven final distribution. Compare it with empirical residual distributions, heavier-tail alternatives and eventually ensemble/quantile forecasts. Promote complexity only when it improves held-out probability quality and decision outcomes after costs.

Save raw and calibrated probabilities separately. The current databank prefers calibrated probability in `model_prob`, while calibration subsequently fits that field: future iterations could calibrate already-calibrated predictions. Train mappings on explicitly raw predictions and evaluate the exact final normalization used at inference.

Split by time and city-day so neighboring buckets from one outcome do not appear as independent days. Report distinct days, dates, cities, lead coverage and uncertainty alongside row counts. Evaluate Brier score, log loss, reliability, interval coverage and market baselines on the same samples. The current ten banked probabilities are insufficient; no fitting should be forced to fill the dashboard.

The derived weather model already uses held-out selection, but some feature aggregates describe the completed day. Training on realized clouds/wind/rain and serving with forecast versions of those features is a different test from genuine decision-time prediction. Build separate forward-forecast and intraday observation experiments with availability-controlled features. Keep persistence and provider forecast baselines.

### Backtest modes

| Mode | What it may report | Required evidence |
|---|---|---|
| Weather-model evaluation | Forecast error, distribution quality, source/horizon comparisons | Valid local-day outcomes and correctly identified forecasts |
| Historical paper replay | Orders, fills, fees, cash/equity, exits and settlement | Decision-available forecasts/rules, dated books, parameter versions and outcome evidence |
| Forward paper record | Actual signals issued and simulated orders handled by the running platform | Immutable decision/approval/execution timestamps, source snapshots and ledger |

For historical replay, fix the raw `book_snapshots` normalization first: its `bid_levels`/`ask_levels` are counts, while `levels_for_side` expects arrays. Then process events in chronological order, carry positions/cash forward, support partial exits and final payouts, and claim jobs atomically. The current one-instant-per-city-day, hold-to-settlement harness should retain that limitation label until event replay is implemented.

Require an availability report before queueing a long run: eligible city-days, book coverage, forecast provenance, model versions, outcome status and expected skipped periods. Include a reason for every skipped segment. Missing depth must not be replaced with unlimited top-of-book liquidity. Completed runs store input manifests and engine/config versions; reruns produce new records.

Maintain separate comparisons for all valid signals, approved trades, and actual simulated fills. This prevents user selection, missing fills, and alert counts from distorting the claimed strategy edge.

## 10. UI and stronger product ideas

### Preserve and improve the existing interface

The source audit includes navigation, shared data hooks, status components, signals, timing, and execution calculations. It is not a claim that every deployed screen has passed browser interaction testing. Use the current interface as the baseline, keep its styling and controls, and verify existing behavior alongside each change.

| Existing navigation destination | Improvement within its current purpose |
|---|---|
| Overview `/` | Reconciled summary totals, current data readiness and links to paper activity. |
| Board `/board` | Correct contract units/bounds, complete books, fee-aware trade preview and Paper trade link. |
| Opportunities `/opportunities` | Authoritative plans, expiry/readiness reasons, and approval links. |
| Predictive `/predictive` | Point-in-time forecast identity, uncertainty, model availability and linked decisions. |
| Strategies `/strategies` | Preserve every strategy/control; explain required inputs, current eligibility and automatic paper policy. |
| City Clusters `/clusters` | Preserve grouping and comparisons; expose evidence coverage and correlated exposure. |
| Globe `/globe` | Preserve map interaction and city navigation; distinguish stale/missing data. |
| Live Weather `/live` | Correct source/time/unit display and observation-versus-model provenance. |
| City Monitor `/monitor` | Dated station trajectory, local peak context, source health and open-position links. |
| Analytics `/analytics` | Ledger-backed metrics with manual/assisted/automatic and strategy filters. |
| Data Bank `/databank` | Prediction/outcome provenance, valid coverage and visible quarantine status. |
| Synthesis `/synthesis` | Explain authoritative decisions and reuse them when opening a paper ticket. |
| Backtest `/backtest` | Preserve controls; show preflight coverage, queue state, reproducible results and limitations. |
| Campaigns `/campaigns` | Preserve planning/allocation controls; route execution through shared account reservations. |
| Goals `/goals` | Preserve goal and budget settings; reconcile projections with actual paper-account resources. |
| Workflows `/workflows` | Preserve controls; show useful output, per-source freshness, failures, queue lag and measured cost. |
| How it works `/docs` | Retain existing guidance; explain paper simulation modes, costs, timing assumptions and new workflows. |

**Shared component changes:** `useQuery` currently creates polling per consumer, clears its data on errors, and detects truncation with a 1,000-row/request-limit heuristic. Introduce shared, account/city/date-scoped caching and request deduplication, explicit server pagination, mutation invalidation, and visibility-aware polling. Retain last successful data during a background failure only with its age and error clearly visible; it must not remain executable as if fresh. Refresh immediately on scope changes and discard responses for obsolete scopes.

`SignalsPanel` fetches the latest 50 signals and subscribes to inserts; approval/status updates need scoped update invalidation as well. Its fallback band metadata currently invents an empty city/date and Celsius unit. Join the actual market metadata instead, or display unknown and block a dependent ticket until resolved. Give alerts their own acknowledgment controls, preserve the alert surface, and route genuine trading actions to canonical plans. Format decimals by meaning, retain exact values in detail/tooltips, and show city-relevant temperature units consistently.

Acceptance includes keyboard-accessible tickets/dialogs, responsive navigation, loading/empty/error/stale/partial states, refresh after commands, and screenshots of changed screens against the current design. Adding Paper Trades must pass the existing environment-free build and route checks with all existing routes retained.

### Further improvements after the core lifecycle

| Improvement | User-facing result | Prerequisite |
|---|---|---|
| Decision card | “Why this trade, at this size, now?” with net edge, source age, costs, worst-case loss, and alternative/no-trade reason. | Canonical TradePlan |
| Account reconciliation | Starting cash, reserved cash, invested cost, free cash, realized/unrealized P&L, fees and pending payouts that reconcile. | Transactional ledger |
| City readiness matrix | One row per city showing contract, station metadata, forecast, book, timing, settlement and last useful run. | Coverage metrics |
| Replay inspector | Select a trade and inspect the exact forecast, ladder, book, approval and fill used at each step. | Immutable source IDs and replay |
| Scenario portfolio | Show total profit/loss under every mutually exclusive winning bucket, including partial baskets. | Canonical contract lattice and positions |
| Strategy scorecards | Separate forecast accuracy, execution quality, net returns, drawdown, capital usage and evidence size. | Valid outcomes and accounting |
| Forecast disagreement panel | Show which independently identified providers disagree, at which horizon, and how that affects uncertainty. | Source-correct forecast runs |
| Challenger experiments | Run proposed models/strategies in separate paper accounts against identical opportunities and costs. | Reproducible engine, account isolation |
| Closing-price comparison | Compare entry with later comparable executable quotes as a diagnostic, alongside final P&L. | Recorded comparable books and fixed measurement times |
| Capacity-aware allocation | Choose capital across city-day outcome scenarios with correlation and liquidity constraints. | Reliable fills, empirical uncertainty, validated dependence estimates |

Keep new strategy ideas as hypotheses: improved pre-peak trajectory conditioning, forecast-revision responses, and complete-ladder arbitrage after full costs. Test them against simple provider-based and no-trade baselines. Do not call a strategy superior solely because it wins often or has attractive in-sample returns.

## 11. Acceptance suite and release gates

Add meaningful integration tests around the points where the current 575 passing tests coexist with live failures. Use captured, sanitized real payloads plus isolated synthetic edge-case fixtures; production records remain real.

| Gate | Required result |
|---|---|
| Contract/lattice | Celsius/Fahrenheit, singleton/range/tail/negative/rounding cases agree across all consumers. A complete exclusive ladder has exactly one matching outcome for a valid settlement value. |
| Historical repair | Original and replacement definitions/outcomes are traceable. No quarantined fact enters calibration. Missing evidence stays unresolved. |
| Weather time | UTC conversion, local midnight and DST cases pass; future timestamps and mixed-source projections are rejected or explicitly quarantined. |
| Coverage | Each selected eligible market has expected fresh inputs or a named reason. Zero-current-market and zero-pricing runs cannot appear fully healthy. |
| API pagination | Results remain complete under smaller server caps and stable across retries/pages. |
| Database contract | A fresh install and an upgraded populated schema both accept actual worker payloads, including backtest results and UUID version references. |
| Approval/idempotency | Repeated clicks, network retries, and overlapping workers result in one economic command and no duplicate fills. |
| Cash | No overspend under concurrent orders; reservations release; realized P&L and fees reconcile exactly at documented numeric precision. |
| Basket/exit | Partial legs, failed legs, repeated use of a snapshot, partial exits and final settlement preserve actual holdings. |
| Settlement | Source variants, fallback deadlines, disputes, absent data, duplicate finalization and accepted corrections all produce correct states/postings. |
| Point in time | Any input captured/available after decision time is excluded from forward-equivalent replay. Completed-day weather cannot enter an earlier prediction. |
| n8n import | JSON graph/types/expressions/credentials validate; fixture tests pass; manual dry-run and controlled writes pass on the actual n8n version. |
| Access | Anonymous users cannot mutate paper records/settings; the owner can operate the desk; workers can perform only intended ingest/execution operations. |
| UI | Existing pages build and load; totals reconcile; empty, stale, failed, queued, estimated and final values display distinctly. |
| Feature preservation | All 17 existing navigation destinations, strategy controls, campaign/goal flows and manual workflow entrypoints remain usable. New Paper Trades is additive; database upgrades retain existing reads and history. |
| Three paper modes | Manual tickets can operate independently of a strategy recommendation; assisted plans require approval; automatic plans respect the saved policy. All share reservations, fill rules, position updates and the ledger. |
| Timing and audit | Late approvals revalidate current books; corrected forecasts trigger evaluation; due timers run even without a feed change. Every actual command and economic event is traceable, including partial/rejected/expired outcomes. |
| Pause and recovery | Pausing entries retains configured exit management, reconciliation and settlement. Worker interruption, lease expiry and retries recover without duplicate orders or lost reservations. |
| Actions efficiency | Same-input replay preserves coverage and due actions. Incremental ingestion catches revisions/gaps. Backfill continuation is bounded and detects no progress; failed Python commands cannot appear successful. |
| Cost measurement | Budget counts derive from current YAML; job-minute estimates are distinguished from actual charges. Savings are measured on productive workloads across all services, with no removed CI gates or data sources. |

Release progression: feed-only observation → one canary paper account → several representative cities/sources → all eligible cities → faster timing-sensitive strategies. Include a Celsius city, Fahrenheit city and a separate source family where supported; do not force an unresolved HKO adapter through the gate to claim complete coverage.

A first 48–72 hours of healthy operation can verify scheduling and lifecycle behavior. It does not establish a profitable strategy. Model promotion needs a predeclared evaluation window and enough independent settled outcomes; even 30 days is diagnostic rather than proof of durable profitability.

## 12. Concrete handoff

**I build in `arbdesk4`:** incremental repository fixes; compatible database migrations and repair tooling; worker package; shared execution/replay contracts; refined/importable n8n workflows; optimized Actions workflows and cost reporting; the new Paper Trades section; improvements to existing UI components; tests; and the ordered deployment/import guide.

**You implement in n8n:** bind the named credentials, import the supplied files, run the documented checks, and activate the workflows in order. The current request is planning; these deployment actions have not been performed.

**Still to establish during implementation:** actual n8n version/hosting/execution allowance; worker hosting capability; actual GitHub billing allowance and charged usage; and the intended paper starting balance and automation policy. These do not prevent completing this plan. Preserve existing settings until explicitly configured. No profitable performance should be inferred from the current alert-only record.

The first implementation batch should deliver a current, valid market universe, real order-book capture, trustworthy weather timestamps, a staged historical repair report, and a health screen that explains why each city can or cannot be evaluated. That gives every later improvement a usable foundation.

## Evidence and reference index

Repository links below pin the reviewed version rather than a moving branch:

- [Reviewed commit](https://github.com/hassansab00/arbdesk4/commit/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0)
- [Signals orchestration](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/scripts/signals.py)
- [Paper engine](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/scripts/paper_engine.py)
- [Probability engine](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/scripts/probability_engine.py)
- [Databank](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/scripts/databank.py)
- [Settlement](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/scripts/settlement.py)
- [Backtest runner](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/scripts/backtest/runner.py)
- [n8n templates and credential binder](https://github.com/hassansab00/arbdesk4/tree/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/n8n)
- [UI execution calculator](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/web/lib/execution.ts)
- [Tests run: 575 passed, 4 skipped](https://github.com/hassansab00/arbdesk4/actions/runs/34144539851)
- [Latest relevant web build](https://github.com/hassansab00/arbdesk4/actions/runs/34143652936)
- [Daily pipeline with settlement failures](https://github.com/hassansab00/arbdesk4/actions/runs/34204739766)
- [All reviewed Actions workflows](https://github.com/hassansab00/arbdesk4/tree/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/.github/workflows)
- [Forecast backfill continuation](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/.github/workflows/forecasts.yml)
- [Current compute budget](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/docs/compute_budget.md)
- [Intraday timing sample](https://github.com/hassansab00/arbdesk4/actions/runs/34187220553)
- [Observation timing sample](https://github.com/hassansab00/arbdesk4/actions/runs/34186819805)
- [Existing navigation](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/web/components/NavTabs.tsx)
- [Shared query hook](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/web/lib/useQuery.ts)
- [Signals component](https://github.com/hassansab00/arbdesk4/blob/eafc0bc3e21bd2f84a2b9038d033105bc54e83f0/web/components/SignalsPanel.tsx)

Live evidence comes from read-only SQL over markets/bands, forecasts/observations/live weather, signals/trades/ledger, outcomes, backtests, configuration, ingestion logs, catalog constraints/policies/functions, and migration history. Counts in section 2 are database findings, not claims from public documentation.

Primary technical references consulted for the design:

- [GitHub Actions runner pricing and per-job minute rounding](https://docs.github.com/en/billing/reference/actions-runner-pricing)
- [GitHub Actions billing and included usage](https://docs.github.com/en/actions/concepts/billing-and-usage)
- [GitHub scheduled workflow timing limitations](https://docs.github.com/actions/using-workflows/events-that-trigger-workflows)
- [GitHub dependency caching](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching)
- [Open-Meteo forecast API: timezone and timestamp formats](https://open-meteo.com/en/docs)
- [Open-Meteo previous-runs API](https://open-meteo.com/en/docs/previous-runs-api)
- [n8n HTTP Request: pagination, batching, response handling](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-base.httprequest)
- [n8n error handling](https://docs.n8n.io/build/flow-logic/handle-errors-gracefully)
- [Supabase RLS and invoker views](https://supabase.com/docs/guides/database/postgres/row-level-security)
- [Supabase Edge Function limits](https://supabase.com/docs/guides/functions/limits)
- [Polymarket fees](https://docs.polymarket.com/trading/fees)
- [Polymarket token tick-size metadata](https://docs.polymarket.com/api-reference/market-data/get-tick-size)
- [Supabase advisor: privileged views](https://supabase.com/docs/guides/database/database-linter?lint=0010_security_definer_view)
- [Supabase advisor: anonymous privileged functions](https://supabase.com/docs/guides/database/database-linter?lint=0028_anon_security_definer_function_executable)
- [Supabase advisor: duplicate indexes](https://supabase.com/docs/guides/database/database-linter?lint=0009_duplicate_index)
