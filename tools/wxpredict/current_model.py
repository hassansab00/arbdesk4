"""A fixed weather-only band classifier using received current forecasts.

Targets are the venue's winning bucket, including open ends. No midpoint or
unreported maximum is invented. The accepted predictor is a bounded correction
to the actually served incumbent; the new weather model must earn its weight.
"""
import math
import hashlib
from pathlib import Path

from wxpredict import current_calls, current_recipe

RECIPE = 'current-band-v1'
SEED, ROUNDS, BLEND = 20261024, 120, .10
PARAMS = dict(objective='binary', learning_rate=.03, num_leaves=15,
              min_data_in_leaf=200, lambda_l2=20., feature_fraction=.8,
              bagging_fraction=.8, bagging_freq=1, num_threads=2, verbose=-1,
              deterministic=True, force_col_wise=True, seed=SEED)


def source_hashes():
    names=('current_calls.py','current_cycle.py','current_model.py','current_recipe.py',
           'current_forecasts.py','decision_inputs.py','remaining.py','features.py')
    return {name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest() for name in names}


def matrix(records, labels=False):
    import numpy as np
    import pandas as pd
    rows = [r['row'] for r in records]
    base = current_recipe.features(pd.DataFrame(rows)).reset_index(drop=True)
    base = pd.DataFrame({col:base[col] if isinstance(base[col].dtype,pd.CategoricalDtype)
                        else pd.to_numeric(base[col],errors='raise') for col in base})
    for name in ('current_hourly_age_h','current_models_age_h'):
        base[name] = pd.to_numeric([r['row'][name] for r in records],errors='raise')
    base['station_id'] = pd.Series([r['row']['station'] for r in records], dtype='category')
    base['observation_source'] = pd.Series([r['row'].get('observation_contract') or 'legacy'
                                          for r in records], dtype='category')
    selected, extras, targets, weights, sizes = [], [], [], [], []
    for i, record in enumerate(records):
        row = record['row']
        step = 2. if row['unit'] == 'F' else 1.
        forecast = float(row['current_max_c'])
        forecast = forecast*9/5+32 if row['unit'] == 'F' else forecast
        floor = float(row['rmax_unit'])
        if not math.isfinite(floor):
            raise ValueError('non-finite qualifying running maximum')
        edges = current_calls.bounds(row)
        if labels and not 0 <= record['winner'] < len(edges):
            raise ValueError('winner outside frozen ladder')
        sizes.append(len(edges))
        for j, ((lo, hi), bid) in enumerate(zip(edges, record['band_ids'])):
            selected.append(i)
            extras.append(dict(band_lo_from_current=(lo-forecast)/step if lo is not None else np.nan,
                               band_hi_from_current=(hi-forecast)/step if hi is not None else np.nan,
                               band_lo_from_floor=(lo-floor)/step if lo is not None else np.nan,
                               band_hi_from_floor=(hi-floor)/step if hi is not None else np.nan,
                               band_open_low=int(lo is None), band_open_high=int(hi is None),
                               band_count=len(edges), incumbent_raw_prob=record['raw_probs'][bid]))
            if labels:
                targets.append(int(j == record['winner']))
                weights.append(1./len(edges))  # one first call has total weight one
    x = base.iloc[selected].reset_index(drop=True)
    return pd.concat([x, pd.DataFrame(extras)], axis=1), targets, weights, sizes


def fit(records):
    import lightgbm as lgb
    x, y, weights, _ = matrix(records, labels=True)
    return lgb.train(PARAMS, lgb.Dataset(x, label=y, weight=weights), num_boost_round=ROUNDS)


def predict(model, records):
    import numpy as np
    if not records:
        return []
    x, _, _, sizes = matrix(records)
    if list(x.columns) != model.feature_name():
        raise ValueError('current model feature contract changed')
    values = np.asarray(model.predict(x, validate_features=True), dtype=float)
    if values.shape != (sum(sizes),) or not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError('invalid current model output')
    out, offset = [], 0
    for record, size in zip(records, sizes):
        weather = values[offset:offset+size].copy()
        offset += size
        edges = current_calls.bounds(record['row'])
        possible = np.array([hi is None or hi > float(record['row']['rmax_unit']) for _, hi in edges])
        weather = np.where(possible, np.maximum(weather, 1e-6), 0.)
        if weather.sum() <= 0:
            raise ValueError('no possible weather bucket')
        weather /= weather.sum()
        old = np.array([record['incumbent_probs'][bid] for bid in record['band_ids']])
        p = np.where(possible, (1-BLEND)*old+BLEND*weather, 0.)
        p /= p.sum()
        out.append(dict(zip(record['band_ids'], map(float, p))))
    return out


def scores(records, predictions):
    if len(records) != len(predictions):
        raise ValueError('current prediction count mismatch')
    out = []
    for r, p in zip(records, predictions):
        ids, win = r['band_ids'], r['band_ids'][r['winner']]
        q = {'candidate':p, 'incumbent':r['incumbent_probs'], 'market':r['market_probs']}
        row = dict(city=r['city'], date=r['date'], checkpoint=r['checkpoint'],
                   source_identity=r['source_identity'],
                   jump=max(abs(p[k]-r['incumbent_probs'][k]) for k in ids))
        point=float(r['row']['current_max_c'])
        point=point*9/5+32 if r['row']['unit']=='F' else point
        point=math.floor(point+.5)
        lo,hi=current_calls.bounds(r['row'])[r['winner']]
        row['current_point_in_winner']=(lo is None or point>=lo) and (hi is None or point<hi)
        row['ensemble_sd_steps']=float(r['row']['current_models_sd_c'])/(10/9 if r['row']['unit']=='F' else 1.)
        for name, probs in q.items():
            current_calls.distribution(probs, ids)
            row[name+'_hit'] = min(probs, key=lambda k:(-probs[k], k)) == win
            row[name+'_nll'] = -math.log(max(1e-12, probs[win]))
            row[name+'_brier'] = sum((probs[k]-int(k==win))**2 for k in ids)
        out.append(row)
    return out
