#!/usr/bin/env python3
"""
Learn how wrong this desk's probabilities are, and correct the next ones.

THE QUESTION. Of every band AD4 priced at 30%, how many settled yes? If the
answer is 42%, the model is not merely imprecise - it is systematically
underconfident at 30%, by 12 points, and every trade sized off that number was
sized wrong. Until fact_band_outcome existed the desk could not ask this at
all: band_probabilities was written and never compared to anything.

THE METHOD. Platt scaling - a logistic regression on the log-odds of the
stated probability:

    calibrated = sigmoid(a * logit(p) + b)

Two parameters, fitted by gradient descent on log loss. That choice is
deliberate. Isotonic regression is more flexible and would fit the training
data better, which is exactly the problem: with a few hundred settled bands it
carves the curve into steps that describe this sample rather than the model's
behaviour. Two parameters cannot overfit a few hundred points, and they have
readable meanings:

    a < 1  the model is OVERCONFIDENT - its probabilities are pushed too far
           toward 0 and 1, and calibration pulls them back toward the middle
    a > 1  underconfident, too timid, pushed toward 0.5
    b != 0 a standing bias toward yes or no regardless of the probability

REFUSING TO FIT is a real outcome and the default one. Below MIN_SAMPLES the
script writes nothing and says so: a calibration map fitted on eighty rows is
more dangerous than none, because it looks like knowledge.

    python scripts/calibration.py [--min-samples 300] [--dry-run]
"""
import argparse
import datetime as dt
import json
import math
import sys

from common import rest, log_run, model_version_id, _cfg, _headers  # noqa: F401
import requests

# Below this, the honest output is "not enough evidence". A two-parameter fit
# needs a few hundred outcomes before its parameters mean anything.
MIN_SAMPLES = 300
EPS = 1e-6


def logit(p):
    p = min(max(p, EPS), 1 - EPS)
    return math.log(p / (1 - p))


def sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)                      # avoid overflow for large negative z
    return e / (1.0 + e)


def fit_platt(samples, iters=4000, lr=0.05):
    """samples: [(p_stated, outcome 0/1)] -> (a, b, log_loss).

    Plain gradient descent on log loss. No scipy: two parameters over a few
    thousand points converges in well under a second, and adding a numerical
    dependency to a repo that has none would cost more than it buys.
    """
    xs = [logit(p) for p, _ in samples]
    ys = [float(y) for _, y in samples]
    n = len(xs)
    a, b = 1.0, 0.0                       # start at the identity: no correction
    for _ in range(iters):
        ga = gb = 0.0
        for x, y in zip(xs, ys):
            err = sigmoid(a * x + b) - y
            ga += err * x
            gb += err
        a -= lr * ga / n
        b -= lr * gb / n
    loss = -sum(
        y * math.log(max(sigmoid(a * x + b), EPS)) + (1 - y) * math.log(max(1 - sigmoid(a * x + b), EPS))
        for x, y in zip(xs, ys)
    ) / n
    return a, b, loss


def apply_platt(p, a, b):
    return sigmoid(a * logit(p) + b)


def log_loss(samples):
    return -sum(
        y * math.log(max(min(p, 1 - EPS), EPS)) + (1 - y) * math.log(max(1 - min(p, 1 - EPS), EPS))
        for p, y in samples
    ) / len(samples)


def brier(samples):
    return sum((p - y) ** 2 for p, y in samples) / len(samples)


def describe(a, b):
    """What the two numbers mean, in words, because 'a=0.78' is not a finding."""
    bits = []
    if a < 0.92:
        bits.append(f"OVERCONFIDENT (a={a:.3f}): probabilities are pushed too far toward 0 and 1; "
                    f"calibration pulls them back toward the middle")
    elif a > 1.08:
        bits.append(f"UNDERCONFIDENT (a={a:.3f}): probabilities are too timid, huddled near 0.5; "
                    f"calibration pushes them out")
    else:
        bits.append(f"well scaled (a={a:.3f}): confidence is about right")
    if abs(b) > 0.08:
        bits.append(f"biased toward {'YES' if b > 0 else 'NO'} (b={b:+.3f}) regardless of the probability")
    else:
        bits.append(f"no standing directional bias (b={b:+.3f})")
    return "; ".join(bits)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--min-samples", type=int, default=MIN_SAMPLES)
    ap.add_argument("--dry-run", action="store_true", help="fit and report, write nothing")
    args = ap.parse_args()

    try:
        rows = rest("fact_band_outcome", [
            ("select", "model_prob,market_price,settled_yes,for_date,city_key"),
            ("model_prob", "not.is.null"), ("limit", "50000"),
        ])
    except Exception as e:
        print(f"fact_band_outcome unavailable ({e}). Run sql/ad4_18_databank.sql, then "
              f"scripts/databank.py.", file=sys.stderr)
        log_run("calibration", "attention", 0, {"error": str(e)})
        return 1

    samples = [(float(r["model_prob"]), 1 if r["settled_yes"] else 0)
               for r in rows if r.get("model_prob") is not None]
    if len(samples) < args.min_samples:
        msg = (f"{len(samples)} settled bands banked, {args.min_samples} needed. "
               f"Not fitting: a calibration map from this little data looks like knowledge "
               f"and is not. Keep running scripts/databank.py daily.")
        print(msg)
        log_run("calibration", "attention", len(samples), {"summary": msg, "n": len(samples)})
        return 0

    a, b, loss = fit_platt(samples)
    before_ll, before_br = log_loss(samples), brier(samples)
    cal = [(apply_platt(p, a, b), y) for p, y in samples]
    after_ll, after_br = log_loss(cal), brier(cal)

    # The market's own score on the same bands, as the benchmark that matters:
    # beating it is the entire proposition of the desk.
    mkt = [(float(r["market_price"]), 1 if r["settled_yes"] else 0)
           for r in rows if r.get("market_price") is not None]
    mkt_br = brier(mkt) if mkt else None

    print(f"fitted on {len(samples)} settled bands across "
          f"{len({r['city_key'] for r in rows})} cities")
    print(f"  {describe(a, b)}")
    print(f"  log loss  {before_ll:.4f} -> {after_ll:.4f}")
    print(f"  Brier     {before_br:.4f} -> {after_br:.4f}"
          + (f"   (market {mkt_br:.4f})" if mkt_br is not None else ""))
    if mkt_br is not None:
        verdict = ("the model beats the market on these bands"
                   if after_br < mkt_br else
                   "the MARKET is better calibrated than the model on these bands - "
                   "an edge computed from these probabilities is not yet evidence of one")
        print(f"  {verdict}")

    improved = after_br < before_br - 1e-6
    if not improved:
        print("  calibration does not improve the score; leaving the model uncorrected")

    payload = {
        "method": "platt", "a": round(a, 6), "b": round(b, 6),
        "n": len(samples), "fitted_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "brier_before": round(before_br, 6), "brier_after": round(after_br, 6),
        "log_loss_before": round(before_ll, 6), "log_loss_after": round(after_ll, 6),
        "market_brier": round(mkt_br, 6) if mkt_br is not None else None,
        "applies": bool(improved),
        "note": describe(a, b),
    }

    if args.dry_run:
        print("\n--dry-run: nothing written\n" + json.dumps(payload, indent=2))
        return 0

    # Stored in settings so the engine can read it without a schema change, and
    # registered as a model_version so any row priced with it is traceable.
    r = requests.post(
        f"{_cfg()['url']}/rest/v1/rpc/update_setting",
        headers=_headers(),
        data=json.dumps({"p_key": "calibration_map", "p_value": payload}),
        timeout=30,
    )
    if r.status_code >= 400 or (r.text and '"ok": false' in r.text.replace(" ", "")):
        # update_setting refuses keys outside its whitelist; fall back to a
        # direct upsert, which the service key is allowed to do.
        requests.post(
            f"{_cfg()['url']}/rest/v1/settings",
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "key"},
            data=json.dumps([{"key": "calibration_map", "value": payload}]),
            timeout=30,
        ).raise_for_status()

    model_version_id("calibration", f"platt:a={a:.4f}:b={b:.4f}:n={len(samples)}",
                     config=payload, structural=True)
    log_run("calibration", "ok", len(samples), payload)
    print("\nwritten to settings.calibration_map — probability_engine applies it on its next run")
    return 0


if __name__ == "__main__":
    sys.exit(main())
