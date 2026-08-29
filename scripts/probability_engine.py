"""
AD4 probability engine (Task 4).

Deliberately simple, honestly uncertain: a bias-corrected normal
distribution over the forecast, discretised onto the whole-number lattice
Polymarket actually settles on. No EMOS, no gradient boosting, no isotonic
calibration - those need settled-outcome data to calibrate against, which
does not exist yet.

    centre            = latest forecast_max_c for (city, for_date) at the
                         shortest available lead time
    centre_corrected  = centre - bias_c              (station bias correction,
                                                        derived_forecast_skill)
    sigma             = mae_c * 1.2533 * regime.sigma_multiplier
    distribution      = Normal(centre_corrected, sigma), in Celsius always -
                         forecast_max_c is stored in Celsius regardless of
                         the city's settlement unit.
    lattice           = probability mass assigned to each REACHABLE INTEGER
                         in the city's settlement unit, not to a continuous
                         interval, then summed into bands. 27.4 and 27.6 are
                         the same weather and different money.
    open tails        = all remaining mass below/above the closed range.
    normalise         = the 11 bands sum to exactly 1.0.

Run standalone (`python probability_engine.py`) or via
.github/workflows/probabilities.yml, 4x/day aligned after model cycles.
"""
import datetime as dt
import math
import sys
from collections import defaultdict

from common import rest, insert, get_cities, log_run
import regime

MAE_TO_SIGMA = 1.2533          # sourced: sigma = MAE * sqrt(pi/2) for a normal distribution
CALIBRATION_VERSION = "v0_normal_lattice_no_calibration"
UNTRUSTED_N_DAYS = 200          # matches Task 1's "worst_sample should be 200+"
BIAS_EXCEEDS_MAE_RATIO = 0.95   # matches anomaly_rules.bias_exceeds_mae threshold
UNTRUSTED_CONFIDENCE_PENALTY = 0.5


# --------------------------------------------------------------------------
# Pure math - no network calls, independently unit-testable.
# --------------------------------------------------------------------------

def normal_cdf(x, mean, sigma):
    if sigma <= 0:
        return 1.0 if x >= mean else 0.0
    return 0.5 * (1.0 + math.erf((x - mean) / (sigma * math.sqrt(2.0))))


def unit_edge_c(unit, boundary):
    """
    The Celsius split-point between "settles as boundary-1" and "settles as
    boundary" for a city reporting whole degrees in `unit`. This is the
    whole-number lattice: a band expressed as [lo, hi) in the LOCAL unit
    does not translate to [lo, hi) in Celsius for F cities, because 1F is
    not 1C. Converting through the half-integer split point is what makes
    the lattice honest.
    """
    half_step_local = boundary - 0.5
    if unit == "F":
        return (half_step_local - 32.0) * 5.0 / 9.0
    return half_step_local


def band_mass(centre_c, sigma_c, unit, band_lo, band_hi, open_low, open_high):
    """Probability mass for one band under Normal(centre_c, sigma_c)."""
    if open_low:
        edge_hi = unit_edge_c(unit, band_hi)
        return normal_cdf(edge_hi, centre_c, sigma_c)
    if open_high:
        edge_lo = unit_edge_c(unit, band_lo)
        return 1.0 - normal_cdf(edge_lo, centre_c, sigma_c)
    edge_lo = unit_edge_c(unit, band_lo)
    edge_hi = unit_edge_c(unit, band_hi)
    return max(0.0, normal_cdf(edge_hi, centre_c, sigma_c) - normal_cdf(edge_lo, centre_c, sigma_c))


def compute_band_probabilities(centre_c, sigma_c, unit, bands):
    """
    bands: list of dicts with band_id, band_lo, band_hi, open_low, open_high.
    Returns list of (band_id, prob) with prob summing to exactly 1.0.
    """
    raw = [(b["band_id"], band_mass(centre_c, sigma_c, unit,
                                     b["band_lo"], b["band_hi"],
                                     bool(b.get("open_low")), bool(b.get("open_high"))))
           for b in bands]
    total = sum(p for _, p in raw)
    if total <= 0:
        n = len(raw)
        return [(bid, 1.0 / n) for bid, _ in raw]
    return [(bid, p / total) for bid, p in raw]


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def _upcoming_markets():
    return rest("markets", [
        ("select", "market_id,city_key,resolution_date"),
        ("resolution_date", f"gte.{dt.date.today().isoformat()}"),
    ])


def _bands_for_markets(market_ids):
    out = []
    chunk = 100
    for i in range(0, len(market_ids), chunk):
        batch = market_ids[i:i + chunk]
        rows = rest("bands", [
            ("select", "band_id,market_id,band_lo,band_hi,open_low,open_high,band_label"),
            ("market_id", f"in.({','.join(str(m) for m in batch)})"),
        ])
        out.extend(rows)
    return out


def _forecast_for(city_key, for_date):
    """Row at the shortest available lead time for this city+for_date."""
    rows = rest("weather_forecasts", [
        ("select", "for_date,lead_days,forecast_max_c,model,run_at"),
        ("city_key", f"eq.{city_key}"),
        ("for_date", f"eq.{for_date}"),
        ("order", "lead_days.asc"),
        ("limit", "1"),
    ])
    return rows[0] if rows else None


def _skill_for(city_key, lead_days):
    rows = rest("derived_forecast_skill", [
        ("select", "city_key,lead_days,n_days,mae_c,bias_c,computed_at"),
        ("city_key", f"eq.{city_key}"),
        ("lead_days", f"eq.{lead_days}"),
        ("order", "computed_at.desc"),
        ("limit", "1"),
    ])
    return rows[0] if rows else None


def process_city_day(city_key, for_date, unit, bands, history_cache):
    forecast = _forecast_for(city_key, for_date)
    if forecast is None or forecast.get("forecast_max_c") is None:
        print(f"  TODO: unmeasured - no forecast for {city_key} {for_date}", file=sys.stderr)
        return None
    lead_days = forecast["lead_days"]

    skill = _skill_for(city_key, lead_days)
    reg = regime.classify(city_key, for_date, history_cache)
    confidence = reg.confidence

    if skill is None:
        print(f"  TODO: unmeasured - no derived_forecast_skill for {city_key} lead={lead_days}", file=sys.stderr)
        bias_c, mae_c = 0.0, None
        confidence = min(confidence, 0.1)
        reasons = reg.reasons + ["no_skill_row"]
    else:
        bias_c = skill.get("bias_c") or 0.0
        mae_c = skill.get("mae_c")
        n_days = skill.get("n_days") or 0
        reasons = list(reg.reasons)
        if n_days < UNTRUSTED_N_DAYS:
            confidence *= UNTRUSTED_CONFIDENCE_PENALTY
            reasons.append(f"thin_sample:{n_days}d")
        if mae_c and mae_c > 0 and abs(bias_c) / mae_c >= BIAS_EXCEEDS_MAE_RATIO:
            confidence *= UNTRUSTED_CONFIDENCE_PENALTY
            reasons.append("bias_exceeds_mae")

    if mae_c is None:
        print(f"  TODO: unmeasured - no mae_c for {city_key} lead={lead_days}, skipping", file=sys.stderr)
        return None

    centre = forecast["forecast_max_c"]
    centre_corrected = centre - bias_c
    sigma = mae_c * MAE_TO_SIGMA * reg.sigma_multiplier

    probs = compute_band_probabilities(centre_corrected, sigma, unit, bands)
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    forecast_version = f"{forecast.get('model')}:{forecast.get('run_at')}"

    rows = [{
        "band_id": band_id, "computed_at": computed_at, "calibrated_prob": round(p, 6),
        "forecast_max_c": centre, "bias_applied_c": bias_c, "sigma_c": round(sigma, 4),
        "lead_days": lead_days, "lattice_applied": True,
        "confidence": round(confidence, 4), "regime_label": reg.label,
        "forecast_version": forecast_version, "calibration_version": CALIBRATION_VERSION,
    } for band_id, p in probs]
    return rows, reg, reasons


def main():
    cities = get_cities(require_coords=False)
    unit_of = {c["city_key"]: (c.get("unit") or "C") for c in cities}

    markets = _upcoming_markets()
    by_city = defaultdict(list)
    for m in markets:
        by_city[m["city_key"]].append(m)

    market_ids = [m["market_id"] for m in markets]
    bands = _bands_for_markets(market_ids)
    bands_by_market = defaultdict(list)
    for b in bands:
        bands_by_market[b["market_id"]].append(b)

    history_cache = {}
    all_rows = []
    sample_prints = []
    for city_key, city_markets in by_city.items():
        unit = unit_of.get(city_key, "C")
        for m in city_markets:
            band_rows = bands_by_market.get(m["market_id"], [])
            if not band_rows:
                continue
            result = process_city_day(city_key, m["resolution_date"], unit, band_rows, history_cache)
            if result is None:
                continue
            rows, reg, reasons = result
            all_rows.extend(rows)
            if len(sample_prints) < 3:
                sample_prints.append((city_key, m["resolution_date"], band_rows, rows, reg))

    if all_rows:
        insert("band_probabilities", all_rows)

    for city_key, for_date, band_rows, rows, reg in sample_prints:
        print(f"\n{city_key} {for_date}  regime={reg.label} confidence={reg.confidence:.2f}")
        by_band = {b["band_id"]: b for b in band_rows}
        for r in rows:
            b = by_band[r["band_id"]]
            label = b.get("band_label") or f"{b['band_lo']}-{b['band_hi']}"
            print(f"  {label:<14} p={r['calibrated_prob']:.4f}")
        total = sum(r["calibrated_prob"] for r in rows)
        print(f"  sum={total:.6f}")

    log_run("probability_engine", "ok", len(all_rows), {"city_days": len(sample_prints)})
    print(f"\nwrote {len(all_rows)} band_probabilities rows")


if __name__ == "__main__":
    main()
