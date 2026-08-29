"""
AD4 regime classifier (Task 5).

Produces, per city-day, a continuous `confidence` score (0-1) and a discrete
label on top (SHARP / NORMAL / UNCERTAIN / BLOCKED). Consumed by
probability_engine.py (widens sigma) and edge_engine.py / strategies (gates
entries and scales size).

Thresholds are PER-CITY PERCENTILES of that city's own history, not global
constants - Denver's calm day and Singapore's calm day are not the same
spread. This module recomputes those percentiles from the accumulating
archive on every call; nothing is frozen or cached across runs (per the
spec's REFERENCE/OPERATIONAL/DERIVED data-class rule).

HONEST LIMITATION, stated once rather than silently worked around:
`weather_forecasts.model` is currently always `open_meteo_best_match`
(see scripts/ingest_forecasts.py) - there is no second forecast source to
disagree with yet. Cross-model disagreement, the spec's *primary* regime
signal, cannot be measured today. Until a second model is ingested, this
module falls back to a measurable proxy - run-to-run forecast volatility
for the same `for_date` across the lead times observed so far - and labels
it as a proxy everywhere it is used, rather than presenting it as the real
thing. Swap `_disagreement_proxy` for true multi-model spread the day a
second source exists; nothing else in this module needs to change.

Precipitation and wind are similarly not yet columns on `weather_forecasts`
(they land in Task 13d's live_weather schema for *observations*, not
forecasts). Where that data isn't present, the corresponding signal is
simply left out of the score rather than defaulted to a guessed value.
"""
import datetime as dt
import statistics
import sys
from collections import defaultdict

from common import rest, get_cities

STALE_FORECAST_HOURS = 12          # matches anomaly_rules.stale_forecast
SEASONAL_WINDOW_GATE_HOURS = 12    # spec §0.4 / Task 5: "~12h" is given verbatim
MIN_HISTORY_DAYS = 20              # below this, per spec: confidence low, label UNCERTAIN
LOOKBACK_DAYS = 120

# PROVISIONAL - claude_invented, no evidential basis. UI/settings-settable
# once there is realised P&L to tune against. These translate the discrete
# label into (a) how much to widen the probability engine's sigma and
# (b) how much strategies should scale size by.
SIGMA_MULTIPLIER = {"SHARP": 1.00, "NORMAL": 1.15, "UNCERTAIN": 1.50, "BLOCKED": 2.00}
SIZE_MULTIPLIER = {"SHARP": 1.00, "NORMAL": 0.60, "UNCERTAIN": 0.25, "BLOCKED": 0.00}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _forecasts_for_date(city_key, for_date):
    """All forecast rows on record for this city+for_date, any lead/run."""
    return rest("weather_forecasts", [
        ("select", "for_date,lead_days,forecast_max_c,model,run_at"),
        ("city_key", f"eq.{city_key}"),
        ("for_date", f"eq.{for_date}"),
        ("order", "lead_days.asc"),
    ])


def _disagreement_proxy(rows):
    """
    PROXY for cross-model disagreement (see module docstring): the spread of
    forecast_max_c across the distinct lead times on record for this
    for_date. A forecast that has been swinging run to run as lead shortens
    is exactly as untrustworthy right now as one where models disagree -
    both mean "don't trust this number yet."

    Returns (spread_c, n_points, used_multi_model: bool).
    """
    models = {r["model"] for r in rows if r.get("model")}
    if len(models) > 1:
        vals = [r["forecast_max_c"] for r in rows if r.get("forecast_max_c") is not None]
        if len(vals) >= 2:
            return max(vals) - min(vals), len(vals), True
    # fall back to the proxy: spread across lead times
    by_lead = {}
    for r in rows:
        if r.get("forecast_max_c") is None:
            continue
        by_lead[r["lead_days"]] = r["forecast_max_c"]
    vals = list(by_lead.values())
    if len(vals) < 2:
        return None, len(vals), False
    return max(vals) - min(vals), len(vals), False


def _history_disagreement(city_key, before_date, lookback_days=LOOKBACK_DAYS):
    """
    This city's own recent history of the disagreement proxy, one value per
    past for_date, used to build the per-city percentile threshold.
    """
    start = (before_date - dt.timedelta(days=lookback_days)).isoformat()
    end = (before_date - dt.timedelta(days=1)).isoformat()
    rows = rest("weather_forecasts", [
        ("select", "for_date,lead_days,forecast_max_c,model"),
        ("city_key", f"eq.{city_key}"),
        ("for_date", f"gte.{start}"),
        ("for_date", f"lte.{end}"),
        ("order", "for_date.asc"),
    ])
    by_date = defaultdict(list)
    for r in rows:
        by_date[r["for_date"]].append(r)
    out = []
    for d, rs in by_date.items():
        spread, n, _ = _disagreement_proxy(rs)
        if spread is not None:
            out.append(spread)
    return out


def _percentile_rank(value, history):
    """Fraction of this city's own history that `value` is >= to. 0=calmest, 1=most volatile ever seen."""
    if not history:
        return None
    return sum(1 for h in history if h <= value) / len(history)


def _weather_peak(city_key, month):
    rows = rest("derived_weather_peak", [
        ("select", "*"),
        ("city_key", f"eq.{city_key}"),
        ("month", f"eq.{month}"),
        ("order", "computed_at.desc"),
        ("limit", "1"),
    ])
    return rows[0] if rows else None


def _latest_book_age_minutes(city_key):
    """Best-effort: age of the freshest book snapshot for any of this city's live bands. None if unavailable."""
    try:
        rows = rest("book_snapshots", [
            ("select", "observed_at,band_id"),
            ("order", "observed_at.desc"),
            ("limit", "1"),
        ])
    except Exception:
        return None
    if not rows or not rows[0].get("observed_at"):
        return None
    observed = dt.datetime.fromisoformat(rows[0]["observed_at"].replace("Z", "+00:00"))
    return (_now() - observed).total_seconds() / 60.0


class RegimeResult:
    def __init__(self, city_key, for_date, label, confidence, sigma_multiplier,
                 size_multiplier, s5_allowed, window_width_h, reasons, diagnostics):
        self.city_key = city_key
        self.for_date = for_date
        self.label = label
        self.confidence = confidence
        self.sigma_multiplier = sigma_multiplier
        self.size_multiplier = size_multiplier
        self.s5_allowed = s5_allowed
        self.window_width_h = window_width_h
        self.reasons = reasons
        self.diagnostics = diagnostics

    def as_dict(self):
        return {
            "city_key": self.city_key, "for_date": self.for_date,
            "regime_label": self.label, "confidence": self.confidence,
            "sigma_multiplier": self.sigma_multiplier, "size_multiplier": self.size_multiplier,
            "s5_allowed": self.s5_allowed, "window_width_h": self.window_width_h,
            "reasons": self.reasons,
        }


def classify(city_key, for_date, history_cache=None):
    """
    Classify one city-day. `for_date` is an ISO date string.
    `history_cache`: optional dict shared across calls in one run, keyed by
    city_key, to avoid re-pulling the same city's history for every date.
    """
    reasons = []
    diagnostics = {}
    for_date_obj = dt.date.fromisoformat(for_date)

    rows = _forecasts_for_date(city_key, for_date)
    if not rows:
        return RegimeResult(city_key, for_date, "BLOCKED", 0.0,
                             SIGMA_MULTIPLIER["BLOCKED"], SIZE_MULTIPLIER["BLOCKED"],
                             False, None, ["no_forecast_on_record"], {})

    shortest_lead_row = min(rows, key=lambda r: r["lead_days"])
    forecast_age_h = None
    if shortest_lead_row.get("run_at"):
        run_at = dt.datetime.fromisoformat(shortest_lead_row["run_at"].replace("Z", "+00:00"))
        forecast_age_h = (_now() - run_at).total_seconds() / 3600.0
        diagnostics["forecast_age_h"] = round(forecast_age_h, 2)
        if forecast_age_h > STALE_FORECAST_HOURS:
            reasons.append(f"stale_forecast:{forecast_age_h:.1f}h")
            return RegimeResult(city_key, for_date, "BLOCKED", 0.0,
                                 SIGMA_MULTIPLIER["BLOCKED"], SIZE_MULTIPLIER["BLOCKED"],
                                 False, None, reasons, diagnostics)

    spread, n_points, used_multi_model = _disagreement_proxy(rows)
    diagnostics["disagreement_c"] = spread
    diagnostics["disagreement_is_true_multi_model"] = used_multi_model
    if not used_multi_model:
        reasons.append("disagreement_is_proxy_not_multi_model")

    if history_cache is None:
        history_cache = {}
    if city_key not in history_cache:
        history_cache[city_key] = _history_disagreement(city_key, for_date_obj)
    history = history_cache[city_key]
    diagnostics["history_n"] = len(history)

    if len(history) < MIN_HISTORY_DAYS or spread is None:
        reasons.append(f"insufficient_history:{len(history)}")
        confidence = 0.2
        label = "UNCERTAIN"
    else:
        pct = _percentile_rank(spread, history)
        diagnostics["disagreement_percentile"] = round(pct, 3)
        confidence = max(0.0, min(1.0, 1.0 - pct))
        if confidence >= 0.66:
            label = "SHARP"
        elif confidence >= 0.33:
            label = "NORMAL"
        else:
            label = "UNCERTAIN"

    peak = _weather_peak(city_key, for_date_obj.month)
    window_width_h = peak.get("window_width_h") if peak else None
    diagnostics["window_width_h"] = window_width_h
    s5_allowed = window_width_h is not None and window_width_h <= SEASONAL_WINDOW_GATE_HOURS
    if window_width_h is None:
        reasons.append("no_measured_peak_window:s5_blocked_by_default")
    elif not s5_allowed:
        reasons.append(f"seasonal_window_too_wide_for_s5:{window_width_h}h")

    book_age_min = _latest_book_age_minutes(city_key)
    diagnostics["book_age_min"] = book_age_min
    if book_age_min is not None and book_age_min > 60:
        reasons.append(f"stale_book:{book_age_min:.0f}min")
        if label == "SHARP":
            label = "NORMAL"
        confidence = min(confidence, 0.5)

    return RegimeResult(city_key, for_date, label, round(confidence, 4),
                         SIGMA_MULTIPLIER[label], SIZE_MULTIPLIER[label],
                         s5_allowed, window_width_h, reasons, diagnostics)


def upcoming_city_dates(cities, days_forward=3):
    today = dt.date.today()
    for c in cities:
        for d in range(days_forward):
            yield c["city_key"], (today + dt.timedelta(days=d)).isoformat()


def main():
    cities = get_cities(require_coords=False)
    history_cache = {}
    print(f"{'CITY':<16}{'DATE':<12}{'LABEL':<10}{'conf':>6}{'s5':>4}{'window_h':>9}  reasons")
    for city_key, for_date in upcoming_city_dates(cities):
        try:
            result = classify(city_key, for_date, history_cache)
        except Exception as e:
            print(f"  ! {city_key} {for_date} failed: {e}", file=sys.stderr)
            continue
        print(f"{city_key[:15]:<16}{for_date:<12}{result.label:<10}{result.confidence:>6.2f}"
              f"{'Y' if result.s5_allowed else 'N':>4}"
              f"{(result.window_width_h if result.window_width_h is not None else -1):>9}"
              f"  {','.join(result.reasons)}")


if __name__ == "__main__":
    main()
