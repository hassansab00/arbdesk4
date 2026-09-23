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

## Checklist

| Step | Title | Status | PR | Acceptance result | Notes |
|---|---|---|---|---|---|
| — | Progress file and `CLAUDE.md` pointer | doing | | n/a | First PR. Also adds the plan itself as `docs/AD4_IMPROVEMENT_PLAN.md`. |
| **P0.1** | Capture the baseline | todo | | | |
| **P0.2** | Repo goes private | todo | | | The API already says `private: true` (session log, 23 Sep). The doc and comment updates are still to do. |
| **P0.3** | Retire every existing paper desk | todo | | | Touches paper accounts, so Hassan merges it. |
| **P1.1** | Revoke PUBLIC execute on destructive functions | todo | | | Grants, so Hassan merges it. |
| **P1.2** | Put UI writes behind auth | todo | | | Auth, so Hassan merges it. |
| **P1.3** | Align archiver floors with the SQL prune floors | doing | [#102](https://github.com/hassansab00/arbdesk4/pull/102) + plan/p1-archive-guards | pending: the next archive run after merge must log research, resolution and trades `ok` | Floors fixed in #102 (merged before the plan run; its test parses the SQL guards). This branch adds the rest of the step: a preflight **refusal** now logs `status='error'` with `detail.refused = <the function's reason>`, and a genuine count mismatch still says ARCHIVE COUNT MISMATCH. The 08:16Z run on 23 Sep (before #102) logged `attention` with `preflight.ok=false` for research, resolution and trades. |
| **P1.4** | Stop backfills flooding `research_captures` | doing | plan/p1-archive-guards | pending: apply the migration, then run the plan's rolled-back test UPDATE live | `20260923110000_a_backfill_is_not_research.sql`: on `band_probabilities`, INSERT is always captured and UPDATE only when `raw_prob`/`calibrated_prob`/`centre_c`/`sigma_c` changes; `set local arbdesk.skip_capture = on` skips capture on all six tables. `CLAUDE.md` documents it. The PGlite fixture's `band_probabilities` now matches the live shape (it had 3 columns and a uuid `prob_id`; live has 24 and bigint). Live, 23 Sep: `band_probabilities` is 82,350 of 116,789 captures and 79 MB of 135 MB (`select source_relation, count(*), sum(pg_column_size(r.*)) from research_captures r group by 1`). |
| **P1.5** | Guard the prune on a confirmed push | doing | plan/p1-archive-guards | pending: the next archive run after merge | Finding confirmed in the code: the push loop ended with `git pull --rebase` and exited 0 after four failed pushes, and `is_committed()` checked local HEAD. Now the commit step has the `pushed=1` guard and writes `ok=1`; the prune runs only on `steps.commit.outputs.ok == '1'`; and `is_committed()` fetches origin and compares each file's blob with `FETCH_HEAD`. The index-commit step has the same guard. Unit-tested against a real bare remote. |
| **P1.6** | Get under the cap | todo | | | 622.4 MB = 124.5% of the 500 MB tier (`select storage_pressure()`, 23 Sep 08:56Z). |
| **P2.1** | Read every METAR | todo | | | |
| **P2.2** | Fix `v_station_day_max` / the agreement view | todo | | | |
| **P2.3** | Rebuild `observation_trust` from venue evidence | todo | | | |
| **P2.4** | Keep market state current | todo | | | |
| **P2.5** | One bucket convention everywhere | todo | | | |
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
