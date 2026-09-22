"""
AD4 backtest runner (Task 12) - the I/O layer around engine.py.

Job-queue architecture per Revision A §6.2 (no Vercel functions): the UI
calls the `queue_backtest` RPC, which inserts a `backtest_runs` row with
status='queued'; `.github/workflows/backtest.yml` polls for queued runs
on a schedule and runs this script, which reads params straight from
Supabase, runs the walk-forward simulation, writes results, and sets
status='complete' (or 'failed', with the error recorded - never silently
dropped).

A run too big for one step's budget does NOT fail. It banks the dates it
finished, records the cursor on backtest_runs.progress, and goes back to
'queued' so the next invocation continues from there - see the block above
run() for the measurement that made this necessary.

Immutable saved runs: this script only ever INSERTs backtest_results/
backtest_trades rows tagged with this run's run_id. Re-running the same
params from the UI creates a NEW backtest_runs row (a new run_id) via
queue_backtest - nothing here ever overwrites a prior run.
"""
import datetime as dt
import os
import sys
from collections import defaultdict

from common import rest, rest_all, insert, rpc, _cfg, _headers
import requests
import paper_engine
import regime
from strategies import StrategyConfig
from backtest.engine import evaluation_instant, simulate_city_day
from backtest import metrics

DATA_STARTS_2025 = "book depth history begins 2026-08-22 (Polymarket publishes no depth history) - " \
                    "forecast accuracy can be backtested against ~2.5 years, strategy profitability cannot " \
                    "until AD4's own market data accumulates. This is a property of the data, not the harness."


# THE STEP BUDGET, AND WHY THE RUNNER HAS TO KNOW IT.
#
# pipeline_daily gives this step 30 minutes. When it overran, GitHub killed
# the process where it stood - so the `running` row it had already written was
# never updated, and poll_and_run_queued() only ever selects status='queued'.
# The result is a run that says it is running for ever and that nothing will
# reclaim: 5f7bc321 sat that way from 2026-09-22 09:26 with no finished_at.
#
# A budget a couple of minutes under the step's own lets the runner stop
# itself and record WHY, which is the difference between a failure and a
# mystery. STALE_AFTER is the other half: a row still `running` long past any
# plausible budget was killed, and the next run says so instead of stepping
# around it.
#
# THE BUDGET IS NOT A PROPERTY OF THE RUNNER, it is a property of whoever
# invoked it, and the two workflows are not the same:
#
#     pipeline_daily.yml   a 30-minute STEP inside a shared job - the sweep
#     backtest.yml         a 120-minute JOB of its own - an explicit ask
#
# Hard-coding 26 would cut a deliberate two-hour backtest off at twenty-six
# minutes and record it as out of time, which is a worse failure than the one
# this replaced. Each workflow passes its own.
BUDGET_MINUTES = int(os.environ.get("BACKTEST_BUDGET_MINUTES") or 26)
STALE_AFTER_MINUTES = int(os.environ.get("BACKTEST_STALE_AFTER_MINUTES") or 90)


def _patch(table, match, values):
    response = requests.patch(f"{_cfg()['url']}/rest/v1/{table}", headers=_headers(),
                   params=match, json=values, timeout=60)
    response.raise_for_status()


def _delete(table, match):
    """Scoped to one run_id, always. See _rewind_to() for why it exists."""
    response = requests.delete(f"{_cfg()['url']}/rest/v1/{table}", headers=_headers(),
                               params=match, timeout=60)
    response.raise_for_status()


def _load_run(run_id):
    rows = rest("backtest_runs", [("select", "*"), ("run_id", f"eq.{run_id}")])
    if not rows:
        raise ValueError(f"backtest_runs {run_id} not found")
    return rows[0]


def _daily_max_observed(city_key, start, end, timezone=None):
    """Venue-date outcomes backed by final station-authority evidence."""
    rows = rest_all("v_verified_weather_outcomes", [
        ("select", "for_date,observed_max_c"), ("city_key", f"eq.{city_key}"),
        ("for_date", f"gte.{start.isoformat()}"), ("for_date", f"lte.{end.isoformat()}"),
        ("order", "for_date.asc"),
    ], order="for_date.asc")
    out = {}
    for r in rows:
        if r.get("observed_max_c") is None:
            continue
        out[str(r["for_date"])] = r["observed_max_c"]
    return out


def strategy_configs_from_rows(rows):
    """Turn `strategies` table rows into runnable configs.

    A PRESENT KEY HOLDING NULL IS NOT A MISSING KEY, and this table is full of
    them: capital_cap_pct, max_concurrent, regime_filter and universe are all
    nullable, and on 21 Sep six of the nine trading strategies carried NULL for
    the first two - seeded before the columns existed, never set since.

    dict.get(key, default) returns the NULL rather than the default, so a
    config built that way arrived with capital_cap_pct None and
    Strategy.size() divided None by 100 the first time a backtest sized
    anything. signal_engine.py reads the same rows as `float(r.get(...) or 5.0)`
    and is fine, which is why only this door was broken.
    """
    def _or(row, key, default):
        value = row.get(key)
        return default if value is None else value

    return [StrategyConfig(
        strategy_id=r["strategy_id"], name=_or(r, "name", r["strategy_id"]),
        side=_or(r, "side", "BOTH"), universe=_or(r, "universe", ["ALL"]),
        regime_filter=_or(r, "regime_filter", []),
        conflict_class=_or(r, "conflict_class", "default"),
        capital_cap_pct=float(_or(r, "capital_cap_pct", 5.0)),
        max_concurrent=int(_or(r, "max_concurrent", 10)),
        enabled=True, extra=_or(r, "extra", {}),
    ) for r in rows]


# ===========================================================================
# WHY THIS WALKS DATES AND REMEMBERS WHERE IT STOPPED.
#
# MEASURED 2026-09-22: no backtest has reached `complete` since 14 September.
# The reason is not the budget, it is what the runner did when it ran out.
# run() walked every market from the first, accumulated every trade in memory,
# and wrote nothing until the last one. Hit the deadline and the whole thing
# was marked `failed` - the work discarded, the next attempt starting again at
# the first market, against the same window, with the same budget.
#
# So a job that needs 90 minutes could never finish, no matter how many
# nights it was given. Not slowly: never. "Re-queue it with a narrower date
# range" was a human working around a harness that could not resume, and the
# narrowed re-queue (079d4d10) then sat `running` from 16:41 because the
# process was killed mid-flight again.
#
# THE CURSOR IS A DATE, NOT A MARKET, and that is what makes recovery exact.
# Trades carry resolution_date but not market_id, so a cursor mid-date could
# not be cleaned up without guessing which of that date's cities had already
# been written. Advancing only at a date boundary means everything after the
# cursor is, by construction, from a chunk that did not finish - so it can be
# deleted and recomputed rather than double-counted. A date is at most 48
# city-days, which is tens of seconds, so the granularity costs nothing.
#
# OUT OF TIME IS `queued`, NOT `failed`. A run that stopped on the budget has
# done real work and will continue tomorrow; calling that a failure both
# throws the work away and tells the operator to go and fix something.
# ===========================================================================
def _markets_by_date(start, end, cities):
    """Every market in the window, grouped by resolution date, in a FIXED order.

    The previous read had no `order` at all. PostgREST is then free to return
    the rows however the planner produced them, so two runs of the same window
    could walk the markets in different orders - which is survivable when a
    run either finishes or is thrown away, and is not survivable once a cursor
    has to mean the same thing on the next night.
    """
    markets = rest("v_canonical_markets", [
        ("select", "market_id,city_key,resolution_date,unit"),
        ("resolution_date", f"gte.{start.isoformat()}"),
        ("resolution_date", f"lte.{end.isoformat()}"),
        ("order", "resolution_date.asc,city_key.asc,market_id.asc"),
    ])
    if cities:
        markets = [m for m in markets if m["city_key"] in cities]
    by_date = defaultdict(list)
    for m in markets:
        by_date[str(m["resolution_date"])].append(m)
    for rows in by_date.values():
        rows.sort(key=lambda m: (m["city_key"], m["market_id"]))
    return by_date


def _bands_for_markets(markets):
    """ONE request for a whole date's ladders instead of one per market.

    Paged on (market_id, band_id), which is unique, so this cannot silently
    lose the ladders that sort last the way an unordered offset read can.
    """
    if not markets:
        return {}
    ids = ",".join(m["market_id"] for m in markets)
    out = defaultdict(list)
    for r in rest_all("v_canonical_bands", [
        ("select", "market_id,band_id,band_lo,band_hi,open_low,open_high,band_label"),
        ("market_id", f"in.({ids})"),
    ], order="market_id.asc,band_id.asc", page_size=1000):
        out[r["market_id"]].append(r)
    return out


def _merge_counts(into, add):
    for strategy_id, counts in (add or {}).items():
        row = into.setdefault(strategy_id, {"fired": 0, "enter": 0, "exit": 0})
        for key, value in counts.items():
            row[key] = row.get(key, 0) + value
    return into


def run(run_id, deadline=None):
    run_row = _load_run(run_id)
    params = run_row.get("params") or {}
    progress = run_row.get("progress") or {}
    _patch("backtest_runs", {"run_id": f"eq.{run_id}"},
           {"status": "running", "started_at": dt.datetime.now(dt.timezone.utc).isoformat()})

    start = dt.date.fromisoformat(params["start_date"])
    end = dt.date.fromisoformat(params["end_date"])
    cities = params.get("cities") or []
    starting_budget = params.get("starting_budget", 10000.0)
    evaluation_lead_days = params.get("evaluation_lead_days", 1)
    max_slippage = ((params.get("cost_params") or {}).get("max_slippage_c")) or 0.05
    strategy_configs = strategy_configs_from_rows(params.get("strategies") or [])

    completed_through = progress.get("completed_through")
    portfolio = paper_engine.Portfolio(
        bankroll=float(progress.get("bankroll", starting_budget)),
        compounding=params.get("compounding", False))
    portfolio.realized_pnl = float(progress.get("realized_pnl", 0.0))
    signal_counts = _merge_counts({}, progress.get("signal_counts"))

    history_cache, obs_cache = {}, {}

    try:
        # timezone comes along too: a daily maximum belongs to the CITY's
        # calendar day, and the backtest was bucketing observations by UTC.
        cities_meta = {c["city_key"]: c
                       for c in rest("cities", {"select": "city_key,unit,icao,timezone"})}
        tz_of = {k: v.get("timezone") for k, v in cities_meta.items()}

        by_date = _markets_by_date(start, end, cities)
        dates = sorted(by_date)
        total_dates = len(dates)
        if completed_through:
            # Anything past the cursor belongs to a chunk that did not finish.
            _delete("backtest_trades", {"run_id": f"eq.{run_id}",
                                        "resolution_date": f"gt.{completed_through}"})
            dates = [d for d in dates if d > completed_through]
            print(f"resuming {run_id} after {completed_through}: "
                  f"{len(dates)} of {total_dates} date(s) left")

        for day in dates:
            if deadline and dt.datetime.now(dt.timezone.utc) >= deadline:
                return _pause(run_id, completed_through, portfolio, signal_counts,
                              len(dates), total_dates)

            day_trades, day_signals, out_of_time = _simulate_date(
                by_date[day], cities_meta, tz_of, start, end, evaluation_lead_days,
                strategy_configs, max_slippage, portfolio, history_cache, obs_cache,
                deadline)
            if out_of_time:
                # The cursor never moved, so this date's partial work is simply
                # not written. Tomorrow recomputes it whole.
                return _pause(run_id, completed_through, portfolio, signal_counts,
                              len(dates), total_dates)

            if day_trades:
                insert("backtest_trades", [{**t, "run_id": run_id} for t in day_trades])
            _merge_counts(signal_counts, metrics.signal_frequency(day_signals))
            completed_through = day
            _patch("backtest_runs", {"run_id": f"eq.{run_id}"}, {"progress": {
                "completed_through": completed_through,
                "bankroll": portfolio.bankroll,
                "realized_pnl": portfolio.realized_pnl,
                "signal_counts": signal_counts,
            }})

        _finish(run_id, starting_budget, signal_counts)

    except Exception as e:
        _patch("backtest_runs", {"run_id": f"eq.{run_id}"}, {
            "status": "failed", "error": str(e), "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        raise


def _simulate_date(markets, cities_meta, tz_of, start, end, evaluation_lead_days,
                   strategy_configs, max_slippage, portfolio, history_cache,
                   obs_cache, deadline):
    """One resolution date, whole. Returns (trades, signals, out_of_time)."""
    trades, signals = [], []
    bands_by_market = _bands_for_markets(markets)

    for m in markets:
        if deadline and dt.datetime.now(dt.timezone.utc) >= deadline:
            return [], [], True

        city_key = m["city_key"]
        resolution_date = dt.date.fromisoformat(m["resolution_date"])
        unit = m.get("unit") or cities_meta.get(city_key, {}).get("unit", "C")
        as_of = evaluation_instant(resolution_date, evaluation_lead_days)

        bands = bands_by_market.get(m["market_id"]) or []
        if not bands:
            continue

        if city_key not in obs_cache:
            obs_cache[city_key] = _daily_max_observed(
                city_key, start - dt.timedelta(days=1), end + dt.timedelta(days=1),
                tz_of.get(city_key))
        actual = obs_cache[city_key].get(m["resolution_date"])
        if actual is None:
            continue  # no observation on record for this date, skip rather than guess

        forecast_rows = regime._forecasts_for_date(city_key, m["resolution_date"], as_of=as_of)
        if not forecast_rows:
            continue
        lead_days = min(r["lead_days"] for r in forecast_rows)
        skill_rows = rest("derived_forecast_skill", [
            ("select", "mae_c,bias_c,n_days,computed_at,evidence_scope"), ("city_key", f"eq.{city_key}"),
            ("lead_days", f"eq.{lead_days}"), ("computed_at", f"lte.{as_of.isoformat()}"),
            ("evidence_scope", "eq.verified_outcomes_v1"),
            ("order", "computed_at.desc"), ("limit", "1"),
        ])
        skill_row = skill_rows[0] if skill_rows else None

        # The rows this city-day was just read for, rather than a second
        # request for the identical set - see regime.classify's docstring.
        reg = regime.classify(city_key, m["resolution_date"], history_cache,
                              as_of=as_of, forecast_rows=forecast_rows)

        # ONE REQUEST FOR THE WHOLE LADDER, not one per band.
        #
        # This loop used to run `rest("book_snapshots", band_id=eq.<one>,
        # limit 1)` once per band - about 15,800 sequential round-trips for a
        # 30-day, 48-city, 9-strategy run, which at ~100ms each is 26 minutes
        # before a single trade is simulated.
        #
        # It was not careless: it replaced a single read that pulled EVERY
        # snapshot up to as_of, hit PostgREST's 1,000-row cap, and left the
        # bands that sorted last with no book at all. book_as_of does
        # `distinct on (band_id)` in the database, which is both complete and
        # bounded, and PostgREST cannot express it.
        book_by_band = {}
        for row in rpc("book_as_of", {
            "p_band_ids": [b["band_id"] for b in bands],
            "p_as_of": as_of.isoformat(),
        }) or []:
            book_by_band[row["band_id"]] = row

        result = simulate_city_day(
            city_key, resolution_date, unit, bands, forecast_rows, skill_row, reg,
            book_by_band, actual, strategy_configs, open_positions=[], as_of=as_of,
            max_slippage=max_slippage, portfolio=portfolio,
        )
        trades.extend(result["trades"])
        signals.extend(result["signals"])
    return trades, signals, False


def _pause(run_id, completed_through, portfolio, signal_counts, left, total):
    """Out of time is not a failure, and the work already banked is not lost."""
    done = total - left
    note = (f"out of time after {done} of {total} date(s) - budget is "
            f"{BUDGET_MINUTES} min. Banked through {completed_through or 'nothing yet'}; "
            f"the next run continues from there.")
    _patch("backtest_runs", {"run_id": f"eq.{run_id}"}, {
        "status": "queued", "error": note,
        "progress": {"completed_through": completed_through,
                     "bankroll": portfolio.bankroll,
                     "realized_pnl": portfolio.realized_pnl,
                     "signal_counts": signal_counts},
    })
    print(f"  {note}")
    return None


def _finish(run_id, starting_budget, signal_counts):
    """Score the run from what was STORED, not from what this process held.

    A resumed run's trades were written across several nights and no single
    process ever held them all, so the metrics have to be computed from
    backtest_trades. That also makes them describe exactly what the database
    contains rather than what memory did.
    """
    trades = rest_all("backtest_trades", [
        ("select", "*"), ("run_id", f"eq.{run_id}"),
    ], order="bt_trade_id.asc", page_size=1000)
    for t in trades:
        for key in ("net_pnl", "gross_pnl", "fee_paid", "slippage_paid", "gas_paid",
                    "shares", "avg_fill_price", "model_prob"):
            if t.get(key) is not None:
                t[key] = float(t[key])

    results = {
        "headline": metrics.headline(trades),
        "strategy": metrics.by_strategy(trades),
        "city": metrics.by_city(trades),
        "costs": metrics.costs(trades),
        "distribution": metrics.distribution(trades),
        "signal_frequency": signal_counts,
        "equity_curve": metrics.equity_curve(trades, starting_budget),
        "calibration": metrics.calibration(trades),
    }
    results["headline"]["data_limitation"] = DATA_STARTS_2025

    # This run's own rows only. A different run_id is never touched, so the
    # promise that a saved run is immutable still holds; what this clears is
    # a half-written result set from an attempt that died after inserting.
    _delete("backtest_results", {"run_id": f"eq.{run_id}"})
    insert("backtest_results", [{"run_id": run_id, "scope": scope, "data": data,
                                 "metrics": data} for scope, data in results.items()])
    _patch("backtest_runs", {"run_id": f"eq.{run_id}"}, {
        "status": "complete", "error": None,
        "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    print(f"run {run_id}: complete, {len(trades)} trade(s) stored")


def reclaim_stalled(stale_after_minutes=STALE_AFTER_MINUTES):
    """Fail runs that say `running` and are not.

    A killed process leaves its row mid-flight. Nothing selects `running`, so
    the row is neither retried nor visible as a failure - it just sits there
    claiming to be working. This marks it failed with the reason, which both
    tells the truth and lets somebody re-queue it deliberately.

    IT NOW RE-QUEUES A RUN THAT BANKED SOMETHING, and that is a change of
    meaning, not of policy. The old rule - never re-queue, "the run that
    stalled is the run that would stall again" - was correct when a retry
    restarted at the first market with the same budget. With a date cursor a
    retry starts where the last one stopped, so re-queueing is how a long
    window finishes rather than how a step becomes permanently oversized.

    A run that banked NOTHING is still failed: its very first date did not fit
    the budget, and nothing about tomorrow changes that. So is a run whose
    cursor has not moved since the last reclaim - two kills at the same date
    is a date that cannot be simulated, not bad luck, and looping on it nightly
    would be the failure mode the old rule was written against.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=stale_after_minutes)
    stalled = rest("backtest_runs", [
        ("select", "run_id,started_at,progress"), ("status", "eq.running"),
        ("started_at", f"lt.{cutoff.isoformat()}"),
    ])
    for row in stalled:
        progress = row.get("progress") or {}
        cursor = progress.get("completed_through")
        stuck_at = progress.get("reclaimed_at_cursor")
        killed = (f"no progress for over {stale_after_minutes} min - the process was "
                  f"killed, most likely by the workflow step timeout")
        if cursor and cursor != stuck_at:
            print(f"  re-queueing stalled run {row['run_id']} from {cursor}")
            _patch("backtest_runs", {"run_id": f"eq.{row['run_id']}"}, {
                "status": "queued",
                "error": f"{killed}. Banked through {cursor}; the next run continues from there.",
                "progress": {**progress, "reclaimed_at_cursor": cursor},
            })
            continue
        print(f"  failing stalled run {row['run_id']} (started {row.get('started_at')})")
        _patch("backtest_runs", {"run_id": f"eq.{row['run_id']}"}, {
            "status": "failed",
            "error": (f"{killed}, and it "
                      + (f"has not advanced past {cursor} across two attempts"
                         if cursor else "banked no complete date at all")
                      + ". Re-queue it with a narrower date range, city list or "
                        "strategy set."),
            "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
    return len(stalled)


def poll_and_run_queued(budget_minutes=BUDGET_MINUTES):
    reclaim_stalled()
    deadline = dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=budget_minutes)
    queued = rest("backtest_runs", [("select", "run_id"), ("status", "eq.queued"), ("order", "created_at.asc")])
    for row in queued:
        if dt.datetime.now(dt.timezone.utc) >= deadline:
            print(f"  budget spent - {row['run_id']} stays queued for the next run")
            break
        print(f"running queued backtest {row['run_id']}")
        try:
            run(row["run_id"], deadline=deadline)
        except Exception as e:
            print(f"  ! run {row['run_id']} failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].strip():
        run(sys.argv[1])
    else:
        poll_and_run_queued()
