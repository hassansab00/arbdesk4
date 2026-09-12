"""
AD4 signals engine (Task 10). Runs all enabled strategies, resolves
conflicts, layers on the system-wide (non-strategy) signal categories,
dedupes, and hands every fired signal to paper_engine.process_signal()
for logging + (if auto_approve) filling.

Every signal carries severity, city_key, band_id, side, strategy_id, ttl,
and a dedupe_key that prevents the same condition re-firing every cycle -
dedupe is enforced here by checking for an existing signals row with the
same dedupe_key still inside its ttl before inserting a new one.
"""
import datetime as dt
import uuid
from dataclasses import asdict
from collections import defaultdict

from common import rest, rest_all, insert, get_cities
import edge_engine
import paper_engine
import regime
from strategies import REGISTRY, StrategyConfig
from strategies.base import BandView, Context
from strategies.conflicts import resolve_conflicts

DEFAULT_TTL_MINUTES = 30
STALE_JOB_HOURS = 6
PEAK_WINDOW_SOON_MINUTES = 30
THIN_BOOK_MIN_USD = 200.0
SELLOFF_CENTS = 0.10


# --------------------------------------------------------------------------
# Pure detectors - independently unit-testable.
# --------------------------------------------------------------------------

def detect_model_shift(prev_centre_c, curr_centre_c, band_width_c):
    if prev_centre_c is None or curr_centre_c is None or not band_width_c:
        return False, 0.0
    bands_moved = abs(curr_centre_c - prev_centre_c) / band_width_c
    return bands_moved >= 1.0, bands_moved


def detect_regime_change(prev_label, curr_label):
    return prev_label is not None and curr_label is not None and prev_label != curr_label


def detect_thin_book(fillable_usd, min_required=THIN_BOOK_MIN_USD):
    return fillable_usd is not None and fillable_usd < min_required


def detect_peak_window_open(minutes_to_peak, threshold=PEAK_WINDOW_SOON_MINUTES):
    return minutes_to_peak is not None and 0 <= minutes_to_peak <= threshold


def detect_selloff(price_now, price_before, cents_threshold=SELLOFF_CENTS):
    if price_now is None or price_before is None:
        return False, 0.0
    move = abs(price_now - price_before)
    return move >= cents_threshold, move


def system_health_signals(ingest_log_rows, now, stale_hours=STALE_JOB_HOURS):
    """ingest_log_rows: latest row per job, [{"job","status","logged_at"}]."""
    out = []
    for row in ingest_log_rows:
        job = row.get("job")
        if row.get("status") == "error":
            out.append(_system_signal(f"job_failed:{job}", "medium", {"job": job}))
            continue
        logged_at = row.get("logged_at")
        if logged_at:
            ts = dt.datetime.fromisoformat(logged_at.replace("Z", "+00:00"))
            age_h = (now - ts).total_seconds() / 3600.0
            if age_h > stale_hours:
                out.append(_system_signal(f"job_stale:{job}:{age_h:.1f}h", "medium", {"job": job, "age_h": age_h}))
    return out


def _system_signal(reason, severity, payload):
    from strategies.base import Signal, dedupe_key
    return Signal(strategy_id="system", band_id=None, side=None, action="ALERT", reason=reason,
                  price_at_fire=None, prob_at_fire=None, edge_at_fire=None, suggested_shares=0.0,
                  confidence=None, regime_label=None, severity=severity,
                  dedupe_key=dedupe_key("system", "none", "none", "ALERT", reason), payload=payload)


def anomaly_signals(anomaly_rows):
    from strategies.base import Signal, dedupe_key
    out = []
    for a in anomaly_rows:
        out.append(Signal(
            strategy_id="system", band_id=a.get("band_id"), side=a.get("side"), action="ALERT",
            reason="implausible_edge_anomaly", price_at_fire=None, prob_at_fire=None,
            edge_at_fire=a.get("value"), suggested_shares=0.0, confidence=None, regime_label=None,
            severity="critical",
            dedupe_key=dedupe_key("system", a.get("band_id") or "none", a.get("side") or "none",
                                   "ALERT", f"anomaly:{a.get('detected_at')}"),
            payload={"anomaly_id": a.get("id"), "detail": a.get("detail")},
        ))
    return out


# --------------------------------------------------------------------------
# Strategy orchestration
# --------------------------------------------------------------------------

def run_strategies(ctx, strategy_configs, open_positions, portfolio=None):
    """
    Runs every enabled strategy's entry+exit rules, sizes every ENTER
    signal via that strategy's own size(signal, portfolio) - entry_signals
    itself has no portfolio context, so every strategy deliberately leaves
    suggested_shares at 0.0/N-from-formula for this step to finalise -
    then applies the Task 8 conflict rules before returning.
    """
    all_entries, all_exits = [], []
    for cfg in strategy_configs:
        cls = REGISTRY.get(cfg.strategy_id)
        if cls is None or not cfg.enabled:
            continue
        strat = cls(cfg)
        for sig in strat.entry_signals(ctx):
            sig.suggested_shares = strat.size(sig, portfolio)
            all_entries.append(sig)
        all_exits.extend(strat.exit_signals(ctx, open_positions))

    allowed_entries, blocked_entries, conflict_rows = resolve_conflicts(all_entries, open_positions)
    return allowed_entries + all_exits, blocked_entries, conflict_rows


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def _strategy_configs():
    rows = rest("strategies", {"select": "*"})
    out = []
    for r in rows:
        out.append(StrategyConfig(
            strategy_id=r["strategy_id"], name=r.get("name", r["strategy_id"]), side=r.get("side", "BOTH"),
            universe=r.get("universe") or ["ALL"], regime_filter=r.get("regime_filter") or [],
            conflict_class=r.get("conflict_class", "default"), capital_cap_pct=r.get("capital_cap_pct", 5.0),
            max_concurrent=r.get("max_concurrent", 10), enabled=bool(r.get("enabled", False)),
            extra=r.get("extra") or {},
        ))
    return out


def _open_positions():
    return rest("paper_trades", {"select": "*", "closed_at": "is.null"})


def _already_fired_recently(dedupe_key, ttl_minutes=DEFAULT_TTL_MINUTES):
    since = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=ttl_minutes)).isoformat()
    rows = rest("signals", [("select", "dedupe_key"), ("dedupe_key", f"eq.{dedupe_key}"),
                             ("fired_at", f"gte.{since}"), ("limit", "1")])
    return bool(rows)


def build_context():
    """
    Joins bands + latest edges (both sides) + latest band_probabilities +
    live_weather (Task 13d - fields default to "unknown" until that table
    has data, which correctly makes S5 never fire rather than guessing)
    into one BandView per band. Real I/O; not exercised by unit tests
    (those cover the pure detectors/run_strategies on fixtures instead).
    """
    cities = get_cities(require_coords=False)
    now = dt.datetime.now(dt.timezone.utc)
    since = (now-dt.timedelta(minutes=15)).isoformat()
    markets = rest_all("markets", [("select", "market_id,city_key,resolution_date,unit"),
        ("closed", "eq.false"), ("resolution_date", f"gte.{(now.date()-dt.timedelta(days=1)).isoformat()}")], order="market_id")
    markets = [m for m in markets if m.get('unit') in ('C','F')]
    unit_of = {m['market_id']:m['unit'] for m in markets}
    market_city = {m["market_id"]: m["city_key"] for m in markets}
    market_date = {m["market_id"]: m["resolution_date"] for m in markets}

    bands = []
    market_ids = list(market_city)
    for offset in range(0,len(market_ids),100):
        bands.extend(rest_all("bands", {"select":"*", "market_id":"in.("+','.join(market_ids[offset:offset+100])+")"}, order="band_id"))
    edges = rest_all("edges", [("select", "*"), ("computed_at", "gte."+since),
        ("computed_at", "lte."+now.isoformat())], order="band_id,side,computed_at.desc")
    latest_edge = {}
    for e in edges:
        key = (e["band_id"], e["side"])
        if key not in latest_edge:
            latest_edge[key] = e

    try:
        live_weather = {r["city_key"]: r for r in rest("live_weather", {"select": "*"})}
    except Exception:
        live_weather = {}

    # Which way today is pointing, and how much this city usually still climbs
    # from this hour (sql/ad4_26_temp_trend.sql). Absent, every field stays
    # None and S7 never fires - which is what a strategy should do when its
    # inputs do not exist, rather than assume a flat day.
    try:
        approach = {r["city_key"]: r for r in rest("v_city_peak_approach", {"select": "*"})}
    except Exception as e:
        approach = {}
        print(f"  ! v_city_peak_approach unavailable ({e}). "
              f"S7 cannot fire. Run sql/ad4_26_temp_trend.sql.")

    # Today's forecast maximum per city, shortest lead first - the same
    # ordering v_city_stats uses, and for the same reason: run_at alone
    # prefers a week-old seven-day-lead row over this morning's.
    forecast_max = {}
    try:
        for r in rest_all("weather_forecasts", [
            ("select", "*"), ("for_date", f"gte.{(now.date()-dt.timedelta(days=1)).isoformat()}"),
            ("run_at", "lte."+now.isoformat()),
        ], order="lead_days.asc,run_at.desc,forecast_id"):
            forecast_max.setdefault((r["city_key"],r["for_date"]), r)
    except Exception:
        pass

    history_cache = {}
    views = []
    for b in bands:
        city_key = market_city[b["market_id"]]
        resolution_date = market_date[b["market_id"]]
        yes = latest_edge.get((b["band_id"], "YES"), {})
        no = latest_edge.get((b["band_id"], "NO"), {})
        lw = live_weather.get(city_key, {})
        ap = approach.get(city_key, {})
        forecast = forecast_max.get((city_key,resolution_date),{})
        try:
            observed = dt.datetime.fromisoformat(lw.get('observed_at','').replace('Z','+00:00'))
            weather_current = lw.get('local_date') == resolution_date and 0 <= (now-observed).total_seconds() <= 10800
        except (ValueError,TypeError):
            weather_current = False
        if not weather_current:
            lw, ap = {}, {}
        reg = regime.classify(city_key, resolution_date, history_cache)

        views.append(BandView(
            band_id=b["band_id"], city_key=city_key, resolution_date=resolution_date,
            band_lo=b.get("band_lo"), band_hi=b.get("band_hi"), open_low=bool(b.get("open_low")),
            open_high=bool(b.get("open_high")), band_label=b.get("band_label") or "",
            unit=unit_of[b['market_id']], model_prob_yes=yes.get("model_prob"),
            yes_price=yes.get("market_price"), no_price=no.get("market_price"),
            yes_edge_net_pp=yes.get("edge_net_pp"), no_edge_net_pp=no.get("edge_net_pp"),
            yes_tradeable=bool(yes.get("tradeable")), yes_block_reason=yes.get("block_reason"),
            no_tradeable=bool(no.get("tradeable")), no_block_reason=no.get("block_reason"),
            confidence=yes.get("confidence") or reg.confidence, regime_label=yes.get("regime_label") or reg.label,
            market_state=yes.get("block_reason") or "LIVE",
            fillable_usd_5c_yes=yes.get("fillable_usd_5c") or 0.0, fillable_usd_5c_no=no.get("fillable_usd_5c") or 0.0,
            token_yes=b.get("token_yes"), token_no=b.get("token_no"),
            running_max_c=lw.get("running_max_c"), minutes_to_peak=lw.get("minutes_to_peak"),
            peak_window_state=lw.get("peak_window_state"), day_decided=bool(lw.get("day_decided")),
            window_width_h=reg.window_width_h, s5_allowed=reg.s5_allowed,
            slope_3_c_per_h=ap.get("slope_3_c_per_h"), slope_6_c_per_h=ap.get("slope_6_c_per_h"),
            trend_direction=ap.get("direction"), rolling_over=bool(ap.get("rolling_over")),
            latest_temp_c=ap.get("latest_temp_c"), reading_age_min=ap.get("reading_age_min"),
            typical_climb_left_c=ap.get("typical_climb_left_c"),
            implied_max_c=ap.get("implied_max_c"),
            implied_max_low_c=ap.get("implied_max_low_c"),
            implied_max_high_c=ap.get("implied_max_high_c"),
            pct_already_peaked=ap.get("pct_already_peaked"),
            forecast_max_c=forecast.get('forecast_max_c'),
            decision_evidence={'market':next(m for m in markets if m['market_id']==b['market_id']),
                'band':b,'yes_edge':yes,'no_edge':no,'forecast':forecast,'weather':lw,'approach':ap},
        ))

    settings = {r["key"]: r["value"] for r in rest("settings", {"select": "key,value"})}
    return Context(bands=views, settings=settings, now=dt.datetime.now(dt.timezone.utc))


def main():
    ctx = build_context()
    strategy_configs = _strategy_configs()
    open_positions = _open_positions()

    portfolio = paper_engine.Portfolio(bankroll=(ctx.settings.get("bankroll") or {}).get("amount") or 0.0,
                                        compounding=bool((ctx.settings.get("bankroll") or {}).get("compounding")))
    fired, blocked, conflict_rows = run_strategies(ctx, strategy_configs, open_positions, portfolio)

    ingest_log_rows = rest("ingest_log", [("select", "job,status,logged_at"), ("order", "job,logged_at.desc")])
    seen_job = set()
    latest_per_job = []
    for r in ingest_log_rows:
        if r["job"] not in seen_job:
            seen_job.add(r["job"])
            latest_per_job.append(r)
    fired.extend(system_health_signals(latest_per_job, ctx.now))

    try:
        unnotified = rest("anomalies", {"select": "*", "notified": "is.false"})
        fired.extend(anomaly_signals(unnotified))
    except Exception as e:
        print(f"  ! anomalies read failed: {e}")

    versions = {}  # forecast_version/calibration_version come from the band's own edge row, filled in per-signal below

    max_slippage = ((ctx.settings.get("max_slippage_cents") or {}).get("value", 5)) / 100.0
    # band_id -> city_key, so every signal row can name its city (see below).
    band_city = {str(b.band_id): b.city_key for b in ctx.bands if getattr(b, "band_id", None)}
    n_fired, n_deduped, n_filled = 0, 0, 0
    signal_rows, conflict_log_rows = [], conflict_rows
    cycle_id = str(uuid.uuid4())
    decision_bands = {str(b.band_id):b for b in ctx.bands}
    for sig in fired:
        if _already_fired_recently(sig.dedupe_key):
            n_deduped += 1
            continue
        n_fired += 1
        ids = (sig.payload or {}).get('band_ids') or [sig.band_id]
        sig.payload = {**(sig.payload or {}), 'cycle_id':cycle_id,'decision_at':ctx.now.isoformat(),
            'decision_inputs':{str(bid):asdict(decision_bands[str(bid)]) for bid in ids if str(bid) in decision_bands}}
        levels_by_side = {}
        if sig.action == "ENTER" and sig.band_id:
            try:
                # v_latest_book, NOT book_snapshots: the raw table's
                # bid_levels/ask_levels are integer level counts, so reading it
                # here would hand paper_engine an EMPTY ladder for every signal
                # and quietly size every fill at zero. The view exposes
                # normalised {"price","size"} ladders under the same names.
                book_rows = rest("v_latest_book", [("select", "*"), ("band_id", f"eq.{sig.band_id}"),
                                                    ("order", "observed_at.desc"), ("limit", "1")])
                book = book_rows[0] if book_rows else {}
                levels_by_side = {"YES": edge_engine.levels_for_side(book, "YES"),
                                   "NO": edge_engine.levels_for_side(book, "NO")}
            except Exception as e:
                print(f"  ! book fetch failed for {sig.band_id}: {e}")
        result = paper_engine.process_signal(sig, levels_by_side=levels_by_side, portfolio=portfolio,
                                              settings=ctx.settings, versions=versions, max_slippage=max_slippage)
        row = result["signal_row"]
        # signals.city_key is a real column and nothing has ever written it.
        # Without it a signal in the UI can name no city - which is exactly how
        # "system / critical / implausible_edge_anomaly" ended up on screen,
        # naming neither a place nor a trade. band_id resolves through the
        # band->market->city map this run already built.
        # Set unconditionally, not only when there is a band. Conditionally
        # ADDING the key gave band signals a city_key and band-less ones none,
        # so one batch carried two key shapes - and PostgREST refuses that with
        # PGRST102 "All object keys must match", which killed the whole write.
        # common.insert now groups by shape, so this is no longer load-bearing;
        # it is here because a uniform row is the right shape anyway, and
        # city_key has no column default, so an explicit null is exactly what
        # the absent key produced.
        row["city_key"] = row.get("city_key") or (
            band_city.get(str(row["band_id"])) if row.get("band_id") else None)
        signal_rows.append(row)
        if result["status"] == "filled":
            n_filled += 1
            insert("paper_trades", [result["trade_row"]])
            insert("ledger", result["ledger_rows"])

    if signal_rows:
        insert("signals", signal_rows)
    if conflict_log_rows:
        try:
            insert("strategy_conflicts", conflict_log_rows)
        except Exception as e:
            print(f"  ! strategy_conflicts insert failed: {e}")

    print(f"fired={n_fired} deduped={n_deduped} filled={n_filled} blocked_by_conflict={len(blocked)}")


if __name__ == "__main__":
    main()
