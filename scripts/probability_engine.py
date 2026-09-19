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
                        * calibration.sigma_multiplier (ad4_45: measured from
                          settled days - sd of (observed-forecast)/sigma is 1
                          if and only if the stated sigma was honest)
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
.github/workflows/pipeline_intraday.yml, 4x/day aligned after model cycles.
"""
import datetime as dt
import math
import sys
from collections import defaultdict

from common import rest, rest_all, insert, get_cities, log_run, model_version_id
import regime

MAE_TO_SIGMA = 1.2533          # sourced: sigma = MAE * sqrt(pi/2) for a normal distribution
CALIBRATION_VERSION = "v0_normal_lattice_no_calibration"
VERIFIED_EVIDENCE_SCOPE = "verified_outcomes_v1"
COLD_START_MAE_C = 4.0

# --------------------------------------------------------------------------
# Calibration.
#
# The lattice gives a probability from a normal centred on the forecast. That
# is a MODEL of how the day resolves, not a measurement of how often this desk
# is right, and the two differ in a way only the desk's own history can show.
# scripts/calibration.py fits a two-parameter Platt map on settled bands from
# fact_band_outcome and writes it to settings.calibration_map.
#
# Two guards, because a wrong calibration map is worse than none - it rescales
# every probability while looking exactly like a right one:
#
#   applies=false, written by the fitter when the correction did not improve
#   the Brier score on its own training data, is honoured here.
#
#   A map is only used when the fitter had enough evidence; below that it
#   writes nothing at all, and this reads nothing.
# --------------------------------------------------------------------------
_calibration = None

def _calibration_map():
    """{'a','b','applies'} or None. Read once per run."""
    global _calibration
    if _calibration is None:
        _calibration = {}
        try:
            rows = rest("settings", [("select", "value"), ("key", "eq.calibration_map")])
            v = rows[0]["value"] if rows else None
            if (isinstance(v, dict)
                    and v.get("applies")
                    and v.get("method") == "platt"
                    and v.get("evidence_scope") == VERIFIED_EVIDENCE_SCOPE):
                a, b = float(v["a"]), float(v["b"])
                _calibration = {"a": a, "b": b, "n": v.get("n"), "note": v.get("note")}
        except Exception as e:
            print(f"  note: no calibration map ({e})", file=sys.stderr)
    return _calibration or None


def _calibrate(p):
    """Apply the fitted map to one probability. Identity when none is fitted."""
    m = _calibration_map()
    if not m:
        return p
    q = min(max(p, 1e-6), 1 - 1e-6)
    z = m["a"] * math.log(q / (1 - q)) + m["b"]
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))
UNTRUSTED_N_DAYS = 200          # matches Task 1's "worst_sample should be 200+"
BIAS_EXCEEDS_MAE_RATIO = 0.95   # matches anomaly_rules.bias_exceeds_mae threshold
UNTRUSTED_CONFIDENCE_PENALTY = 0.5
# Measured on this city and lead, but the outcomes behind it are not yet
# corroborated by weather_resolution_evidence. Halves confidence and blocks
# the ticket; it does NOT throw the measurement away. See _newest_measured.
UNVERIFIED_CONFIDENCE_PENALTY = 0.5
GLOBAL_SKILL_CONFIDENCE_PENALTY = 0.25


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


# A DAILY MAXIMUM CANNOT GO DOWN.
#
# The lattice prices every band off a forecast and a width, and then ignores
# the fact that the day is half over and the thermometer has already been
# somewhere. On 16 Sep at 16:00 UTC, across the 297 bands resolving that day in
# 27 cities, 28 were already PHYSICALLY IMPOSSIBLE - their top was below the
# temperature their own city had already recorded - and twelve of those were
# still priced above 2%, the worst at 23.7%. Nearly a full unit of probability
# mass sat on outcomes that could not happen, and the desk would have bought
# them at a discount it had invented.
#
# This is not a model improvement. It is arithmetic: the maximum of a set does
# not decrease when you add to it. Of everything the platform collects, it is
# the cheapest and the most certain, and it was not being used.
#
# THE TOLERANCE IS THE WHOLE RISK. Our running max comes from the station
# live_weather tracks; the venue settles on ITS chosen source, and the two can
# disagree by a few tenths. Killing a band on a 0.1C edge would eventually zero
# a band that settles right where we said it could not. So the floor is
# discounted by OBSERVED_FLOOR_TOLERANCE_C before it is allowed to zero
# anything: a band dies only when the observed maximum has cleared its top edge
# by more than half a degree Celsius, which is under one degree Fahrenheit.
OBSERVED_FLOOR_TOLERANCE_C = 0.5


def band_is_impossible(floor_c, unit, band_lo, band_hi, open_low, open_high,
                       tolerance_c=OBSERVED_FLOOR_TOLERANCE_C):
    """True when today's already-observed maximum settles ABOVE this band.

    A closed band [lo, hi) covers the integers lo..hi-1, so its upper split
    point in Celsius is unit_edge_c(unit, band_hi) - the boundary between
    settling as hi-1 and settling as hi. An observed maximum at or above that
    point has already settled the day outside this band.

    An open-high band is never impossible: there is no temperature the day can
    reach that puts it out of range.
    """
    if floor_c is None or open_high:
        return False
    return (floor_c - tolerance_c) >= unit_edge_c(unit, band_hi)


def compute_band_probabilities(centre_c, sigma_c, unit, bands, floor_c=None):
    """
    bands: list of dicts with band_id, band_lo, band_hi, open_low, open_high.
    floor_c: the maximum already observed today, in Celsius, or None when the
             market does not resolve today or no observation exists.
    Returns list of (band_id, prob) with prob summing to exactly 1.0.
    """
    raw = [(b["band_id"], band_mass(centre_c, sigma_c, unit,
                                     b["band_lo"], b["band_hi"],
                                     bool(b.get("open_low")), bool(b.get("open_high"))))
           for b in bands]

    if floor_c is not None:
        kept = [(bid, 0.0 if band_is_impossible(
                     floor_c, unit, b["band_lo"], b["band_hi"],
                     bool(b.get("open_low")), bool(b.get("open_high"))) else p)
                for (bid, p), b in zip(raw, bands)]
        # IF THE FLOOR KILLS EVERYTHING, DO NOT APPLY IT. A day that has already
        # run past the entire ladder means the ladder is wrong, the station is
        # wrong, or the city is mismatched - and none of those are improved by
        # publishing a uniform distribution over impossibilities. Fall back to
        # the unfloored lattice, which at least states the forecast's opinion.
        if sum(p for _, p in kept) > 0:
            raw = kept

    total = sum(p for _, p in raw)
    if total <= 0:
        n = len(raw)
        return [(bid, 1.0 / n) for bid, _ in raw]
    return [(bid, p / total) for bid, p in raw]


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------

def _observed_floors():
    """{city_key: (local_date, running_max_c)} - today's maximum so far.

    Read from v_city_running_max rather than live_weather directly, because
    that view carries the invariant this number has to satisfy: A MAXIMUM IS
    NEVER BELOW THE LATEST READING OF THE SAME DAY. live_weather.running_max_c
    was, for 14 of 54 cities on 2026-09-19, BELOW the current temperature - by
    up to 12.5 C - and 25 cities had no maximum at all, because the running
    maximum was built only from the observation archive and that archive runs
    about a day behind for 37 cities. A floor that is too low leaves
    probability on buckets the day has already passed.

    The view takes the greatest of today's observation series, the stored
    maximum and the live thermometer - each one gated to the city's own LOCAL
    date, which is the only date a daily high market resolves against. A UTC
    date would put half the world's cities on the wrong day; so would a live
    reading from 23:00 yesterday, which is what Shanghai has at 01:00.

    A FLOOR MAY REST ON A SINGLE READING. That is the difference between this
    caller and s5: "the day has already reached at least X" is true of one
    reading, while "the day's maximum is X" needs a series. So
    running_max_basis is not filtered here - only 'absent', which arrives as a
    null maximum and is skipped.

    One request for all 54 cities. A city with no row, or a null maximum, is
    simply absent, and every caller treats absence as "no floor" rather than
    as zero - which would make every band impossible.
    """
    floors = {}
    for row in rest("v_city_running_max",
                    {"select": "city_key,local_date,running_max_c,running_max_basis"}):
        if row.get("running_max_c") is None or not row.get("local_date"):
            continue
        floors[row["city_key"]] = (str(row["local_date"]), float(row["running_max_c"]))
    return floors


def _upcoming_markets():
    return rest("v_canonical_markets", [
        ("select", "market_id,city_key,resolution_date,unit,correction_id"),
        ("resolution_date", f"gte.{dt.date.today().isoformat()}"),
    ])


def _bands_for_markets(market_ids):
    out = []
    # A normal market has 11 bands. One 100-market request therefore returns
    # about 1,100 rows, beyond PostgREST's usual 1,000-row response cap. The
    # old unpaginated read silently clipped the result and produced partial
    # 8/11, 9/11 and 10/11 probability ladders. Page each bounded chunk with
    # a stable unique order so every canonical band is priced exactly once.
    chunk = 100
    for i in range(0, len(market_ids), chunk):
        batch = market_ids[i:i + chunk]
        rows = rest_all("v_canonical_bands", [
            ("select", "band_id,market_id,band_lo,band_hi,open_low,open_high,band_label,correction_id"),
            ("market_id", f"in.({','.join(str(m) for m in batch)})"),
        ], order="band_id", page_size=500)
        out.extend(rows)
    return out


def _forecast_for(city_key, for_date, as_of=None):
    """The shortest-lead forecast for this city+date, from its NEWEST run.

    A forecast's identity is (city, model, run_at, for_date) - that is
    weather_forecasts' own unique key. So several rows sharing a lead is the
    normal state of this table, not a fault: the intraday job re-fetches the
    same date every few hours and each fetch is a row.

    This used to order by lead_days alone and take the first row, which left
    the choice among those rows to whatever order PostgREST happened to
    return. It was not academic. Denver for 2026-09-13, three runs at lead 7:

        run 15:21   11.2 C   (max_at_local 00:00 - a truncated forecast day)
        run 21:00   30.3 C
        run 03:00   26.2 C

    a 19.1 C spread, any of which could be priced. Ordering by run_at desc
    settles it and retires the 11.2 without needing a rule about midnight
    maxima, because it is simply the oldest of the three.

    `as_of` restricts the pick to runs ISSUED BY a given moment. Live pricing
    leaves it None and gets the newest run there is; a backtest passes the
    decision timestamp so it cannot price against a forecast that did not
    exist yet.
    """
    params = [
        ("select", "for_date,lead_days,forecast_max_c,model,run_at"),
        ("city_key", f"eq.{city_key}"),
        ("for_date", f"eq.{for_date}"),
        ("order", "lead_days.asc,run_at.desc"),
        ("limit", "1"),
    ]
    if as_of is not None:
        params.insert(3, ("run_at", f"lte.{as_of}"))
    rows = rest("weather_forecasts", params)
    return rows[0] if rows else None


# --------------------------------------------------------------------------
# Forecast divergence.
#
# Every other input to sigma is HISTORICAL: mae_c is measured skill over past
# days, and the regime multiplier is a classification of the recent past. None
# of it says how uncertain TODAY is. When two independent models disagree
# about the same date, that disagreement is live evidence - and it is the only
# such evidence AD4 has.
#
# The multiplier is clamped at a floor of 1.0 in v_forecast_divergence, so a
# second source can only ever make AD4 LESS confident than its own measured
# skill says, never more. With one model, or with divergence disabled, it is
# exactly 1.0 and behaviour is unchanged.
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Calibration feedback - the loop that was left open.
#
# mae_c makes the CENTRE right and sets a starting width. Nothing measured
# whether the resulting distribution turned out HONEST: a model can have
# excellent mae_c and still be systematically overconfident, because the
# centre is right and the spread is too narrow. Every edge computed from a
# too-narrow distribution is overstated, so the desk sizes up on exactly the
# trades it should be sizing down.
#
# sql/ad4_45 measures it from settled days - sd of (observed - forecast)/sigma
# is 1 if and only if sigma was right - and stores one multiplier per city,
# already guarded: 1.0 under 30 days, 1.0 inside a 0.9-1.1 noise band, never
# narrowing on under 60 days, clamped to [0.75, 2.5].
#
# Read once per run. Absent table, absent row, or an unapplied row all mean
# exactly 1.0, which is the behaviour before this existed.
# --------------------------------------------------------------------------
_calibration_cache = None


def _calibration_for(city_key):
    """(multiplier, row) for a city. 1.0 and None when there is nothing to apply."""
    global _calibration_cache
    if _calibration_cache is None:
        _calibration_cache = {}
        try:
            for r in rest("derived_calibration_adjustment", [
                    ("select", "city_key,sigma_multiplier,applied,n_days,z_sd,reason,evidence_scope"),
                    ("evidence_scope", f"eq.{VERIFIED_EVIDENCE_SCOPE}"),
            ]):
                _calibration_cache[r["city_key"]] = r
        except Exception as e:
            # Not installed is not a failure: it is a desk that has not run
            # ad4_45 yet, and the engine priced fine before it existed.
            print(f"  note: calibration feedback unavailable ({str(e)[:80]}) - "
                  f"sigma is measured skill alone. Run sql/ad4_45_calibration_feedback.sql.",
                  file=sys.stderr)
    row = _calibration_cache.get(city_key)
    if (not row or not row.get("applied")
            or row.get("evidence_scope") != VERIFIED_EVIDENCE_SCOPE):
        return 1.0, None
    try:
        m = float(row.get("sigma_multiplier") or 1.0)
    except (TypeError, ValueError):
        return 1.0, None
    return (m, row) if m > 0 else (1.0, None)


_divergence_cache = None

# The view carries one row per (city, date) that has ever had a forecast -
# tens of thousands of rows on a database with any history. This engine only
# ever prices resolution_date >= today (see _upcoming_markets), so asking for
# the rest is not just waste: rest() is a single unpaginated GET, and if
# PostgREST caps the response the cache comes back PARTIAL. Every city past
# the cap would then silently price at multiplier 1.0 - the failure would look
# exactly like "the models agree", which is the one thing it must never be
# confused with. Bound the query by date, and make a truncated page loud.
_DIVERGENCE_LIMIT = 5000

def _divergence():
    """(city_key, for_date) -> divergence row, read once per run."""
    global _divergence_cache
    if _divergence_cache is None:
        _divergence_cache = {}
        try:
            rows = rest_all("v_forecast_divergence", [
                ("select", "city_key,for_date,n_models,models,spread_c,sigma_multiplier"),
                ("for_date", f"gte.{dt.date.today().isoformat()}"),
            ], order="city_key.asc,for_date.asc", page_size=1000)
            for r in rows:
                _divergence_cache[(r["city_key"], str(r["for_date"]))] = r
        except Exception as e:
            # A database without ad4_16 has no such view. Carry on with the
            # historical sigma rather than refuse to price anything.
            print(f"  note: no forecast divergence available ({e})", file=sys.stderr)
    return _divergence_cache


def _divergence_for(city_key, for_date):
    row = _divergence().get((city_key, str(for_date)))
    if not row:
        return 1.0, None
    try:
        mult = float(row.get("sigma_multiplier") or 1.0)
    except (TypeError, ValueError):
        return 1.0, None
    return max(1.0, mult), row


_skill_cache = {}
_global_skill_cache = {}


def _global_skill(lead_days):
    """Conservative cross-city cold-start width for an unmeasured city.

    This is not evidence that the new city itself is calibrated. It exists so
    the UI can show a complete, explicitly low-confidence probability ladder
    while the edge layer still blocks trading it. The 75th percentile of the
    newest positive city MAEs at the same lead is deliberately wider than the
    median and bias is always zero: another city's bias is not this city's
    correction.
    """
    if lead_days in _global_skill_cache:
        return _global_skill_cache[lead_days]
    try:
        # Verified rows if any exist, otherwise every measured row at this
        # lead. Same reasoning as _newest_measured: this path already refuses
        # to trade (`no_city_skill`), so the only question it decides is the
        # WIDTH, and a measured p75 across cities beats a constant.
        base = [
            ("select", "city_key,lead_days,n_days,mae_c,computed_at,evidence_scope"),
            ("lead_days", f"eq.{lead_days}"),
        ]
        rows = rest_all("derived_forecast_skill",
                        base + [("evidence_scope", f"eq.{VERIFIED_EVIDENCE_SCOPE}")],
                        order="city_key,computed_at.desc", page_size=500)
        if not rows:
            rows = rest_all("derived_forecast_skill", base,
                            order="city_key,computed_at.desc", page_size=500)
    except Exception as e:
        print(f"  note: global skill fallback unavailable ({e})", file=sys.stderr)
        rows = []

    latest = {}
    for row in rows:
        latest.setdefault(row.get("city_key"), row)
    maes = []
    for row in latest.values():
        try:
            value = float(row.get("mae_c"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(value) and value > 0:
            maes.append(value)
    maes.sort()
    if len(maes) < 5:
        result = None
    else:
        index = max(0, math.ceil(0.75 * len(maes)) - 1)
        result = {
            "lead_days": lead_days,
            "n_days": 0,
            "mae_c": maes[index],
            "bias_c": 0.0,
            "global_n_cities": len(maes),
        }
    _global_skill_cache[lead_days] = result
    return result

def _newest_measured(table, base_filters):
    """The newest VERIFIED skill row; failing that, the newest measured row of
    any scope, tagged `verified: False`.

    WHY THE SECOND HALF EXISTS. Phase 2A stamps `evidence_scope` on skill it
    can corroborate against `weather_resolution_evidence`. That table has zero
    rows and its capture has never run, so `evidence_scope = verified_outcomes_v1`
    matched NOTHING - and because `null = anything` is never true in SQL, all
    5,187 measured rows (50 cities, leads 1-7) were discarded silently in
    favour of COLD_START_MAE_C.

    The desk was therefore pricing 48 cities at sigma 5.0-15.0 C while holding
    a measured mean absolute error of 1.47 C at lead 1. London today: forecast
    24.2 C, sigma 5.013, every closed bucket near 8% and 32% of the mass piled
    into "27 C or higher" - against a market holding 46c and 45c on 24 and 25.
    That is not caution, it is a fabricated number outvoting a measured one.

    Corroboration decides whether a price may be TRADED. It was never meant to
    decide how WIDE the distribution is. So it travels as a column here -
    exactly as ad4_62 carries `verified` on the scorecard views instead of
    filtering by it - and the caller below blocks trading on `verified: False`
    while still pricing with the number the desk actually measured. When the
    evidence capture runs, these rows gain their scope and the block lifts on
    its own, with nothing to change here.
    """
    tail = [("order", "computed_at.desc"), ("limit", "1")]
    try:
        rows = rest(table, base_filters
                    + [("evidence_scope", f"eq.{VERIFIED_EVIDENCE_SCOPE}")] + tail)
        if rows:
            return {**rows[0], "verified": True}
        rows = rest(table, base_filters + tail)
        if rows:
            return {**rows[0], "verified": False}
    except Exception:
        # Table not installed yet. Same answer as before it existed: nothing.
        return None
    return None


def _pooled_skill(city_key, lead_days):
    key = (city_key, lead_days)
    if key not in _skill_cache:
        _skill_cache[key] = _newest_measured("derived_forecast_skill", [
            ("select", "city_key,lead_days,n_days,mae_c,bias_c,computed_at,evidence_scope"),
            ("city_key", f"eq.{city_key}"),
            ("lead_days", f"eq.{lead_days}"),
        ])
    return _skill_cache[key]


def _model_skill(city_key, model, lead_days):
    """Skill of ONE model, or None if it has not been scored at this lead."""
    key = (city_key, model, lead_days)
    if key not in _skill_cache:
        _skill_cache[key] = _newest_measured("derived_forecast_skill_model", [
            ("select", "city_key,model,lead_days,n_days,mae_c,bias_c,computed_at,evidence_scope"),
            ("city_key", f"eq.{city_key}"),
            ("model", f"eq.{model}"),
            ("lead_days", f"eq.{lead_days}"),
        ])
    return _skill_cache[key]


def _skill_for(city_key, lead_days, model=None):
    """Measured skill for the forecast being priced.

    THE ENSEMBLE RULE, stated once and in one place:

      Use the skill of the model that actually produced this forecast, but
      only once that model has earned a trusted sample of its own
      (UNTRUSTED_N_DAYS). Until then, use the pooled number.

    Skill is generated per model - NWS and Open-Meteo miss in different
    directions - so the per-model figure is the correct grain and the pooled
    one is an average over whichever models happened to write rows. But the
    correct grain is worthless without history behind it, and on this desk
    only open_meteo_best_match has any: nws and open_meteo_forecast both
    start 2026-09-06. Preferring a per-model row unconditionally would hand
    the engine a row with a handful of days behind it, or no row at all, and
    `no_skill_row` floors confidence at 0.1 - which would take the twelve NWS
    cities dark to fix a problem they do not yet have.

    So the fallback is not a hedge, it is the rule: the blend is what the
    desk prices with until a model has proved itself separately, and then
    the switch happens on its own, per city and per lead, with no migration.
    """
    if model:
        m = _model_skill(city_key, model, lead_days)
        if m and (m.get("n_days") or 0) >= UNTRUSTED_N_DAYS:
            return m
    return _pooled_skill(city_key, lead_days)


def process_city_day(city_key, for_date, unit, bands, history_cache, floors=None):
    forecast = _forecast_for(city_key, for_date)
    if forecast is None or forecast.get("forecast_max_c") is None:
        print(f"  TODO: unmeasured - no forecast for {city_key} {for_date}", file=sys.stderr)
        return None
    lead_days = forecast["lead_days"]

    skill = _skill_for(city_key, lead_days, forecast.get("model"))
    skill_lead_days = lead_days
    skill_proxy = False
    skill_source = "city_lead"
    pricing_eligible = True
    pricing_block_reason = None

    # The skill archive is deliberately measured at leads 1-7.  Same-day
    # forecasts have lead=0, and the old code therefore skipped every current
    # market even when a well-measured lead-1 error distribution existed.  Use
    # the nearest longer-horizon skill as a conservative width proxy while
    # retaining the fresh lead-0 forecast as the centre.  Do not borrow its
    # bias: lead-specific bias can change sign, so applying it would be an
    # unsupported correction.  The row records the proxy and confidence is
    # penalised until genuine lead-0 skill exists.
    if skill is None or skill.get("mae_c") is None:
        for candidate_lead in range(int(lead_days) + 1, 8):
            candidate = _skill_for(city_key, candidate_lead, forecast.get("model"))
            if candidate is not None and candidate.get("mae_c") is not None:
                skill = candidate
                skill_lead_days = candidate_lead
                skill_proxy = True
                skill_source = "city_lead_proxy"
                break
    # A city with no local outcome history should not disappear from the
    # probability surface: that made incomplete data look like a broken run.
    # Borrow only a conservative cross-city WIDTH and mark the result
    # ineligible for trading. Local evidence automatically replaces it later.
    if skill is None or skill.get("mae_c") is None:
        for candidate_lead in range(int(lead_days), 8):
            candidate = _global_skill(candidate_lead)
            if candidate is not None:
                skill = candidate
                skill_lead_days = candidate_lead
                skill_proxy = True
                skill_source = "global_lead_p75"
                pricing_eligible = False
                pricing_block_reason = "no_city_skill"
                break
    # A fresh installation has no verified outcome history at all. It should
    # still render a complete probability surface, but a historical error
    # estimate must not be invented from quarantined legacy facts. Use an
    # explicitly conservative fixed width and make the result ineligible for
    # trading until independent outcome evidence can replace it.
    if skill is None or skill.get("mae_c") is None:
        skill = {
            "lead_days": lead_days,
            "n_days": 0,
            "mae_c": COLD_START_MAE_C,
            "bias_c": 0.0,
        }
        skill_lead_days = lead_days
        skill_proxy = True
        skill_source = "fixed_cold_start"
        pricing_eligible = False
        pricing_block_reason = "no_verified_skill"

    # MEASURED BUT NOT CORROBORATED: price with it, refuse to trade on it.
    #
    # This is the whole of Phase 2A's guarantee and none of its collateral
    # damage. A width the desk has actually measured is strictly better
    # information than COLD_START_MAE_C, which is a constant somebody chose;
    # but until weather_resolution_evidence can corroborate the outcomes that
    # width was measured against, no order may rest on it. So the number is
    # used and the ticket stays blocked - the same verdict the desk reached
    # before, arrived at with the real number instead of an invented one.
    skill_verified = skill.get("verified")
    if skill_verified is False and pricing_eligible:
        pricing_eligible = False
        pricing_block_reason = "no_verified_skill"

    reg = regime.classify(city_key, for_date, history_cache)
    confidence = reg.confidence

    # Widening on an uncorroborated measurement is safe; MOVING THE CENTRE on
    # one is not. If the outcomes behind it are wrong, a bias correction walks
    # the forecast in a direction nobody can check, while an over-wide sigma
    # only ever understates the edge. Same asymmetry the proxies get.
    bias_c = 0.0 if (skill_proxy or skill_verified is False) else (skill.get("bias_c") or 0.0)
    mae_c = skill.get("mae_c")
    n_days = skill.get("n_days") or 0
    reasons = list(reg.reasons)
    if skill_proxy:
        if skill_source == "global_lead_p75":
            confidence *= GLOBAL_SKILL_CONFIDENCE_PENALTY
            reasons.append(
                f"global_skill_proxy:lead{lead_days}_to_lead{skill_lead_days}:"
                f"{skill.get('global_n_cities')}cities")
        elif skill_source == "fixed_cold_start":
            confidence = min(confidence, 0.1)
            reasons.append(f"fixed_cold_start:{COLD_START_MAE_C:.1f}C_mae:no_verified_skill")
        else:
            confidence *= UNTRUSTED_CONFIDENCE_PENALTY
            reasons.append(f"skill_proxy:lead{lead_days}_to_lead{skill_lead_days}")
    if skill_verified is False:
        confidence *= UNVERIFIED_CONFIDENCE_PENALTY
        reasons.append(f"measured_unverified:mae{mae_c:.2f}C_n{n_days}:awaiting_evidence_capture")
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

    div_mult, div_row = _divergence_for(city_key, for_date)
    cal_mult, cal_row = _calibration_for(city_key)
    # A MULTIPLIER SUBSTITUTES FOR A MEASUREMENT. IT DOES NOT SUPPLEMENT ONE.
    #
    # sigma was mae_c * 1.2533 * regime * calibration * divergence, where the
    # regime factor reaches 2.00 (BLOCKED) and divergence reaches 2.00. On a
    # cold start that is right: mae_c is then COLD_START_MAE_C, a constant
    # nobody measured, and the multipliers are the only information there is.
    #
    # Applied to a MEASURED mae_c they count the same uncertainty twice.
    # mae_c is the mean |forecast - actual| over this city's own settled days,
    # and those days already contain the uncertain regimes and the days the
    # models disagreed. Widening a measurement because conditions are uncertain
    # asserts that the measurement was taken in some other, calmer world.
    #
    # Measured on the live board, today, all on this city's own skill:
    #
    #   milan     mae 0.75 -> 0.94 sigma, published 2.91   (3.10x)
    #   seattle   mae 2.39 -> 2.99 sigma, published 8.97   (3.00x)
    #   nyc       mae 1.28 -> 1.61 sigma, published 4.22   (2.63x)
    #   jeddah    mae 3.36 -> 4.20 sigma, published 10.09  (2.40x)
    #
    # That is what put nyc's modal bucket at "65F or below" on a 23.2C (73.8F)
    # forecast, and chicago's at "82F or higher" on 23.7C (74.7F). The tell was
    # that TOMORROW read correctly everywhere - one model quoted, no divergence
    # factor, multiplier 1.00 - while today, the better-informed day, did not.
    #
    # Calibration still applies, always: ad4_45 fits it FROM settled outcomes
    # by asking whether the stated sigma matched reality, so it corrects the
    # measurement rather than second-guessing it, and it is self-limiting.
    #
    # The today-specific signal is not discarded, it is moved to where it
    # belongs. "The models disagree unusually today" is a reason to TRUST the
    # number less, not to publish a different one, so it keeps its effect on
    # confidence below and loses its effect on the distribution.
    width_is_measured = skill_source in ("city_lead", "city_lead_proxy")
    regime_sigma_mult = 1.0 if width_is_measured else reg.sigma_multiplier
    div_sigma_mult = 1.0 if width_is_measured else div_mult
    sigma_historical = mae_c * MAE_TO_SIGMA * regime_sigma_mult
    sigma = sigma_historical * cal_mult * div_sigma_mult
    if width_is_measured and (reg.sigma_multiplier > 1.0 or div_mult > 1.0):
        reasons.append(
            f"measured_width_kept:regime{reg.sigma_multiplier:.2f}x_"
            f"div{div_mult:.2f}x_not_applied_to_sigma")
    if cal_row is not None:
        # Named in the reasons so a price that moved can be traced to the
        # recompute that moved it, rather than looking like drift.
        reasons.append(
            f"calibration:{cal_mult:.2f}x_from_{cal_row.get('n_days')}d")
        if cal_mult > 1.0:
            # A distribution that had to be widened is one the desk was
            # overconfident about. Saying so in confidence is the point.
            confidence *= min(1.0, 1.0 / cal_mult)
    if div_row and div_mult > 1.0:
        reasons.append(
            f"models_disagree:{div_row.get('spread_c')}C_over_{div_row.get('n_models')}")
        confidence *= min(1.0, 1.0 / div_mult)

    # THE DAY SO FAR, when this market resolves today. A maximum cannot go
    # down, so anything the city has already recorded is a hard floor under the
    # outcome - the one input here that is measured rather than predicted.
    observed_floor_c = None
    local_date, running_max = (floors or {}).get(city_key, (None, None))
    if local_date is not None and str(for_date) == local_date:
        observed_floor_c = running_max

    probs = compute_band_probabilities(centre_corrected, sigma, unit, bands,
                                       floor_c=observed_floor_c)
    floored = [b["band_id"] for b in bands
               if observed_floor_c is not None and band_is_impossible(
                   observed_floor_c, unit, b["band_lo"], b["band_hi"],
                   bool(b.get("open_low")), bool(b.get("open_high")))]
    if floored and len(floored) < len(bands):
        print(f"  {city_key} {for_date}: already {observed_floor_c:.1f}C today - "
              f"{len(floored)} of {len(bands)} bands are out of reach")
    computed_at = dt.datetime.now(dt.timezone.utc).isoformat()

    # forecast_version / calibration_version are uuid columns referencing
    # model_versions - not free text. Resolve the readable labels to their
    # ids (registering them on first use). See common.model_version_id.
    forecast_label = f"{forecast.get('model')}:{forecast.get('run_at')}"
    forecast_version = model_version_id(
        "forecast", forecast_label,
        config={"model": forecast.get("model"), "run_at": forecast.get("run_at")},
        structural=False)
    _cal = _calibration_map()
    calibration_version = model_version_id(
        "calibration",
        f"platt:a={_cal['a']:.4f}:b={_cal['b']:+.4f}" if _cal else CALIBRATION_VERSION,
        config=_cal or {"note": "no calibration applied; normal lattice only"},
        structural=True)

    row = {
        "computed_at": computed_at,
        "input_forecast_run": forecast.get("run_at"),
        "forecast_max_c": centre, "bias_applied_c": bias_c, "sigma_c": round(sigma, 4),
        "lead_days": lead_days, "skill_lead_days": skill_lead_days,
        "skill_proxy": skill_proxy, "lattice_applied": True,
        "skill_source": skill_source, "pricing_eligible": pricing_eligible,
        "pricing_block_reason": pricing_block_reason,
        "confidence": round(confidence, 4), "regime_label": reg.label,
        # Why this band is zero. Without it, a floored price is indistinguishable
        # from a forecast that simply never reached here, and the calibration
        # fitter would learn from an outcome arithmetic had already decided.
        "observed_floor_c": observed_floor_c,
    }
    # A widened sigma must be explainable after the fact, not mysterious.
    if div_row and div_mult > 1.0:
        print(f"  {city_key} {for_date}: sigma {sigma_historical:.3f} -> {sigma:.3f} "
              f"({div_row.get('models')} differ by {div_row.get('spread_c')}C)")
    # Omit rather than send null: a database where model_versions is absent
    # should still get its probabilities written.
    if forecast_version:
        row["forecast_version"] = forecast_version
    if calibration_version:
        row["calibration_version"] = calibration_version

    # raw_prob is what the lattice said; calibrated_prob is what the desk's own
    # record says that number has been worth. Both are stored: the fitter needs
    # the raw one to keep learning, and unpicking a calibration after the fact
    # is impossible if only the corrected number survives.
    cal = _calibration_map()
    out = []
    for band_id, p in probs:
        r = dict(row, band_id=band_id, raw_prob=round(p, 6))
        r["calibrated_prob"] = round(_calibrate(p), 6) if cal else round(p, 6)
        out.append(r)

    # Calibration breaks the lattice's guarantee that the bands sum to 1, since
    # each is mapped independently. Renormalise: exactly one band resolves yes,
    # so the probabilities must still sum to one or every downstream figure -
    # edge, EV, Kelly size - is built on a distribution that is not one.
    if cal:
        total = sum(r["calibrated_prob"] for r in out)
        if total > 0:
            for r in out:
                r["calibrated_prob"] = round(r["calibrated_prob"] / total, 6)
        reasons.append(f"calibrated:platt(a={cal['a']:.3f},b={cal['b']:+.3f},n={cal.get('n')})")

    return out, reg, reasons


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

    floors = _observed_floors()
    history_cache = {}
    all_rows = []
    sample_prints = []
    priced_city_days = 0
    for city_key, city_markets in by_city.items():
        for m in city_markets:
            unit = m.get("unit") or unit_of.get(city_key, "C")
            band_rows = bands_by_market.get(m["market_id"], [])
            if not band_rows:
                continue
            result = process_city_day(city_key, m["resolution_date"], unit, band_rows,
                                      history_cache, floors)
            if result is None:
                continue
            rows, reg, reasons = result
            all_rows.extend(rows)
            priced_city_days += 1
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

    log_run(
        "probability_engine",
        "ok" if all_rows else "attention",
        len(all_rows),
        {"city_days": priced_city_days, "upcoming_markets": len(markets),
         "cities_with_observed_floor": len(floors)},
    )
    print(f"\nwrote {len(all_rows)} band_probabilities rows")


if __name__ == "__main__":
    main()
