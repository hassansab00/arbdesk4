"""Rejected research challenger: forecast errors in venue steps; never serving."""
import numpy as np
from wxpredict import remaining
RECIPE = "forecast-error-v1"
RADIUS = 12
CLASSES = 2 * RADIUS + 1


def anchor(df):
    value = np.full(len(df), np.nan)
    source = np.full(len(df), -1)
    for i, name in enumerate(("fc_bm_tmax_c", "fch_day_max_c", "fc_models_mean_c")):
        values = df[name].to_numpy(dtype=float)
        use = ~np.isfinite(value) & np.isfinite(values)
        value[use], source[use] = values[use], i
    unit_value = np.where(df.unit.values == "F", value * 9 / 5 + 32, value)
    return remaining.steps(df.unit.values, unit_value), source, unit_value


def eligible(df):
    return remaining.eligible(df) & np.isfinite(anchor(df)[0])


def target(df):
    base, _, _ = anchor(df)
    if not np.isfinite(base).all() or not np.isfinite(df.label_unit.values).all():
        raise ValueError("forecast-error training requires finite anchors and labels")
    error = remaining.steps(df.unit.values, df.label_unit.values) - base
    return np.clip(error, -RADIUS, RADIUS).astype(int) + RADIUS


def features(df, weather):
    x = remaining.features(df, weather)
    base, source, value = anchor(df)
    width = np.where(df.unit.values == "F", 2.0, 1.0)
    x["anchor_fraction"] = value / width - base
    x["anchor_gap"] = base - remaining.steps(df.unit.values, df.rmax_unit.values)
    x["anchor_source"] = source
    return x


def fit(x, y, seed, rounds=250):
    import lightgbm as lgb
    return lgb.train(dict(remaining.PARAMS, num_class=CLASSES, seed=seed),
                     lgb.Dataset(x, y), num_boost_round=rounds)


def bucket_probs(p, df, lo, hi, mask):
    p = np.asarray(p, dtype=float)
    if p.shape != (len(df), CLASSES) or not np.isfinite(p).all() or (p < 0).any():
        raise ValueError("invalid forecast-error probabilities")
    base, _, _ = anchor(df)
    if not np.isfinite(base).all():
        raise ValueError("forecast-error inference requires an anchor")
    floor = remaining.steps(df.unit.values, df.rmax_unit.values)
    f = df.unit.values == "F"
    width = np.where(f, 2.0, 1.0)
    out = np.zeros_like(lo, dtype=float)
    for k in range(CLASSES):
        step = np.maximum(base + k - RADIUS, floor)
        for offset in (0.0, 1.0):
            value = step * width + offset
            hit = mask & (lo <= value[:, None]) & (value[:, None] < hi)
            out += hit * (p[:, k] * np.where(f, .5, float(offset == 0)))[:, None]
    if (out.sum(axis=1) <= 0).any():
        raise ValueError("forecast-error ladder does not cover predictions")
    out = np.where(mask, np.maximum(out, remaining.FLOOR), 0.0)
    return out / out.sum(axis=1, keepdims=True)
