# Working in this repository

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
cd web && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/next build
```

## Generated files have tests that catch them going stale

`web/lib/provenance.ts`, `web/lib/sqlOwner.ts` and `sql/ad4_98_ui_health.sql`
are generated. If their tests fail, run the generator rather than editing them:

```bash
python3 tools/gen_provenance.py
python3 tools/gen_sql_owner.py
```

## Every .sql file must be listed in sql/INSTALL_ORDER.txt

Exactly once, in dependency order. `tests/test_sql_order.py` enforces it. A
file that exists but is not listed is a file nobody installs.

## Scheduled workflows have a budget

`tests/test_github_actions.py` caps total scheduled runs per month, because a
private repo meters Actions minutes. Adding a schedule means either cutting
another cadence or raising `SCHEDULED_RUN_BUDGET` **with the reason written
into the constant** - there is a worked example there already.
