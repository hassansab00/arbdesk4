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
import sys

from common import rest, rest_all, insert, _cfg, _headers, city_local_date
import requests
import paper_engine
import regime
from strategies import StrategyConfig
from backtest.engine import evaluation_instant, simulate_city_day
from backtest import metrics

DATA_STARTS_2025 = "book depth history begins 2026-08-22 (Polymarket publishes no depth history) - " \
                    "forecast accuracy can be backtested against ~2.5 years, strategy profitability cannot " \
                    "until AD4's own market data accumulates. This is a property of the data, not the harness."


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
    rows = rest_all("weather_observations", [
        ("select", "valid_at,temp_c"), ("city_key", f"eq.{city_key}"),
        ("valid_at", f"gte.{start.isoformat()}"), ("valid_at", f"lte.{end.isoformat()}"),
        ("order", "valid_at.asc"),
    ], order="valid_at.asc,source.asc")
    out = {}
    for r in rows:
        if r.get("temp_c") is None:
            continue
        # the CITY's day, not UTC - see common.city_local_date
        d = city_local_date(r["valid_at"], timezone)
        out[d] = max(out.get(d, r["temp_c"]), r["temp_c"])
    return out


def run(run_id):
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

    strategy_rows = params.get("strategies") or []
    strategy_configs = [StrategyConfig(
        strategy_id=s["strategy_id"], name=s.get("name", s["strategy_id"]), side=s.get("side", "BOTH"),
        universe=s.get("universe", ["ALL"]), regime_filter=s.get("regime_filter", []),
        conflict_class=s.get("conflict_class", "default"), capital_cap_pct=s.get("capital_cap_pct", 5.0),
        max_concurrent=s.get("max_concurrent", 10), enabled=True, extra=s.get("extra", {}),
    ) for s in strategy_rows]

    all_trades, all_signals, all_conflicts = [], [], []
    history_cache = {}
    portfolio = paper_engine.Portfolio(bankroll=starting_budget, compounding=params.get("compounding", False))

    try:
        # timezone comes along too: a daily maximum belongs to the CITY's
        # calendar day, and the backtest was bucketing observations by UTC.
        cities_meta = {c["city_key"]: c
                       for c in rest("cities", {"select": "city_key,unit,icao,timezone"})}
        tz_of = {k: v.get("timezone") for k, v in cities_meta.items()}
        markets = rest("markets", [
            ("select", "market_id,city_key,resolution_date"),
            ("resolution_date", f"gte.{start.isoformat()}"), ("resolution_date", f"lte.{end.isoformat()}"),
        ])
        if cities:
            markets = [m for m in markets if m["city_key"] in cities]

        obs_cache = {}
        for m in markets:
            city_key = m["city_key"]
            resolution_date = dt.date.fromisoformat(m["resolution_date"])
            unit = cities_meta.get(city_key, {}).get("unit", "C")
            as_of = evaluation_instant(resolution_date, evaluation_lead_days)

            bands = rest("bands", [
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
                ("select", "mae_c,bias_c,n_days,computed_at"), ("city_key", f"eq.{city_key}"),
                ("lead_days", f"eq.{lead_days}"), ("computed_at", f"lte.{as_of.isoformat()}"),
                ("order", "computed_at.desc"), ("limit", "1"),
            ])
            skill_row = skill_rows[0] if skill_rows else None

            reg = regime.classify(city_key, m["resolution_date"], history_cache, as_of=as_of)

            book_rows = rest("book_snapshots", [
                ("select", "*"), ("band_id", f"in.({','.join(b['band_id'] for b in bands)})"),
                ("observed_at", f"lte.{as_of.isoformat()}"), ("order", "band_id,observed_at.desc"),
            ])
            book_by_band = {}
            for r in book_rows:
                book_by_band.setdefault(r["band_id"], r)

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


def poll_and_run_queued():
    queued = rest("backtest_runs", [("select", "run_id"), ("status", "eq.queued"), ("order", "created_at.asc")])
    for row in queued:
        print(f"running queued backtest {row['run_id']}")
        try:
            run(row["run_id"])
        except Exception as e:
            print(f"  ! run {row['run_id']} failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].strip():
        run(sys.argv[1])
    else:
        poll_and_run_queued()
