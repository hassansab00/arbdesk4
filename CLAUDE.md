# Working in this repository

## Hassan's standing rules

These come from Hassan, who owns this platform. They apply to every session
and override defaults.

**No inventing numbers, no assuming, no messing up. Work scientifically.**
- Every number you state (in chat, a commit message, a comment, a doc) comes
  from something you ran: a query against the live database, a test, a log,
  a file. Say where it came from. If you have not measured it, say "not
  measured"; do not estimate and present it as fact.
- Do not assume how the code or the live system behaves. Read the code and
  query the live database first. A comment, a doc, an audit or an earlier
  session's claim is a lead to check, not a fact.
- Do not break what works. Before replacing a live view or function, prove
  the new one returns the same rows as the old: one REPEATABLE READ snapshot,
  `EXCEPT ALL` in both directions. After a change, check it on the next real
  run rather than assuming it worked.
- Say what you have not verified. Report a failure as a failure.

**The paper desks are examples, not the truth.** Judge and improve the
engines behind them (forecasts, probabilities, pricing, strategy logic) on
settled outcomes. What one desk happened to fill says nothing about a
strategy.

**Never delete data.** Rows leave Postgres only through the archive: exported
into the repository, verified, committed, then pruned, so the platform can
still read them. Anything that should go away is archived or retired so it
stays readable, unless Hassan explicitly asks for it to be removed.

**No cutting corners.** Keep the quality structures (tests, verification, the
two-phase archive) intact, and fix things at their root cause.

**Stay focused** on what was asked. Mention anything else you notice briefly;
do not widen the work on your own.

**Pull requests are enabled.** When a piece of work is finished and both test
suites pass, push it and open a PR without waiting to be asked. Merge it once
CI is green on the PR and Hassan has said to merge.

**Secrets.** Never print a key's value. The Supabase secret key never goes in
a Vercel `NEXT_PUBLIC_*` variable or an n8n Config node. Email workflows stay
disabled.

## The improvement plan in progress

`docs/AD4_IMPROVEMENT_PLAN.md` (v2, 23 Sep; v2.1 the same evening adds P1.7, P1.8, P2.8 and P3.8, the hit tournament; v2.2 on 26 Sep adds P2.9, P2.10, P3.9, P4.7 and amends P7.2/P7.3: the predictive engine learns station errors and the remaining day; v2.3 on 27 Sep adds P4.8, P4.9 and P5.14 and amends P3.9, P5.3, P7.2 and P7.3: the board shows what was priced, a rerun cannot step twice, the width follows the centre actually served) is the plan being executed.
`docs/PLAN_PROGRESS.md` is its checklist: every step's status, PR, acceptance
result and notes. **Read the progress file first and resume from it**, and
update it in every PR that advances a step. Where this file and the plan
conflict, the plan wins, because it is newer. Three of its rules apply to all
work, not just to plan steps:

- **Rule 6: shadow is free, capital is Hassan's.** Strategies may run in
  `shadow` mode without asking. Anything that puts capital on the
  **portfolio** account needs Hassan's approval: turning allocation on, the
  bankroll, and the fixed safety rails.
- **Rule 7: Actions minutes are a hard budget.** The repo is private, and
  the account is on GitHub Pro: 3,000 minutes a month (Hassan, 4 Oct), which
  scheduled work and CI share; keep the total under it. Only plan step P6.1 changes
  `SCHEDULED_MINUTE_BUDGET` / `SCHEDULED_RUN_BUDGET`, and it writes the
  reason into the constant. Every workflow's measured minutes go into
  `MEASURED_MINUTES`. No new scheduled workflow is added outside P6.1.
- **Rule 11: adaptive never means unbounded.** Every learned parameter has a
  prior, hard bounds, a minimum sample before it moves off the prior, a
  maximum change per nightly update, and a version recorded on every decision
  that used it. Nothing learned may be evaluated on the data it was learned
  from.

## Run the WHOLE suite before you push, not just pytest

`.github/workflows/tests.yml` runs two things. Running only the first is how
five commits went to `main` with CI red on 16 Sep while every local run said
892 passed:

```bash
PYTHONPATH=scripts python -m pytest tests/ -q          # 1. the Python suite
npm ci --prefix tests/database --ignore-scripts        # 2. the database contracts
npm test --prefix tests/database
```

**`tests/database/paper-contracts.cjs` is the one that gets forgotten.** It
spins up a real Postgres (PGlite), builds a fixture schema, applies **every**
file in `supabase/migrations` in filename order, and then asserts the paper
engine's transaction contracts - leases, cash ceilings, approvals, exits,
settlement payouts, RLS.

Two consequences worth knowing before you write a migration:

- **The fixture is not the production schema.** Tables created by `sql/*.sql`
  (which this harness never applies) do not exist there. `paper_trades` lives
  in `sql/ad4_00_preflight.sql`, so a migration doing
  `alter table public.paper_trades ...` fails here with
  `relation "public.paper_trades" does not exist` while working perfectly in
  production. When that happens, add the table to the fixture block at the top
  of `paper-contracts.cjs`, matching the live column types and NOT NULLs -
  otherwise the contracts pass against a shape the database does not have.

- **Every migration runs, every time.** A migration that is not idempotent, or
  that depends on rows only production has, breaks this suite for everyone.

If you change the web app, also:

```bash
cd web && ./node_modules/.bin/tsc --noEmit && npm run test:routes && ./node_modules/.bin/next build
```

Every browser write goes through `web/app/api/operator` or a route that
calls `requireOperator`, and the server makes it with the service key; the
anon key cannot call a write RPC (plan v2 P1.2). **Sign-in is off** (Hassan,
23 Sep: he is the only user); `OPERATOR_SIGN_IN=required` on the server turns
the operator check back on. `test:routes` tests both modes. The data client in
`web/lib/supabase.ts` stays anonymous and must never carry a session:
`tests/test_the_definer_views_are_a_decision.py` says why.

## A backfill switches research capture off for its own transaction

`preserve_research_output` copies rows from six tables into
`research_captures` on insert and update. An untracked backfill UPDATE on
22 Sep copied 66,345 rows in an hour. Any deliberate backfill or bulk
UPDATE of those tables (`band_probabilities`, `signals`,
`fact_forecast_outcome`, `fact_band_outcome`, `fact_signal_outcome`,
`model_versions`) runs like this:

```sql
begin;
set local arbdesk.skip_capture = on;   -- ends with the transaction
update public.band_probabilities set ... where ...;
commit;
```

On `band_probabilities` an update is captured only when `raw_prob`,
`calibrated_prob`, `centre_c` or `sigma_c` actually changes
(`20260923110000_a_backfill_is_not_research.sql`).

## Check a changed view as the role that reads it

The Supabase SQL tool runs as the owner, which can read everything. On 23 Sep
three browser-read views were rebuilt on `v_canonical_bands`/`_markets`. Every
equivalence check passed as the owner, and every anon read failed for 2 min
43 s: those views are `security_invoker` over the private
`proprietary_data_corrections`, and `security_invoker` checks as the querying
role even through an owner-rights view. After changing a view the web app
reads, and before calling it done, read it as that role:

```sql
set local role anon;   -- in the same execute_sql call
select count(*) from v_the_view_you_changed;
```

## Generated files have tests that catch them going stale

`web/lib/provenance.ts`, `web/lib/sqlOwner.ts` and `sql/ad4_98_ui_health.sql`
are generated. If their tests fail, run the generator rather than editing them:

```bash
python3 tools/gen_provenance.py
python3 tools/gen_sql_owner.py
```

The `paths:` block of `.github/workflows/tests.yml` is generated too: a pull
request that changes only documents no test reads does not run the suite. If
a test starts reading a document, `tests/conftest.py` fails the run; then run
`python3 tools/ci_paths.py --write` and commit `tests.yml` with the change.

## Every .sql file must be listed in sql/INSTALL_ORDER.txt

Exactly once, in dependency order. `tests/test_sql_order.py` enforces it. A
file that exists but is not listed is a file nobody installs.

## Scheduled workflows have a budget

`tests/test_github_actions.py` caps total scheduled runs per month, because a
private repo meters Actions minutes. Adding a schedule means either cutting
another cadence or raising `SCHEDULED_RUN_BUDGET` **with the reason written
into the constant** - there is a worked example there already.
