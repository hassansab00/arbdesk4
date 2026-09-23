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
did. Ask again before merging anything in the "Hassan merges" categories.

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
| **P2.1** | Read every METAR | blocked | | | **Blocked on network access.** Step 1 (fetch IEM with `report_type=3&4` and `=1` for EGLC/EHAM, 12 and 17 Sep) needs a live fetch, and this environment's network policy denies `mesonet.agron.iastate.edu`, `api.synopticdata.com`, `www.weather.gov`, `api.weather.gov`, `aviationweather.gov`, Open-Meteo and Polymarket (curl → proxy 403). Hassan can allow these hosts in the environment's network settings. **What the DB already shows (23 Sep):** WRH evidence for London 17 Sep has 48 readings (:20 and :50), max 22 °C at 14:20Z; our IEM rows for that local day are 24 (:50 only), max 21 °C. Amsterdam 12 Sep: IEM 24 readings, max 21 °C, WRH 22 °C. NYC 20 Sep: the winner 72–73 °F is named only by a 01:04Z report (71.96 °F), not by the :51 routine reports (max 71 °F), which points at specials (`report_type=4`). |
| **P2.2** | Fix `v_station_day_max` / the agreement view | doing | plan/p2-station-agreement | pending: merge, apply `sql/ad4_82`, re-run A4 | Finding confirmed (Dallas 13 Sep: NWS KDAL 100.4 °F at :55 leaked past the ±4-minute window; the routine report was 99.0 °F). The hourly columns now read the primary source (`obs_primary_source()`, `'IEM'` until P2.1) with no minute window; `venue_round(value_c, unit)` rounds to whole degrees in the canonical unit. Live, 23 Sep, 638 settled ladders with a reading: 579 agree today → 587 with this change; the 51 left are 46 below and 5 above (P2.1's missing reports). New PGlite harness `settlement-agreement.cjs` with the Dallas, NYC and London fixtures, mutation-checked. |
| **P2.3** | Rebuild `observation_trust` from venue evidence | blocked | | | Refits on the corrected source, so it waits for P2.1. |
| **P2.4** | Keep market state current | todo | | | **Hassan decided (23 Sep):** a market whose local day has ended is closed; if its temperature was not settled by the venue, that is a real issue and must be flagged. Live on 23 Sep: 379 markets past `local today − 1` still `closed=false` (A5); 231 have a `confirmed` venue resolution in `v_venue_market_resolution`; 148 are `partial`/`unverified`, oldest 20 May. `markets` has no `closed_time` column (it has `settled_at`, `resolution_verified_at`). Discovery (P0.2) runs in n8n. |
| **P2.5** | One bucket convention everywhere | todo | | | Scope measured: raw `bands` is read by 20 files in `sql/` and 6 scripts (`databank`, `export_paper_trades`, `paper_exits`, `paper_settlement`, `paper_worker`, …). Most read it only to join `band_id`→`market_id`, which is harmless. The work is the subset reading `band_lo`/`band_hi`/`open_*` or `markets.unit`, and each view changed needs its own live comparison. Settled ladders in `v_settlement_agreement` showed no raw-vs-canonical unit mismatch (507 C, 147 F). P2.2 already switched that view to `v_canonical_markets`. |
| **P2.6** | Real forecast issue times | todo | | | |
| **P2.7** | Label live-weather sources | todo | | | |
| **P3.1** | Fix the floor atom | todo | | | |
| **P3.2** | Stop pricing after the local day ends | todo | | | |
| **P3.3** | Gate the trajectory on fresh readings | todo | | | |
| **P3.4** | Forward-only fits with a significance gate | todo | | | |
| **P3.5** | Fix the skill sample size | todo | | | |
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

