"""
AD4 backtest runner (Task 12) - the I/O layer around engine.py.

Job-queue architecture per Revision A §6.2 (no Vercel functions): the UI
calls the `queue_backtest` RPC, which inserts a `backtest_runs` row with
status='queued'; `.github/workflows/backtest.yml` polls for queued runs
on a schedule and runs this script, which reads params straight from
Supabase, runs the walk-forward simulation, writes results, and sets
status='complete' (or 'failed', with the error recorded - never silently
dropped).

Immutable saved runs: this script only ever INSERTs backtest_results/
backtest_trades rows tagged with this run's run_id. Re-running the same
params from the UI creates a NEW backtest_runs row (a new run_id) via
queue_backtest - nothing here ever overwrites a prior run.
"""
import datetime as dt
import os
import sys

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


def run(run_id, deadline=None):
    run_row = _load_run(run_id)
    params = run_row.get("params") or {}
    _patch("backtest_runs", {"run_id": f"eq.{run_id}"},
           {"status": "running", "started_at": dt.datetime.now(dt.timezone.utc).isoformat()})

    start = dt.date.fromisoformat(params["start_date"])
    end = dt.date.fromisoformat(params["end_date"])
    cities = params.get("cities") or []
    starting_budget = params.get("starting_budget", 10000.0)
    evaluation_lead_days = params.get("evaluation_lead_days", 1)
    max_slippage = ((params.get("cost_params") or {}).get("max_slippage_c")) or 0.05

    strategy_configs = strategy_configs_from_rows(params.get("strategies") or [])

    all_trades, all_signals, all_conflicts = [], [], []
    history_cache = {}
    portfolio = paper_engine.Portfolio(bankroll=starting_budget, compounding=params.get("compounding", False))

    try:
        # timezone comes along too: a daily maximum belongs to the CITY's
        # calendar day, and the backtest was bucketing observations by UTC.
        cities_meta = {c["city_key"]: c
                       for c in rest("cities", {"select": "city_key,unit,icao,timezone"})}
        tz_of = {k: v.get("timezone") for k, v in cities_meta.items()}
        markets = rest("v_canonical_markets", [
            ("select", "market_id,city_key,resolution_date,unit"),
            ("resolution_date", f"gte.{start.isoformat()}"), ("resolution_date", f"lte.{end.isoformat()}"),
        ])
        if cities:
            markets = [m for m in markets if m["city_key"] in cities]

        obs_cache = {}
        for m in markets:
            city_key = m["city_key"]
            resolution_date = dt.date.fromisoformat(m["resolution_date"])
            unit = m.get("unit") or cities_meta.get(city_key, {}).get("unit", "C")
            as_of = evaluation_instant(resolution_date, evaluation_lead_days)

            bands = rest("v_canonical_bands", [
                ("select", "band_id,band_lo,band_hi,open_low,open_high,band_label"),
                ("market_id", f"eq.{m['market_id']}"),
            ])
            if not bands:
                continue

            if city_key not in obs_cache:
                obs_cache[city_key] = _daily_max_observed(
                    city_key, start - dt.timedelta(days=1), end + dt.timedelta(days=1),
                    tz_of.get(city_key))
            actual = obs_cache[city_key].get(m["resolution_date"])
            if actual is None:
                continue  # TODO: unmeasured - no observation on record for this date, skip rather than guess

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

            reg = regime.classify(city_key, m["resolution_date"], history_cache, as_of=as_of)

            # ONE REQUEST FOR THE WHOLE LADDER, not one per band.
            #
            # This loop used to run `rest("book_snapshots", band_id=eq.<one>,
            # limit 1)` once per band - about 15,800 sequential round-trips
            # for a 30-day, 48-city, 9-strategy run, which at ~100ms each is
            # 26 minutes before a single trade is simulated. It is what made
            # pipeline_daily's step time out every night.
            #
            # It was not careless: it replaced a single read that pulled EVERY
            # snapshot up to as_of, hit PostgREST's 1,000-row cap, and left
            # the bands that sorted last with no book at all - one reason a
            # backtest over 739 city-days once placed zero trades. book_as_of
            # does `distinct on (band_id)` in the database, which is both
            # complete and bounded, and PostgREST cannot express it.
            book_by_band = {}
            for row in rpc("book_as_of", {
                "p_band_ids": [b["band_id"] for b in bands],
                "p_as_of": as_of.isoformat(),
            }) or []:
                book_by_band[row["band_id"]] = row

            if deadline and dt.datetime.now(dt.timezone.utc) >= deadline:
                raise TimeoutError(
                    f"ran out of time after {len(book_by_band)} ladders - the step "
                    f"budget is {BUDGET_MINUTES} min. Narrow the run's dates, cities "
                    f"or strategies, or raise timeout-minutes on the workflow step."
                )

            result = simulate_city_day(
                city_key, resolution_date, unit, bands, forecast_rows, skill_row, reg,
                book_by_band, actual, strategy_configs, open_positions=[], as_of=as_of,
                max_slippage=max_slippage, portfolio=portfolio,
            )
            all_trades.extend(result["trades"])
            all_signals.extend(result["signals"])
            all_conflicts.extend(result["conflicts"])

        results = {
            "headline": metrics.headline(all_trades),
            "strategy": metrics.by_strategy(all_trades),
            "city": metrics.by_city(all_trades),
            "costs": metrics.costs(all_trades),
            "distribution": metrics.distribution(all_trades),
            "signal_frequency": metrics.signal_frequency(all_signals),
            "equity_curve": metrics.equity_curve(all_trades, starting_budget),
            "calibration": metrics.calibration(all_trades),
        }
        results["headline"]["data_limitation"] = DATA_STARTS_2025

        result_rows = [{"run_id": run_id, "scope": scope, "data": data, "metrics": data}
                       for scope, data in results.items()]
        insert("backtest_results", result_rows)
        if all_trades:
            insert("backtest_trades", [{**t, "run_id": run_id} for t in all_trades])

        _patch("backtest_runs", {"run_id": f"eq.{run_id}"}, {
            "status": "complete", "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        print(f"run {run_id}: {len(all_trades)} trades, {len(markets)} city-days evaluated")

    except Exception as e:
        _patch("backtest_runs", {"run_id": f"eq.{run_id}"}, {
            "status": "failed", "error": str(e), "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        })
        raise


def reclaim_stalled(stale_after_minutes=STALE_AFTER_MINUTES):
    """Fail runs that say `running` and are not.

    A killed process leaves its row mid-flight. Nothing selects `running`, so
    the row is neither retried nor visible as a failure - it just sits there
    claiming to be working. This marks it failed with the reason, which both
    tells the truth and lets somebody re-queue it deliberately.

    It does NOT re-queue automatically. The run that stalled is the run that
    would stall again, and a queue that retries the same oversized job every
    night is how a 30-minute step becomes a permanent 30-minute step.
    """
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=stale_after_minutes)
    stalled = rest("backtest_runs", [
        ("select", "run_id,started_at"), ("status", "eq.running"),
        ("started_at", f"lt.{cutoff.isoformat()}"),
    ])
    for row in stalled:
        print(f"  reclaiming stalled run {row['run_id']} (started {row.get('started_at')})")
        _patch("backtest_runs", {"run_id": f"eq.{row['run_id']}"}, {
            "status": "failed",
            "error": (f"no progress for over {stale_after_minutes} min - the process was "
                      f"killed, most likely by the workflow step timeout. Re-queue it "
                      f"with a narrower date range, city list or strategy set."),
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
