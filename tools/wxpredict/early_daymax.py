"""Offline, shared pre-day winning-bucket development experiment.

This is archive reconstruction, NOT a frozen production call or promotion test.
No serving imports, database writes, scheduled job or model registry changes.
Forecast publication times follow features.py's historical availability assumptions;
they are not provider receipt proofs. Labels are venue buckets, never invented
temperatures. Every input to the classifier is explicitly allowlisted below.
"""
import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from wxpredict import common, features as feat

RECIPE = 'pre-day-band-development-v1'
SEED, ROUNDS = 20261031, 120
PARAMS = dict(objective='binary', learning_rate=.03, num_leaves=15,
              min_data_in_leaf=200, lambda_l2=20., feature_fraction=.8,
              bagging_fraction=.8, bagging_freq=1, num_threads=2, verbose=-1,
              deterministic=True, force_col_wise=True, seed=SEED)
NUMERIC = (
    'doy_sin', 'doy_cos', 'fc_bm_tmax_c', 'fc_models_n',
    'fc_models_mean_c', 'fc_models_sd_c', 'fc_models_min_c', 'fc_models_max_c',
    *(f'fc_{m}_c' for m in feat.MODELS),
    'fc_bm_cloud_09_17', 'fc_bm_sw_06_17', 'fc_bm_wind_09_17',
    'fc_bm_precip_00_17', 'fc_bm_td_09_17', 'fch_n_known', 'fch_day_max_c',
    'fch_peak_local_hour', 'fch_rest_cloud', 'fch_rest_sw', 'fch_rest_wind',
    'fch_rest_precip', 'bias7_bm_c', 'bias30_bm_c', 'mae30_bm_c',
    'bias30_models_c', 'bias_n30', 'clim_mean_c', 'clim_sd_c', 'clim_n',
    'clim_peak_hour',
)
CATEGORICAL = ('city_key', 'station', 'unit', 'fc_daily_source')


def anchor(row):
    """Fixed ensemble mean, falling back to best-match; no winner selection."""
    for key in ('fc_models_mean_c', 'fc_bm_tmax_c'):
        x = row.get(key)
        if x is not None and math.isfinite(float(x)):
            return float(x)
    raise ValueError('no finite pre-day forecast anchor')


def validate(record):
    row = record['row']
    local = dt.datetime.fromisoformat(row['decision_local'])
    day = dt.date.fromisoformat(row['date'])
    if (local.utcoffset() is None or local.date() != day-dt.timedelta(days=1)
            or (local.hour, local.minute, local.second) != (18, 1, 0)
            or int(local.timestamp()) != row['decision_utc']
            or row['day_offset'] != -1 or row['local_hour'] != 18):
        raise ValueError('not the reconstructed pre-day 18:01 local checkpoint')
    available = row.get('event_available_from')
    if not isinstance(available, (int, float)) or not math.isfinite(available) or available > row['decision_utc']:
        raise ValueError('venue event not known to be open at the checkpoint')
    if row['unit'] not in ('C', 'F') or not row['station']:
        raise ValueError('unknown unit or station')
    bands = record['bands']
    if (len(bands) < 2 or bands[0][0] is not None or bands[-1][1] is not None
            or any(lo is not None and (not math.isfinite(lo) or hi is not None and lo >= hi)
                   or hi is not None and not math.isfinite(hi) for lo, hi in bands)
            or any(a[1] is None or a[1] != b[0] for a, b in zip(bands, bands[1:]))):
        raise ValueError('invalid contiguous venue ladder')
    if not isinstance(record['winner'], int) or not 0 <= record['winner'] < len(bands):
        raise ValueError('winner outside venue ladder')
    anchor(row)
    market = record.get('market')
    if market is not None and (len(market) != len(bands)
            or any(not math.isfinite(p) or p < 0 or p > 1 for p in market)
            or abs(sum(market)-1) > 1e-8):
        raise ValueError('invalid matched market distribution')


def matrix(records, labels=False):
    import numpy as np
    import pandas as pd
    for r in records:
        validate(r)
    rows = [r['row'] for r in records]
    base = pd.DataFrame({key: pd.to_numeric([r.get(key) for r in rows], errors='raise')
                         for key in NUMERIC})
    for key in CATEGORICAL:
        base[key] = pd.Series([str(r.get(key) or 'unknown') for r in rows], dtype='category')
    selected, extras, targets, weights, sizes = [], [], [], [], []
    for i, record in enumerate(records):
        row, bands = record['row'], record['bands']
        point = anchor(row)
        point = point*9/5+32 if row['unit'] == 'F' else point
        step = 2. if row['unit'] == 'F' else 1.
        sizes.append(len(bands))
        for j, (lo, hi) in enumerate(bands):
            selected.append(i)
            extras.append(dict(band_lo_from_forecast=(lo-point)/step if lo is not None else np.nan,
                               band_hi_from_forecast=(hi-point)/step if hi is not None else np.nan,
                               band_open_low=int(lo is None), band_open_high=int(hi is None),
                               band_count=len(bands)))
            if labels:
                targets.append(int(j == record['winner']))
                weights.append(1./len(bands))
    return pd.concat([base.iloc[selected].reset_index(drop=True), pd.DataFrame(extras)], axis=1), targets, weights, sizes


def fit(records):
    import lightgbm as lgb
    x, y, weights, _ = matrix(records, labels=True)
    return lgb.train(PARAMS, lgb.Dataset(x, label=y, weight=weights), num_boost_round=ROUNDS)


def predict(model, records):
    import numpy as np
    x, _, _, sizes = matrix(records)
    if list(x.columns) != model.feature_name():
        raise ValueError('pre-day model feature contract changed')
    values = np.asarray(model.predict(x, validate_features=True), dtype=float)
    if values.shape != (sum(sizes),) or not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
        raise ValueError('invalid pre-day model output')
    out, offset = [], 0
    for n in sizes:
        p = np.maximum(values[offset:offset+n], 1e-6)
        out.append((p/p.sum()).tolist())
        offset += n
    return out


def windows(records, fit_last, first, last):
    dates = [dt.date.fromisoformat(x) for x in (fit_last, first, last)]
    if not dates[0] < dates[1] <= dates[2]:
        raise ValueError('training must end before evaluation begins')
    identities = set()
    for r in records:
        validate(r)
        key = (r['row']['city_key'], r['row']['date'])
        if key in identities:
            raise ValueError('duplicate city-date; cannot overweight a second listing')
        identities.add(key)
    train = [r for r in records if r['row']['date'] <= fit_last]
    test = [r for r in records if first <= r['row']['date'] <= last]
    if len({r['row']['date'] for r in train}) < 20 or len({r['row']['date'] for r in test}) < 7:
        raise ValueError('insufficient chronological development dates (20 train, 7 evaluation)')
    return train, test


def summarize(records, predictions):
    if len(records) != len(predictions):
        raise ValueError('prediction count mismatch')
    paired = [(r, p) for r, p in zip(records, predictions) if r.get('market') is not None]

    def metrics(pairs, name):
        if not pairs:
            return dict(n=0, hits=0, hit_rate=None, log_loss=None, brier=None)
        hits, losses, briers = 0, [], []
        for r, predicted in pairs:
            p = predicted if name == 'candidate' else r['market']
            if len(p) != len(r['bands']) or any(not math.isfinite(x) or x < 0 or x > 1 for x in p) or abs(sum(p)-1) > 1e-8:
                raise ValueError('invalid scored distribution')
            win = r['winner']
            hits += max(range(len(p)), key=p.__getitem__) == win
            losses.append(-math.log(max(1e-12, p[win])))
            briers.append(sum((q-int(j == win))**2 for j, q in enumerate(p)))
        return dict(n=len(pairs), hits=hits, hit_rate=hits/len(pairs),
                    log_loss=sum(losses)/len(pairs), brier=sum(briers)/len(pairs))

    raw_hits = 0
    for r in records:
        point = anchor(r['row'])
        point = point*9/5+32 if r['row']['unit'] == 'F' else point
        raw_hits += feat.bucket_of(math.floor(point+.5), r['bands']) == r['winner']
    return dict(n=len(records), dates=len({r['row']['date'] for r in records}),
                cities=len({r['row']['city_key'] for r in records}),
                candidate=metrics(list(zip(records, predictions)), 'candidate'),
                raw_ensemble_point=dict(hits=raw_hits, hit_rate=raw_hits/len(records) if records else None),
                matched_market=dict(n=len(paired), dates=len({r['row']['date'] for r, _ in paired}),
                                    candidate=metrics(paired, 'candidate'), market=metrics(paired, 'market')))


def build_records(first, last):
    """One reconstructed pre-day row per closed venue event. Never load D0 reports."""
    from wxpredict import build_table as bt
    old_read = common.READ
    common.READ = {}
    try:
        sdaily, refused = bt.load_station_daily()
        events, left, _ = bt.load_events(set(sdaily), first, last)
        starts = {r['event_id']: r.get('created_at') for r in common.market_rows('events')}
        bm, md = bt.load_daily_forecasts()
        hourly = bt.load_hourly_forecasts()
        prices = bt.load_prices(events)
        cities, records = common.cities(), []
        dropped = collections.Counter()
        with feat.report_lag(feat.REPORT_LAG_V2_S):
            for e in events:
                day = dt.date.fromisoformat(e['date'])
                tz = common.zone(cities[e['city']])
                local = dt.datetime.combine(day-dt.timedelta(days=1), dt.time(18, 1), tz)
                t = int(local.timestamp())
                try:
                    start = dt.datetime.fromisoformat(starts.get(e['event_id']) or '')
                    if start.utcoffset() is None:
                        raise ValueError('event start has no timezone')
                    available = start.timestamp()
                except (ValueError, TypeError):
                    dropped['missing or invalid event start'] += 1
                    continue
                if available > t:
                    dropped['event opens after checkpoint'] += 1
                    continue
                doy = day.timetuple().tm_yday
                row = dict(event_id=e['event_id'], city_key=e['city'], station=e['station'],
                           date=e['date'], unit=e['unit'], day_offset=-1, local_hour=18,
                           decision_local=local.isoformat(timespec='minutes'), decision_utc=t,
                           event_available_from=available,
                           doy_sin=math.sin(2*math.pi*doy/365.25), doy_cos=math.cos(2*math.pi*doy/365.25))
                days = sdaily.get(e['station'], {})
                feat.fc_features(row, e['city'], day, t, tz, bm, md, hourly, feat.Reports([]))
                feat.bias_features(row, e['city'], e['station'], day, t, tz, days, bm, md)
                row['clim_mean_c'], row['clim_sd_c'], row['clim_n'], row['clim_peak_hour'] = feat.climatology(days, day)
                try:
                    anchor(row)
                except ValueError:
                    dropped['missing pre-day forecast anchor'] += 1
                    continue
                complete, _, market, *_ = feat.implied(feat.ladder_at(prices[e['event_id']], t), e['bands'])
                r = dict(row=row, bands=e['bands'], winner=e['winner'], market=market if complete else None)
                validate(r)
                records.append(r)
        return records, dict(event_exclusions=left, feature_exclusions=dict(dropped),
                             refused_station_days=sum(len(x) for x in refused.values()),
                             sources=common.sources(common.READ))
    finally:
        common.READ = old_read


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--from-date', required=True)
    ap.add_argument('--fit-last', required=True)
    ap.add_argument('--eval-first', required=True)
    ap.add_argument('--eval-last', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args(argv)
    if not dt.date.fromisoformat(args.from_date) <= dt.date.fromisoformat(args.fit_last) < dt.date.fromisoformat(args.eval_first) <= dt.date.fromisoformat(args.eval_last):
        raise ValueError('invalid chronological windows')
    records, data = build_records(args.from_date, args.eval_last)
    train, test = windows(records, args.fit_last, args.eval_first, args.eval_last)
    model = fit(train)
    predictions = predict(model, test)
    result = dict(recipe=RECIPE, evidence='reconstructed historical development; previously inspected dates',
                  checkpoint='18:01 on the preceding city-local day; not the production 18:37 receipt',
                  publication_proof='historical provider schedule assumptions, not actual receipt timestamps',
                  changes_serving=False, eligible_for_promotion=False,
                  training=dict(first=args.from_date, last=args.fit_last, rows=len(train),
                                dates=len({r['row']['date'] for r in train})),
                  evaluation=[args.eval_first, args.eval_last], seed=SEED, rounds=ROUNDS,
                  all=summarize(test, predictions),
                  by_city={city:summarize([r for r in test if r['row']['city_key'] == city],
                                         [p for r, p in zip(test, predictions) if r['row']['city_key'] == city])
                           for city in sorted({r['row']['city_key'] for r in test})},
                  data=data,
                  records_sha256=hashlib.sha256(json.dumps(records, sort_keys=True, allow_nan=False).encode()).hexdigest(),
                  model_sha256=hashlib.sha256(model.model_to_string().encode()).hexdigest(),
                  source_sha256={name:hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                                 for name in ('early_daymax.py', 'features.py', 'common.py', 'build_table.py')})
    dest = Path(args.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(result, indent=2, allow_nan=False)+'\n')
    print(json.dumps({k:v for k, v in result.items() if k not in ('data', 'by_city')}, indent=2))
    return result


if __name__ == '__main__':
    main()
