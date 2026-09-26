"""The belief layer (plan v2 P5.3): from model probability to a posterior.

The engine's ladder says what the MODEL thinks each bucket's chance is. This
says what that number has been worth: for every bin of p_model (width 0.05),
how often buckets priced there actually won, on settled checkpoint rows
(P4.1 ladders, P4.3 venue winners). A bucket the model calls 60%, in a bin
whose buckets have won 45% of the time, is believed at about 45% - and a bin
with no history keeps the model's own number with a wide standard deviation,
which is what makes the solver (P5.5) size it small.

THE MODEL: a Beta-binomial per bin, with partial pooling.

    pooled                      prior Beta(k0 * p_model, k0 * (1 - p_model))
      -> city cluster (P5.9)    prior centred on the pooled posterior mean
         -> checkpoint class    prior centred on the level above

Each level adds its own wins and losses in that bin to a prior of strength
k0 centred on the level above, so a narrow scope with little data sits on the
broad answer and moves off it only as its own record grows. With no data at
any level the posterior mean is p_model itself.

RULE 11 - ADAPTIVE NEVER MEANS UNBOUNDED. Every number learned here has:
  - a prior:          p_model, strength K0 = 30 (the plan's value);
  - hard bounds:      a posterior mean inside [P_LO, P_HI];
  - a minimum sample: a scope's bin with fewer than N_MIN settled buckets
                      contributes nothing, it stays on the level above;
  - a maximum step:   a refit may move a bin's frequency at most MAX_STEP
                      (25%, the plan's P5.8 guard) from the previous version;
  - a version:        fit() stamps one, and every posterior carries it.
And it is never evaluated on its own training data: fit() takes `as_of` and
learns only from target dates strictly before it.

K0 and MAX_STEP are the plan's numbers. N_MIN, P_LO and P_HI are this
module's priors, not measurements, and are recorded in every fitted table so
a later version that changes them is visible.
"""
import datetime as dt
import hashlib
import json
import math
import sys

BIN_WIDTH = 0.05
N_BINS = 20
K0 = 30.0
N_MIN = 20
MAX_STEP = 0.25
P_LO, P_HI = 0.001, 0.999
PRIOR_VERSION = "prior"

# P4.2's six checkpoints, folded into the plan's five classes.
CHECKPOINT_CLASS = {
    "d1_eve": "pre_day",
    "morning": "morning",
    "noon": "midday",
    "prepeak_2h": "pre_peak",
    "prepeak_1h": "pre_peak",
    "postpeak_1h": "post_peak",
}


def bin_of(p):
    """Index of the 0.05-wide, half-open [lo, hi) bin holding p; 1.0 falls in the last.

    Rounded before the floor: 0.60 / 0.05 is 11.999999999999998 in floating
    point, and a bucket priced at exactly 0.60 belongs to [0.60, 0.65).
    """
    p = min(max(float(p), 0.0), 1.0)
    return min(int(math.floor(round(p / BIN_WIDTH, 9))), N_BINS - 1)


def checkpoint_class(label):
    return CHECKPOINT_CLASS.get(label)


def scopes_for(cluster=None, checkpoint=None):
    """The chain of scopes a belief is read through, broadest first."""
    chain = ["pooled"]
    parent = "pooled"
    if cluster:
        parent = f"cluster:{cluster}"
        chain.append(parent)
    cls = checkpoint_class(checkpoint) if checkpoint else None
    if cls:
        chain.append(f"{parent}|class:{cls}")
    return chain


def _clip(p):
    return min(max(p, P_LO), P_HI)


def beta_mean_sd(a, b):
    n = a + b
    mean = a / n
    sd = math.sqrt(a * b / (n * n * (n + 1.0)))
    return mean, sd


# --------------------------------------------------------------------------
# Posterior for one bucket, and for a ladder
# --------------------------------------------------------------------------

def posterior(p_model, table=None, cluster=None, checkpoint=None):
    """(p_post, p_sd, version) for one bucket the model prices at p_model.

    `table` is a fitted table from fit(), or None for the prior. Levels whose
    bin has fewer than N_MIN settled buckets add nothing.
    """
    p = _clip(float(p_model))
    centre = p
    a, b = K0 * p, K0 * (1.0 - p)
    version = PRIOR_VERSION
    if table:
        version = table.get("version", PRIOR_VERSION)
        k = str(bin_of(p))
        n_min = table.get("n_min", N_MIN)
        for scope in scopes_for(cluster, checkpoint):
            wins, n = (table.get("bins", {}).get(scope, {}).get(k) or (0, 0))
            a, b = K0 * centre, K0 * (1.0 - centre)
            if n >= n_min:
                a, b = a + wins, b + (n - wins)
            centre = _clip(a / (a + b))
    mean, sd = beta_mean_sd(a, b)
    return _clip(mean), sd, version


def ladder_posterior(probs, table=None, cluster=None, checkpoint=None):
    """{band_id: (p_post, p_sd)} for a whole ladder, means renormalised to 1.

    The buckets of a ladder are mutually exclusive and exactly one pays, so
    the means must sum to one. Each sd is scaled by the same factor as its
    mean, which keeps its uncertainty relative to its size.
    """
    raw = {bid: posterior(p, table, cluster, checkpoint) for bid, p in probs.items()}
    total = sum(m for m, _sd, _v in raw.values())
    if total <= 0:
        return {bid: (0.0, sd) for bid, (_m, sd, _v) in raw.items()}
    scale = 1.0 / total
    return {bid: (m * scale, sd * scale) for bid, (m, sd, _v) in raw.items()}


# --------------------------------------------------------------------------
# Fitting: walk-forward, bounded, versioned
# --------------------------------------------------------------------------

def observations(checkpoints, outcomes, as_of, clusters=None):
    """(p_model, won, cluster, checkpoint) for every bucket of every settled ladder.

    checkpoints: prediction_checkpoints rows (checkpoint_id, city_key,
    target_date, checkpoint, probs). outcomes: fact_checkpoint_outcome rows
    (checkpoint_id, winner_band_id, ladder_has_winner). Only target dates
    strictly before `as_of` are used, and only ladders whose venue winner is
    on the ladder - a ladder that does not hold the winner cannot say which
    of its buckets lost.
    """
    as_of = as_of if isinstance(as_of, dt.date) else dt.date.fromisoformat(str(as_of))
    won_by = {str(o["checkpoint_id"]): str(o["winner_band_id"])
              for o in outcomes
              if o.get("winner_band_id") and o.get("ladder_has_winner") is not False}
    out = []
    for c in checkpoints:
        winner = won_by.get(str(c["checkpoint_id"]))
        if winner is None:
            continue
        td = c["target_date"]
        td = td if isinstance(td, dt.date) else dt.date.fromisoformat(str(td)[:10])
        if td >= as_of:
            continue
        probs = c.get("probs") or {}
        if winner not in probs:
            continue
        cluster = (clusters or {}).get(c.get("city_key"))
        for bid, p in probs.items():
            if p is None:
                continue
            out.append((float(p), bid == winner, cluster, c.get("checkpoint")))
    return out


def _counts(obs):
    bins = {}

    def add(scope, k, won):
        cell = bins.setdefault(scope, {}).setdefault(k, [0, 0])
        cell[0] += int(won)
        cell[1] += 1

    for p, won, cluster, checkpoint in obs:
        k = str(bin_of(p))
        for scope in scopes_for(cluster, checkpoint):
            add(scope, k, won)
    return bins


def _limit_step(prev, bins, max_step):
    """Hold each bin's frequency within max_step (relative) of the last version.

    The sample size is kept - it is what the data is - and only the wins are
    moved, so the posterior's weight is honest while its centre moves slowly.
    Returns how many cells were held back.
    """
    held = 0
    if not prev:
        return held
    for scope, cells in bins.items():
        for k, cell in cells.items():
            old = (prev.get("bins", {}).get(scope, {}) or {}).get(k)
            if not old or old[1] <= 0 or cell[1] <= 0:
                continue
            f_old = old[0] / old[1]
            f_new = cell[0] / cell[1]
            # Relative to the old frequency, with the bin's own width as a
            # floor so a bin that has never won can still learn that it does.
            room = max(max_step * f_old, max_step * BIN_WIDTH)
            f_lim = min(max(f_new, f_old - room), f_old + room)
            if abs(f_lim - f_new) > 1e-12:
                cell[0] = f_lim * cell[1]
                held += 1
    return held


def fit(checkpoints, outcomes, as_of, clusters=None, previous=None):
    """A fitted belief table from settled rows before `as_of`.

    Deterministic: the same inputs give the same version string.
    """
    obs = observations(checkpoints, outcomes, as_of, clusters)
    bins = _counts(obs)
    held = _limit_step(previous, bins, MAX_STEP)
    body = {"bin_width": BIN_WIDTH, "k0": K0, "n_min": N_MIN, "max_step": MAX_STEP,
            "p_lo": P_LO, "p_hi": P_HI, "as_of": str(as_of), "n": len(obs),
            "previous": (previous or {}).get("version"), "held_back": held, "bins": bins}
    digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
    body["version"] = f"belief:{as_of}:{digest[:10]}"
    return body


# --------------------------------------------------------------------------
# Loading: the latest fitted version, or the prior
# --------------------------------------------------------------------------

def load(rest=None):
    """The latest belief table from strategy_params, or None (the prior).

    strategy_params is written by the nightly loop (P5.8), and read only while
    settings.strategy_learning.enabled is true. Until then, or if it cannot be
    read, every posterior is the prior - p_model with a
    wide sd - which is the safe direction: nothing is believed more strongly
    than the model already says.
    """
    if rest is None:
        from common import rest as rest
    import learned
    if not learned.enabled(rest):
        return None                      # priors frozen until the replay (P5.8, P7.3)
    try:
        rows = rest("strategy_params", [("select", "value,version"), ("param", "eq.belief"),
                                        ("order", "fitted_at.desc"), ("limit", "1")])
    except Exception as e:
        print(f"  note: no belief table ({e}); every posterior is the prior", file=sys.stderr)
        return None
    if not rows:
        return None
    table = rows[0].get("value") or {}
    table.setdefault("version", rows[0].get("version") or PRIOR_VERSION)
    return table


def _report():
    """Fit on everything settled before today and print the pooled bins."""
    from common import rest_all
    today = dt.datetime.now(dt.timezone.utc).date()
    cps = rest_all("prediction_checkpoints",
                   {"select": "checkpoint_id,city_key,target_date,checkpoint,probs",
                    "target_date": f"lt.{today}"}, order="checkpoint_id.asc")
    outs = rest_all("fact_checkpoint_outcome",
                    {"select": "checkpoint_id,winner_band_id,ladder_has_winner"},
                    order="checkpoint_id.asc")
    table = fit(cps, outs, today)
    print(f"{table['version']}: {table['n']} settled buckets from "
          f"{len(cps)} checkpoints before {today}, {len(outs)} outcomes")
    for k, (w, n) in sorted(table["bins"].get("pooled", {}).items(), key=lambda x: int(x[0])):
        lo = int(k) * BIN_WIDTH
        print(f"  p_model {lo:.2f}-{lo + BIN_WIDTH:.2f}: won {w:.0f}/{n}"
              f"{'' if n >= N_MIN else '  (under N_MIN, stays on the prior)'}")


if __name__ == "__main__":
    _report()
