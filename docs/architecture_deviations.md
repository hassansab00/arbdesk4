# Deliberate deviations from the literal spec text

`AD4-COMPLETE-BUILD-SPEC.md` + Revision A is internally consistent almost
everywhere, but Revision A's §7 n8n workflow specs occasionally describe
compute (edges, signals, some `derived_*` recomputation) as Postgres RPCs
called from n8n, while Revision A itself states plainly: **"Tasks 1–12
unchanged"** - and Tasks 6, 7, 10 unchanged mean scripts/edge_engine.py,
scripts/capacity.py, scripts/signals.py (Python) are the real
deliverables, not PL/pgSQL reimplementations of the same math.

Where that tension exists, this build keeps the non-trivial logic (ladder
walking, the fee curve, signal rule evaluation) in exactly one place -
Python, already unit-tested - and has n8n (or GitHub Actions) invoke it,
rather than maintaining two implementations of e.g. `walk_ladder` that can
drift. This mirrors a pattern the spec itself already uses for the
backtest harness (§Task 12: "n8n cannot run this... a backtest is heavier
and not I/O bound" -> job runs as a GitHub Action, not inline SQL).

## Specific deviations

**Edge computation (Task 6 / §7.6 P2.1).** The spec's n8n workflow calls
`rpc/compute_edges` "runs entirely in Postgres, no external compute
needed." This build instead runs `scripts/edge_engine.py` as a second step
in `.github/workflows/pipeline_intraday.yml`, right after
`probability_engine.py`. n8n's role (Task 15) is to fire that GitHub
Actions run (or simply do nothing here, since the workflow is already on
its own cron) and then poll `ingest_log` for the result, exactly as it
already does for `measure_skill`. `compute_edges` still exists as a SQL
function in `sql/ad4_rpc.sql` (Task 13b) but is a thin compatibility stub
that raises a notice pointing here - it is not the code path actually used.

**Regime thresholds / capacity / correlation (Task 7 / §7.9 P2.4).**
`recompute_capacity` and `recompute_correlation` genuinely are implemented
as SQL (Postgres's own `corr()` aggregate is the right tool for pairwise
forecast-error correlation, and capacity is a GROUP BY over
`book_snapshots`) - these are real RPCs, not stubs. `scripts/capacity.py`
is a thin Python wrapper that calls them and logs the result via
`log_ingest`, so the Task 7 deliverable name still exists and the job
still shows up in `ingest_log` next to the other jobs.
`recompute_regime_thresholds` is a stub: `scripts/regime.py` (Task 5)
already computes per-city percentile thresholds fresh on every call rather
than reading a cached thresholds table, per the spec's own DERIVED/never-
frozen rule, so there is nothing for this RPC to precompute yet.
`recompute_behavioural_clusters` is also a stub - the spec lists
"behavioural clusters" only under Task 14's "Accumulating" (not "Available
day one") column and never defines a clustering algorithm anywhere. Rather
than invent one, this returns a documented no-op until there's a real
definition and enough realised-trade data to cluster on.

**Signal evaluation (Task 10 / §7.7 P2.2).** Same reasoning as edges:
`scripts/signals.py` is the real implementation of all 15 categories.
`evaluate_signals` in `sql/ad4_rpc.sql` is a thin stub; the actual job
runs as a GitHub Actions workflow.

**Settlement (Task 11 / §7.8 P2.3) is the one case that splits cleanly and
is NOT a deviation:** verifying against `weather.gov`/HKO needs outbound
HTTP calls Postgres can't make, so `scripts/settlement.py` does the
fetch-and-verify, then calls `rpc/settle_markets` to do the atomic
DB-side write (mark trades settled, compute net P&L, append ledger rows)
in one transaction. That RPC is genuine, not a stub.
