# Paper Trades rollout and implementation status

This is an additive implementation on ArbDesk4, based on main
`2004dd2c6fd956798df6040f1456321ede0dd7ec`. The existing colour tokens,
layout components, pages and strategy classes are retained. Paper Trades adds
a single credential-free paper account and a transactional execution path beside legacy trade history.

The four migrations were applied to the existing Supabase project on
2026-09-12. Their filenames match the server's recorded migration versions.
Live checks confirmed RLS, private research history, denied worker truncation,
zero seeded accounts or orders, and service-role-only single-desk commands.
The browser anon role gets no paper-table or command access. The matching web
deployment and worker/n8n steps below are still required.

## Implemented

- Manual BUY tickets, cancellation, partial position exits, assisted plan
  approval/rejection, automatic account policies and optional take-profit/stop-loss exits.
- Account locks, cash reservations, single-desk RPCs, stable command IDs,
  worker leases, expiry cleanup and append-only cash/activity records.
- Direct YES/NO token books, token/outcome identity checks, timestamp rejection,
  limit/tick/minimum-size checks, fee-inclusive IOC depth walking and retained
  per-level fills. A reused snapshot cannot replenish an account's consumed depth.
- Equal-share basket proposals with fee-aware plan limits, current decision
  evidence, forecast date/age checks, label/unit validation and explicit blocked reasons.
  Legs execute independently: a proposal does not promise an atomic basket fill.
- Venue-confirmed binary settlement requires matching condition/token identities,
  Gamma's closed/resolved state and CLOB's unique winner. Both raw responses are
  retained; payouts, released cost basis and realized P&L are one transaction.
  Split/void or disagreeing resolutions remain unpaid pending reconciliation.
- Original strategy inputs, decision time and basket identity are saved with
  signals. Signal context reads are paginated and use market units and matching
  forecast dates instead of a city default and a single UTC date.
- Immutable research revisions for probability, signal, outcome and model-version
  writes, plus deduplicated predictive/synthesis/learning view records. History
  starts when capture is enabled; existing rows are not retroactively proven
  to have been available before an outcome. View captures preserve distinct
  output records, not a complete replay manifest of every unchanged input row.
- Bounded forecast backfill continuation, strict lead-time coverage, safe manual
  backtest dispatch, confirmed backtest-result writes and no extra Actions cron.
- Revised P0.2 contract date/unit inference, P1.5 weather timestamp/source handling,
  and a credential-bound P2.2 paper workflow with bounded HTTP retries.

## Installation sequence

1. Review the branch and run the checks below. For another environment, apply the four SQL migrations in
   `supabase/migrations/` in filename order using Supabase's migration tooling.
   They add tables/functions/triggers and do not seed money, place orders,
   or enable automatic trading.
   Do not reapply these migrations to the existing project. Its older migration
   history also predates this repository's migrations folder; reconcile that
   baseline before using a blanket `supabase db push`.
2. Apply `20260912151318_single_desk_paper_access.sql`. It creates one paper-only
   desk but grants its commands only to `service_role`. The web server exposes a
   fixed, input-limited Paper Trades API backed by the JWT-verified `paper-desk`
   Edge Function; the browser gets no direct access to
   paper tables, RPCs, research captures, or worker evidence. The first visit
   creates the desk after you choose its starting paper cash.
3. Deploy the Python worker on an existing approved host using
   `Dockerfile.paper-worker`. Set server-only `SUPABASE_URL`,
   `SUPABASE_SERVICE_KEY`, `PAPER_WORKER_TOKEN` (random, at least 32 characters),
   and `ARBDESK_ENGINE_VERSION` (the deployed commit). Put HTTPS and normal
   request-rate limits in front of port 8080. No host has been provisioned by this build.
4. Create two different n8n HTTP Header Auth credentials: `AD4 Paper Worker`
   sends `Authorization: Bearer <PAPER_WORKER_TOKEN>` to the worker;
   `AD4 Paper Webhook` authenticates incoming P2.2 requests with a different
   secret. Keep both values inside n8n Credentials. Bind IDs using
   `n8n/bind_credentials.py --worker-id <id> --paper-webhook-id <id>`.
5. Import `n8n/P2.2_paper_trades.template.json` (or its bound import file), set
   `Config.worker_url` to the HTTPS worker origin, and manually execute it.
   Importing does not activate it. HTTP 202 acknowledges webhook receipt;
   workflow success and paper activity confirm completion. Connect it to the
   existing data-refresh flow using its authenticated webhook. No new recurring
   schedule is supplied; this avoids accidentally consuming n8n executions.
6. Deploy the `paper-desk` Supabase Edge Function with JWT verification enabled,
   then deploy the web branch through the existing web deployment. A server-only
   `SUPABASE_SERVICE_KEY` in Vercel is optional; it bypasses the Edge hop when
   present. Set `PAPER_WORKER_URL` and `PAPER_WORKER_TOKEN` there as well.
   The `/api/paper-cycle` route wakes the worker after a manual order, assisted
   approval, or exit. It accepts no payload and the worker token remains server-only.
   Neither value may use a `NEXT_PUBLIC_` prefix.
   Open Paper Trades, choose starting **paper** cash, and test a small manual
   order and an exit. No exchange signing key is used anywhere in this path.
7. After migrations and worker checks, set the GitHub repository variable
   `PAPER_TRADES_ENABLED=true` to enable the shared-runner research capture,
   proposal, exit-policy and queue-recovery steps. Keep legacy
   `settings.auto_approve=false`: new automation is selected per paper account.
   The four-hour recovery sweep alone is too slow for five-minute orders;
   the n8n worker connection is required for timely execution.
8. Optional: apply `sql/ad4_52_paper_workflow_catalogue.sql`. The existing
   freshness specification also lists the added tables. Private account and
   archive payloads remain restricted by RLS and explicit grants.

## Remaining release gates

- Visual browser review is outstanding: branch previews redirect unauthenticated
  visitors to Vercel login. Type-check/build success does not replace a visual review.
- The legacy weather-source settlement parser is unverified and
  `settlement_verified` remains false. The new venue-confirmed binary adapter
  was checked against a real resolved London 2026-09-11 contract and has separate
  transaction tests; it still needs operational validation on new paper holdings.
  Void/split payouts and post-resolution corrections require reconciliation.
- Historical zero-width bands, unit mismatches and frozen outcome defects need
  an evidence-backed correction/backfill. New guards prevent using bad inputs;
  they do not make existing history correct.
- Legacy analytics still reads legacy `paper_trades`/`ledger`. New account
  balances and execution history are shown in Paper Trades; combined analytics
  and per-strategy lot attribution require a compatibility projection.
- Automatic exits are account-level profit/loss policies. Strategy-specific
  forecast-reversal exits and coordinated basket reductions remain separate work.
- Backtest claims/leases, point-in-time research manifests, calibrated
  out-of-sample evaluation, archive export/restore checks and the broader legacy
  anonymous-RPC/security audit remain on the improvement plan.
- Live n8n import, worker hosting and end-to-end live-data
  paper operation must be verified before treating the system as operational.

## Verification

```bash
PYTHONPATH=scripts python -m pytest tests/ -q
npm ci --prefix tests/database --ignore-scripts
npm test --prefix tests/database
python scripts/validate_n8n_workflows.py
python tools/gen_provenance.py
npm ci --prefix web
npm run build --prefix web
```

The database contract suite runs real PostgreSQL statements in PGlite with
Supabase-like default grants. It verifies migrations, RLS/ownership, denied
archive writes/truncation, duplicate commands/approvals, account leases,
fee/cash arithmetic, cancellation, partial inventory sales, automatic exits
while entries are paused, and idempotent expiry cleanup. It is not a substitute
for the live project's permissions/configuration check.

Verification on 2026-09-12: 615 Python tests passed, 6 non-applicable workflow
cases skipped; the database transaction suite, 14-workflow structural validator,
and Next.js production build passed. Live venue reads confirmed the book/fee
metadata and a matching resolved-market winner. Browser review remains blocked
by the authenticated Vercel preview noted above.

API references: [Polymarket fees](https://docs.polymarket.com/trading/fees),
[market details](https://docs.polymarket.com/market-data/market-details),
[order books](https://docs.polymarket.com/api-reference/market-data/get-order-book),
[n8n webhook credentials](https://docs.n8n.io/integrations/builtin/credentials/webhook/).
Venue parameters are read per market; no fixed weather fee is assumed by the worker.
