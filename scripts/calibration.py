#!/usr/bin/env python3
"""
Learn how wrong this desk's probabilities are, and correct the next ones -
out of sample, at the level the probabilities actually live.

WHAT THE PREVIOUS VERSION MEASURED, AND WHY IT WAS NOT A MEASUREMENT
--------------------------------------------------------------------
It fitted Platt scaling on every settled band and then scored the fit ON THE
SAME ROWS. Brier 0.074160 -> 0.073713: an improvement of 0.00045, in sample,
which is not evidence of anything. Adding parameters to a fit always lowers
the error on the data it was fitted to, and `applies` rested on exactly that
number.

Three deeper problems sat underneath it:

  THE ROWS ARE NOT INDEPENDENT EVENTS. 3,702 "samples" are about eleven
  MUTUALLY EXCLUSIVE bands from each of 340 city-days across 8 settlement
  dates. Exactly one band in each ladder wins, by construction. Treating them
  as 3,702 independent Bernoulli trials overstates the evidence by more than
  an order of magnitude - and that is before the correlation between cities on
  the same day, and between consecutive days.

  PER-BAND CALIBRATION BREAKS THE LADDER. Platt scaling each band separately
  and renormalising is not a calibration of the distribution; it is eleven
  unrelated corrections that happen to be divided by their own sum afterwards.

  THE MARKET BRIER IS NOT A BENCHMARK, not as it was quoted. 0.003819 against
  0.074 looks decisive and is not a like-for-like comparison: the two sides
  were not frozen at the same cutoff timestamp, so the market number includes
  information the model did not have. It is printed below as context and it
  never decides anything.

WHAT THIS DOES INSTEAD
----------------------
LADDER-LEVEL TEMPERATURE SCALING. One parameter, over the whole ladder:

    q_i = p_i^(1/T) / SUM_j p_j^(1/T)

T > 1 flattens an overconfident ladder toward uniform; T < 1 sharpens an
underconfident one. The ladder still sums to one by construction, which is the
property the desk prices against, and one parameter over a few hundred ladders
cannot learn their noise.

SPLIT BY SETTLEMENT DATE, IN TIME ORDER. The earlier 70% of DATES fit; the
later 30% are never touched until the verdict. Not shuffled, and split by date
rather than by row so no city-day can appear on both sides - the eleven bands
of one ladder are one observation, and putting some of them in train and the
rest in validation would be scoring the fit against itself.

AND THE GATE COMES FIRST. Below MIN_SETTLEMENT_DATES distinct dates or
MIN_COMPLETE_LADDERS complete ladders this writes an inactive map and says so.
Measured 2026-09-19: 336 complete ladders - which clears that half - across 8
settlement dates, which does not. A calibration fitted across eight days of
weather describes those eight days.

AND IT LEARNS ONLY FROM FORECASTS (26 Sep)
------------------------------------------
Until 26 Sep it learned from fact_band_outcome.model_prob, which databank
freezes from the LATEST pricing before settlement. Over the last 30 days of
that table, 0 of 673 city-days had been priced before the local day began,
338 in its afternoon and 335 after it had ended, when the engine holds the
running maximum. That fit said T=1.999937, and it would have been applied to the
forecasts the desk trades on as soon as the gate opened.

It now reads v_calibration_evidence: the RAW probability of the last pricing
before the city's local day began - the number the map is applied to, at the
moment it is a forecast. The engine refuses any map without this scope
(probability_engine.CALIBRATION_EVIDENCE_SCOPE), so the old fit cannot come
back. Measured 26 Sep on that evidence: 607 ladders over 13 dates; fitted on
the first 9 dates, T=1.365, and on the 4 later dates it made things WORSE
(Brier 0.7563 -> 0.7651, log loss 1.5988 -> 1.6475). The day-ahead ladder's
spread is about right; what it lacks is skill, which no temperature supplies.

THE CADENCE (Hassan, 28 Sep: "the calibration model should kick in every 7
days, not 30, and at the 30 mark it runs a full recalibration")
------------------------------------------------------------------------------
  below 7 dates   nothing can apply; the nightly fit is written INACTIVE, as
                  before, so the number is visible.
  weekly          from WEEKLY_MIN_DATES (7) settlement dates, the map is
                  updated every UPDATE_EVERY_DAYS (7) days: fitted on every
                  complete ladder so far, applied only if it beats the raw
                  ladder on the later dates it never saw. Below 30 dates an
                  update moves only part of the way to its fit - the evidence
                  weight, dates / 30 - because the 26 and 28 Sep fits on 13
                  and 15 dates made their held-out days worse.
  full            when the evidence first reaches FULL_RECALIBRATION_DATES
                  (30) dates, a full recalibration on everything collected, at
                  full weight, that night whatever the weekly clock says. The
                  weekly updates after it are full-weight too.
  not due         the map in force is left alone; the night's fit is logged as
                  a diagnostic only.

RULE 11 (adaptive never means unbounded):
  prior      T = 1 (no correction) until an update passes validation
  bounds     T in [T_MIN, T_MAX] = [0.5, 2.0] - this module's priors, not
             measurements
  min sample 7 settlement dates and 300 complete ladders before anything can
             apply (the full weight needs 30 dates)
  max step   an update moves T at most MAX_STEP (25%, the plan's P5.8 number)
             from the map applied before it, after the evidence weight
  version    every band_probabilities row carries calibration_version
  held out   scored only on dates the fit never saw

    python scripts/calibration.py [--dry-run] [--min-dates 7] [--min-ladders 300]
"""
import argparse
import datetime as dt
import json
import math
import sys

# What the map learned from; probability_engine applies only this scope.
CALIBRATION_EVIDENCE_SCOPE = "frozen_day_ahead_v1"

from common import rest_all, log_run, model_version_id, _cfg, _headers  # noqa: F401
import requests

# THE GATE. Both, not either.
#
# 30 distinct settlement dates, because the unit of independent evidence here
# is closer to a DAY than to a band: every city on one day shares the same
# synoptic pattern, and consecutive days share most of it. Eight days of data
# is one or two weather regimes. Since 28 Sep this is the FULL recalibration's
# mark (and the evidence weight's denominator), not the first day anything can
# apply: that is WEEKLY_MIN_DATES.
MIN_SETTLEMENT_DATES = 30
FULL_RECALIBRATION_DATES = MIN_SETTLEMENT_DATES
# Hassan, 28 Sep: calibration kicks in at 7 settlement dates and updates every
# 7 days. Held out-of-sample like any fit; weighted by dates / 30 until the
# full recalibration.
WEEKLY_MIN_DATES = 7
UPDATE_EVERY_DAYS = 7
# 300 complete ladders, because a ladder is the observation being calibrated
# and a one-parameter fit needs a few hundred of them to mean anything.
MIN_COMPLETE_LADDERS = 300
# The validation improvement that counts as real. A Brier gain smaller than
# this over a few hundred ladders is inside the noise of which days landed in
# the validation slice.
MIN_VALIDATION_BRIER_GAIN = 0.001
TRAIN_FRACTION = 0.70
# Rule 11. The bounds are this module's priors, not measurements. MAX_STEP is
# the plan's P5.8 guard: no learned value moves more than 25% in one night.
T_PRIOR = 1.0
T_MIN, T_MAX = 0.5, 2.0
MAX_STEP = 0.25
# A ladder missing bands is not a ladder: the probabilities no longer describe
# a partition of the outcome space, so normalising them means something else.
MIN_BANDS_IN_LADDER = 8
EPS = 1e-9


# --------------------------------------------------------------------------
# Pure math. A ladder is [(p, y)] with exactly one y == 1.
# --------------------------------------------------------------------------
def temper(ps, T):
    """q_i = p_i^(1/T) / SUM_j p_j^(1/T), computed in log space."""
    u = 1.0 / T
    logps = [math.log(max(p, EPS)) for p in ps]
    m = max(u * lp for lp in logps)
    ws = [math.exp(u * lp - m) for lp in logps]
    z = sum(ws)
    return [w / z for w in ws]


def fit_temperature(ladders, iters=400, lr=0.5):
    """One parameter, by gradient descent on multiclass log loss.

    Optimised over u = 1/T because the objective is a softmax over u*log(p),
    whose gradient is the difference between the winner's log-probability and
    the tempered expectation of it - two lines, no numerical dependency, and
    the repo has none.
    """
    if not ladders:
        return 1.0
    u = 1.0
    for _ in range(iters):
        g = 0.0
        for lad in ladders:
            logps = [math.log(max(p, EPS)) for p, _ in lad]
            win = next((i for i, (_, y) in enumerate(lad) if y), None)
            if win is None:
                continue
            m = max(u * lp for lp in logps)
            ws = [math.exp(u * lp - m) for lp in logps]
            z = sum(ws)
            q = [w / z for w in ws]
            g += -(logps[win] - sum(qi * lp for qi, lp in zip(q, logps)))
        u -= lr * g / len(ladders)
        u = min(20.0, max(0.05, u))       # T between 0.05 and 20; beyond is noise
    return 1.0 / u


def multiclass_brier(ladders, T=None):
    """Mean over LADDERS of SUM_i (q_i - y_i)^2.

    Per ladder, not per band. The per-band figure divides the same error by
    eleven and makes every model look eleven times better calibrated than it
    is.
    """
    if not ladders:
        return None
    total = 0.0
    for lad in ladders:
        qs = temper([p for p, _ in lad], T) if T else [p for p, _ in lad]
        total += sum((q - y) ** 2 for q, (_, y) in zip(qs, lad))
    return total / len(ladders)


def multiclass_log_loss(ladders, T=None):
    """Mean over ladders of -log q(winner)."""
    if not ladders:
        return None
    total = 0.0
    for lad in ladders:
        qs = temper([p for p, _ in lad], T) if T else [p for p, _ in lad]
        z = sum(qs) or 1.0
        win = next((i for i, (_, y) in enumerate(lad) if y), None)
        if win is None:
            continue
        total += -math.log(max(qs[win] / z, EPS))
    return total / len(ladders)


def bounded(T, previous=T_PRIOR):
    """T held inside its hard bounds and within MAX_STEP of the map applied
    before it. What is validated and written is this number, not the raw fit."""
    lo = max(T_MIN, previous * (1 - MAX_STEP))
    hi = min(T_MAX, previous * (1 + MAX_STEP))
    return min(hi, max(lo, T))


def previous_T(value):
    """The T the engine applies today: the stored map's, when it applies and
    carries this scope; otherwise the prior."""
    if (isinstance(value, dict) and value.get("applies") and value.get("method") == "temperature"
            and value.get("evidence_scope") == CALIBRATION_EVIDENCE_SCOPE and value.get("T")):
        return float(value["T"])
    return T_PRIOR


def anchor_T(value, today):
    """The T in force BEFORE the night `today` (plan v2.3 P5.14). A map already
    fitted tonight is a re-run's own first run: step from the T that run
    stepped from (its T_previous), not from its output, or one night moves T
    twice. Any older map: the T the engine applies (previous_T)."""
    if (isinstance(value, dict) and str(value.get("fitted_at") or "")[:10] == str(today)
            and value.get("T_previous") is not None):
        return float(value["T_previous"])
    return previous_T(value)


def evidence_weight(n_dates):
    """How far an update moves toward its fit: dates / 30, capped at 1. A
    weekly update on 15 dates goes half way; the full recalibration and every
    update after it go all the way (still within MAX_STEP)."""
    return min(1.0, max(0, n_dates) / FULL_RECALIBRATION_DATES)


def _day(value):
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def schedule(stored, today, n_dates):
    """(kind, due, next_update_on) for tonight.

      gate     below WEEKLY_MIN_DATES: nothing can apply; the inactive fit is
               written every night, as before
      full     the evidence has reached FULL_RECALIBRATION_DATES and no full
               recalibration is on record
      weekly   UPDATE_EVERY_DAYS since the last update, or none yet
      not_due  otherwise: the map in force stays

    A re-run on the night of an update is due again with the same kind, and
    reproduces it (anchor_T steps from what that night stepped from)."""
    stored = stored if isinstance(stored, dict) else {}
    if n_dates < WEEKLY_MIN_DATES:
        return "gate", True, None
    updated = _day(stored.get("updated_on"))
    full_on = _day(stored.get("full_on"))
    if updated == today:
        return (stored.get("update_kind") or "weekly"), True, today + dt.timedelta(days=UPDATE_EVERY_DAYS)
    if n_dates >= FULL_RECALIBRATION_DATES and full_on is None:
        return "full", True, today + dt.timedelta(days=UPDATE_EVERY_DAYS)
    if updated is None or (today - updated).days >= UPDATE_EVERY_DAYS:
        return "weekly", True, today + dt.timedelta(days=UPDATE_EVERY_DAYS)
    return "not_due", False, updated + dt.timedelta(days=UPDATE_EVERY_DAYS)


def step_toward(T_fitted, prev, weight):
    """The weighted move from the T in force toward the fit, then held to the
    bounds and the step (bounded)."""
    return bounded(prev + weight * (T_fitted - prev), prev)


def describe(T):
    """What the number means, because 'T=1.18' is not a finding."""
    if T > 1.05:
        return (f"OVERCONFIDENT (T={T:.3f}): the ladder is too peaked, and calibration "
                f"flattens it toward uniform")
    if T < 0.95:
        return (f"UNDERCONFIDENT (T={T:.3f}): the ladder is too flat, and calibration "
                f"sharpens it")
    return f"well scaled (T={T:.3f}): the ladder's spread is about right"


# --------------------------------------------------------------------------
# Shaping the evidence
# --------------------------------------------------------------------------
def build_ladders(rows, min_bands=MIN_BANDS_IN_LADDER):
    """{(city, date): [(p, y)]} for the COMPLETE ladders only.

    Complete means enough bands to be a partition and exactly one winner.
    Anything else is a fragment, and normalising a fragment produces numbers
    that do not describe the outcome space they are scored against.
    """
    by_day = {}
    for r in rows:
        p = r.get("prob")
        if p is None:
            continue
        by_day.setdefault((r["city_key"], str(r["for_date"])), []).append(
            (float(p), 1 if r.get("settled_yes") else 0))
    out, dropped = {}, 0
    for key, lad in by_day.items():
        if len(lad) >= min_bands and sum(y for _, y in lad) == 1:
            out[key] = lad
        else:
            dropped += 1
    return out, dropped


def split_by_date(ladders, train_fraction=TRAIN_FRACTION):
    """(train, validation, train_dates, val_dates), in time order.

    Split on the DATE, never on the row. A ladder is one observation and its
    eleven bands are not eleven; putting some of them in train and the rest in
    validation would score the fit against itself.
    """
    dates = sorted({d for _, d in ladders})
    if len(dates) < 2:
        return [], [], [], dates
    cut = max(1, min(len(dates) - 1, int(round(len(dates) * train_fraction))))
    train_dates, val_dates = set(dates[:cut]), set(dates[cut:])
    train = [lad for (_, d), lad in ladders.items() if d in train_dates]
    val = [lad for (_, d), lad in ladders.items() if d in val_dates]
    return train, val, sorted(train_dates), sorted(val_dates)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-dates", type=int, default=WEEKLY_MIN_DATES)
    ap.add_argument("--min-ladders", type=int, default=MIN_COMPLETE_LADDERS)
    ap.add_argument("--dry-run", action="store_true", help="fit and report, write nothing")
    args = ap.parse_args()

    try:
        rows = rest_all("v_calibration_evidence", [
            ("select", "band_id,prob,settled_yes,for_date,city_key"),
            ("prob", "not.is.null"),
        ], order="for_date.asc,band_id.asc", page_size=1000)
        stored = rest_all("settings", [("select", "value"), ("key", "eq.calibration_map")], order="key.asc")
    except Exception as e:
        print(f"calibration evidence unavailable ({e}). Apply migration "
              f"20260926100000_calibration_learns_frozen_calls.sql.", file=sys.stderr)
        log_run("calibration", "attention", 0, {"error": str(e)})
        return 1

    ladders, dropped = build_ladders(rows)
    dates = sorted({d for _, d in ladders})
    cities = sorted({c for c, _ in ladders})

    # FOUR NUMBERS, NOT ONE. Reporting 3,702 "samples" is what made eight days
    # of weather look like a large study.
    print(f"{len(rows)} band rows  |  {len(ladders)} complete ladders  |  "
          f"{len(dates)} settlement dates  |  {len(cities)} cities")
    if dropped:
        print(f"  {dropped} city-day(s) dropped: fewer than {MIN_BANDS_IN_LADDER} bands, "
              f"or not exactly one winner")
    print("  band rows are NOT independent events: about "
          f"{len(rows) // max(1, len(ladders))} mutually exclusive bands share each ladder, "
          "and exactly one of them wins by construction")

    gate = []
    if len(dates) < args.min_dates:
        gate.append(f"{len(dates)} settlement dates, needs {args.min_dates}")
    if len(ladders) < args.min_ladders:
        gate.append(f"{len(ladders)} complete ladders, needs {args.min_ladders}")

    today = dt.datetime.now(dt.timezone.utc).date()
    current = stored[0]["value"] if stored else None
    kind, due, next_update = schedule(current, today, len(dates))
    weight = 1.0 if kind == "full" else evidence_weight(len(dates))
    print(f"  cadence: {kind}" + ("" if due else f" - the map in force stays until {next_update}")
          + (f"; evidence weight {weight:.2f} ({len(dates)} of {FULL_RECALIBRATION_DATES} dates)"
             if kind in ("weekly", "full") else ""))

    train, val, train_dates, val_dates = split_by_date(ladders)
    prev = anchor_T(current, today)
    T_fitted = fit_temperature(train) if train else T_PRIOR
    T = step_toward(T_fitted, prev, weight)
    if T != T_fitted:
        print(f"  fitted T={T_fitted:.3f} moved to {T:.3f}: weight {weight:.2f}, bounds [{T_MIN}, {T_MAX}], "
              f"at most {MAX_STEP:.0%} from the T applied now ({prev:.3f})")

    before_br = multiclass_brier(val) if val else None
    after_br = multiclass_brier(val, T) if val else None
    before_ll = multiclass_log_loss(val) if val else None
    after_ll = multiclass_log_loss(val, T) if val else None

    if train and val:
        print(f"\n  fitted on {len(train)} ladder(s) across {len(train_dates)} date(s) "
              f"({train_dates[0]} to {train_dates[-1]})")
        print(f"  scored on {len(val)} ladder(s) across {len(val_dates)} LATER date(s) "
              f"({val_dates[0]} to {val_dates[-1]}), never seen by the fit")
        print(f"  {describe(T)}")
        print(f"  validation multiclass Brier  {before_br:.4f} -> {after_br:.4f}")
        print(f"  validation log loss          {before_ll:.4f} -> {after_ll:.4f}")
    else:
        print("\n  not enough distinct dates to hold any of them back; nothing was fitted")

    # THE MARKET IS NOT READ HERE. The old "context only" market Brier came
    # from fact_band_outcome.market_price, frozen at the same late cutoff as the
    # model's number; the like-for-like comparison, both sides frozen at the
    # same moment, is v_checkpoint_scoreboard and v_city_hit_history.

    brier_gain = (before_br - after_br) if (before_br is not None and after_br is not None) else None
    ll_improved = (before_ll is not None and after_ll is not None and after_ll < before_ll)
    validated = bool(brier_gain is not None
                     and brier_gain >= MIN_VALIDATION_BRIER_GAIN
                     and ll_improved)
    applies = bool(not gate and validated)

    if gate:
        print("\nNOT APPLYING - " + "; ".join(gate) + ".")
        print("  A calibration fitted across this little of the calendar describes these "
              "days, not this model. The map is written INACTIVE so the number is visible "
              "and nothing prices on it.")
    elif not validated:
        print("\nNOT APPLYING - the correction does not earn its place on days the fit "
              f"never saw (Brier {brier_gain:+.4f}, needs {MIN_VALIDATION_BRIER_GAIN:+.4f}; "
              f"log loss {'improved' if ll_improved else 'did not improve'}).")
    else:
        print(f"\nAPPLYING - validated on {len(val)} held-out ladder(s) across "
              f"{len(val_dates)} later date(s).")

    payload = {
        "method": "temperature",
        "T": round(T, 6),
        "T_fitted": round(T_fitted, 6),
        "T_previous": round(prev, 6),
        "prior": T_PRIOR, "bounds": [T_MIN, T_MAX], "max_step": MAX_STEP,
        "evidence_scope": CALIBRATION_EVIDENCE_SCOPE,
        "band_rows": len(rows),
        "complete_ladders": len(ladders),
        "settlement_dates": len(dates),
        "cities": len(cities),
        "train_ladders": len(train), "train_dates": len(train_dates),
        "validation_ladders": len(val), "validation_dates": len(val_dates),
        "first_validation_date": val_dates[0] if val_dates else None,
        "validation_brier_before": round(before_br, 6) if before_br is not None else None,
        "validation_brier_after": round(after_br, 6) if after_br is not None else None,
        "validation_log_loss_before": round(before_ll, 6) if before_ll is not None else None,
        "validation_log_loss_after": round(after_ll, 6) if after_ll is not None else None,
        "gate_unmet": gate,
        "applies": applies,
        "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "note": describe(T),
        "update_kind": kind,
        "evidence_weight": round(weight, 4),
        "weekly_min_dates": WEEKLY_MIN_DATES, "update_every_days": UPDATE_EVERY_DAYS,
        "full_recalibration_dates": FULL_RECALIBRATION_DATES,
        "updated_on": today.isoformat() if kind in ("weekly", "full") else None,
        "next_update_on": next_update.isoformat() if next_update else None,
        "full_on": (today.isoformat() if kind == "full"
                    else (current or {}).get("full_on") if isinstance(current, dict) else None),
    }

    if not due:
        # NOT DUE: the map in force stays. What tonight's fit would have done
        # is logged, and the log says what is actually in force, so the
        # Predictive page's state (v_calibration_status) stays true.
        in_force = current if isinstance(current, dict) else {}
        diagnostic = dict(payload, applies=bool(in_force.get("applies")), T_candidate=payload["T"],
                          T=in_force.get("T", T_PRIOR), gate_unmet=[],
                          note=f"not due - the map of {in_force.get('updated_on')} stays until "
                               f"{next_update}; tonight's fit: {describe(T)}")
        if args.dry_run:
            print("\n--dry-run: nothing written\n" + json.dumps(diagnostic, indent=2))
            return 0
        log_run("calibration", "ok", len(ladders), diagnostic)
        print(f"\nnot due: settings.calibration_map unchanged until {next_update}")
        return 0

    if args.dry_run:
        print("\n--dry-run: nothing written\n" + json.dumps(payload, indent=2))
        return 0

    r = requests.post(
        f"{_cfg()['url']}/rest/v1/rpc/update_setting",
        headers=_headers(),
        data=json.dumps({"p_key": "calibration_map", "p_value": payload}),
        timeout=30,
    )
    if r.status_code >= 400 or (r.text and '"ok": false' in r.text.replace(" ", "")):
        requests.post(
            f"{_cfg()['url']}/rest/v1/settings",
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "key"},
            data=json.dumps([{"key": "calibration_map", "value": payload}]),
            timeout=30,
        ).raise_for_status()

    model_version_id("calibration", f"temperature:T={T:.4f}:ladders={len(ladders)}",
                     config=payload, structural=True)
    log_run("calibration", "ok", len(ladders), payload)
    print("\nwritten to settings.calibration_map"
          + (" - probability_engine applies it on its next run" if applies
             else " - inactive, so nothing prices on it"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
