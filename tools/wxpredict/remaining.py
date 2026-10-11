"""WXPredict phase 3A: the remaining day.

On the day itself the question is not what the maximum will be but how much
higher it can still go than the maximum already reported. This model answers
that in the market's own steps: j, the number of buckets the day's maximum
ends above the bucket of the running maximum (a step is 1 C on a Celsius
ladder, 2 F on a Fahrenheit one, whose buckets start on even degrees). A
multiclass LightGBM gives P(j = 0..K-1) and P(j >= K) directly from what was
known at the decision, with no assumed shape; each bucket's probability is
the mass of the steps that land in it.

Used from INC_FROM_HOUR local on the day, where a running maximum exists
(11 Oct, the table: after 09h the maximum rises 10 or more steps on 0.1% of
rows or fewer from 12h; before it, 14-16%, which the forecast model covers).
Research only: tools/wxpredict/walkforward.py --remaining scores it.
"""
import lightgbm as lgb
import numpy as np
import pandas as pd

INC_FROM_HOUR = 9
K = 16                     # classes 0..K-1 and "K or more"
FLOOR = 0.0005             # features.PRICE_FLOOR, as the market's prices are floored


def eligible(df):
    return ((df.day_offset == 0) & df.rmax_unit.notna() & (df.local_hour >= INC_FROM_HOUR)).values


def steps(unit, value):
    """The step a value in the market's unit falls in: C 1 degree, F 2 degrees from even."""
    v = np.asarray(value, dtype=float)
    return np.where(np.asarray(unit) == "F", np.floor(v / 2), np.floor(v))


def target(df):
    j = steps(df.unit.values, df.label_unit.values) - steps(df.unit.values, df.rmax_unit.values)
    return np.clip(j, 0, K).astype(int)


def features(df, x):
    """The weather features and what the remaining day turns on: how far the
    forecasts and the current reading are above the running maximum, in the
    market's steps, and how far the hour is from the usual peak."""
    x = x.copy()
    per_step = np.where(df.unit.values == "F", 10 / 9, 1.0)          # C per step
    for name, col in (("gap_rest", "fch_rest_max_c"), ("gap_day", "fch_day_max_c"), ("gap_bm", "fc_bm_tmax_c"),
                      ("gap_models", "fc_models_mean_c"), ("gap_now", "t_now_c"), ("gap_next3", "fch_next3_c")):
        x[name] = (df[col].values - df.rmax_c.values) / per_step
    x["to_peak_h"] = df.clim_peak_hour.values - df.local_hour.values
    x["to_fc_peak_h"] = df.fch_peak_local_hour.values - df.local_hour.values
    return x


PARAMS = dict(objective="multiclass", num_class=K + 1, learning_rate=0.08, num_leaves=63, min_data_in_leaf=200,
              feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0, verbose=-1,
              num_threads=4, deterministic=True, force_col_wise=True)


def fit(x, j, seed, rounds=250):
    return lgb.train(dict(PARAMS, seed=seed), lgb.Dataset(x, j), num_boost_round=rounds)


def bucket_probs(p, unit, rmax_unit, lo, hi, mask):
    """Each bucket's probability from P(step j): step s0 + j covers the values
    [s, s + 1) on a Celsius ladder and [2s, 2s + 2) on a Fahrenheit one; the
    "K or more" class goes to the bucket of step s0 + K and the ones above it,
    in proportion to how many steps each covers up to s0 + K + 4 (the open top
    bucket takes what lies beyond). Floored and normalised like the market."""
    n, kb = lo.shape
    out = np.zeros((n, kb))
    f = np.asarray(unit) == "F"
    width = np.where(f, 2.0, 1.0)
    s0 = steps(unit, rmax_unit)
    for j in range(K + 1):
        if j < K:
            spread = [(j, 1.0)]
        else:
            spread = [(j + d, 1 / 5) for d in range(5)]
        for jj, share in spread:
            v = (s0 + jj) * width                                    # the step's lowest value
            for off in (0.0, 1.0):                                    # its two integers on an F ladder
                val = v + off
                use = (off == 0.0) | f
                hit = mask & (lo <= val[:, None]) & (val[:, None] < hi) & use[:, None]
                w = p[:, j] * share * np.where(f, 0.5, 1.0)
                out += hit * w[:, None]
    out = np.where(mask, np.maximum(out, FLOOR), 0.0)
    return out / out.sum(1, keepdims=True)
