"""WXPredict build, step 2.1: what the price record's `p` is. Each archived book snapshot
(data/archive/books, every 2 h at odd UTC hours, ~hh:25) is matched to the
record's hourly points for its bucket (data/training/market_history/prices,
stamped 0-59 s past the hour): p at the snapshot's own hour (hh:00) and at the
next (hh+1:00). Candidates: the book's mid, best bid, best ask. The control:
hours where p did not move between hh:00 and hh+1:00. Then the tick's own
books (data/mirror/prediction_checkpoints: bid, ask and last at ~hh:36) under the
same control, to test the last trade too.

    python3 tools/wxpredict/what_is_p.py      # reads committed files only
"""
import collections
import csv
import glob
import gzip
import os
import statistics
import sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
tok = {}
for f in sorted(glob.glob(f'{ROOT}/data/mirror/bands/*.csv.gz')):
    for r in csv.DictReader(gzip.open(f, 'rt')):
        if r['token_yes']:
            tok[r['band_id']] = r['token_yes']
ev = {}
for r in csv.DictReader(gzip.open(f'{ROOT}/data/training/market_history/bands.csv.gz', 'rt')):
    ev[r['token_yes']] = (r['event_id'], int(r['band_index']))
snaps = {}
for f in sorted(glob.glob(f'{ROOT}/data/archive/books/*.csv.gz')):
    for r in csv.DictReader(gzip.open(f, 'rt')):
        k = ev.get(tok.get(r['band_id']))
        if k is None:
            continue
        snaps[(r['band_id'], r['observed_at'])] = (k, r)
import datetime as dt
def ts(s): return dt.datetime.fromisoformat(s).timestamp()
want = collections.defaultdict(set)
rows = []
for (bid, at), (k, r) in snaps.items():
    t = ts(at); h0 = int(t // 3600 * 3600)
    want[k].update((h0, h0 + 3600))
    rows.append((k, t, h0, r))
print('snapshots matched to the record:', len(rows), 'buckets:', len(want), file=sys.stderr)
pts = {}
for r in csv.DictReader(gzip.open(f'{ROOT}/data/training/market_history/prices.csv.gz', 'rt')):
    k = (r['event_id'], int(r['band_index']))
    hs = want.get(k)
    if not hs:
        continue
    t = int(r['t']); h = t // 3600 * 3600
    if h in hs and t - h < 60:
        pts[(k, h)] = float(r['p'])
def f(x):
    try: return float(x)
    except: return None
res = collections.defaultdict(list); res_stable = collections.defaultdict(list); n = n_stable = 0
for k, t, h0, r in rows:
    p0, p1 = pts.get((k, h0)), pts.get((k, h0 + 3600))
    bid, ask = f(r['best_bid']), f(r['best_ask'])
    if p0 is None or bid is None or ask is None:
        continue
    n += 1
    cands = {'mid': (bid + ask) / 2, 'bid': bid, 'ask': ask}
    stable = p1 is not None and p1 == p0
    n_stable += stable
    for name, v in cands.items():
        res[name].append(abs(p0 - v))
        if stable:
            res_stable[name].append(abs(p0 - v))
def summ(xs):
    xs = sorted(xs); q = lambda a: xs[min(len(xs) - 1, int(a * len(xs)))]
    return {'n': len(xs), 'median': round(statistics.median(xs), 4), 'p90': round(q(0.9), 4), 'max': round(xs[-1], 4),
            'within_0.005': round(sum(x <= 0.005 for x in xs) / len(xs), 4), 'within_0.01': round(sum(x <= 0.01 for x in xs) / len(xs), 4)}
print('two-sided snapshots with p at hh:00:', n, '; of them p unchanged hh:00 -> hh+1:00:', n_stable)
for name in ('mid', 'bid', 'ask'):
    print(name, 'all   ', summ(res[name]))
    print(name, 'stable', summ(res_stable[name]))

# the venue's display rule: the midpoint unless the spread is over 0.10
by = collections.defaultdict(list)
for k, t, h0, r in rows:
    p0, p1 = pts.get((k, h0)), pts.get((k, h0 + 3600))
    bid, ask = f(r['best_bid']), f(r['best_ask'])
    if p0 is None or bid is None or ask is None or p1 != p0:
        continue
    wide = (ask - bid) > 0.10 + 1e-9
    by[wide].append(abs(p0 - (bid + ask) / 2))
for wide, xs in sorted(by.items()):
    print('stable, spread', '> 0.10' if wide else '<= 0.10', summ(xs))

import json
# The tick's books (bid, ask and last at decided_at, ~hh:36), the same control.
obs = []
want = collections.defaultdict(set)
for f in sorted(glob.glob(f'{ROOT}/data/mirror/prediction_checkpoints/*.csv.gz')):
    for r in csv.DictReader(gzip.open(f, 'rt')):
        m = json.loads(r['market'] or '{}')
        t = dt.datetime.fromisoformat(r['decided_at']).timestamp(); h0 = int(t // 3600 * 3600)
        for bid_, q in m.items():
            k = ev.get(tok.get(bid_))
            if k is None or not q: continue
            b, a, l = q.get('bid'), q.get('ask'), q.get('last')
            if b is None or a is None or l is None: continue
            obs.append((k, h0, float(b), float(a), float(l))); want[k].update((h0, h0 + 3600))
pts2 = {}
for r in csv.DictReader(gzip.open(f'{ROOT}/data/training/market_history/prices.csv.gz', 'rt')):
    k = (r['event_id'], int(r['band_index'])); hs = want.get(k)
    if not hs: continue
    t = int(r['t']); h = t // 3600 * 3600
    if h in hs and t - h < 60: pts2[(k, h)] = float(r['p'])
res = collections.defaultdict(lambda: collections.defaultdict(list))
for k, h0, b, a, l in obs:
    p0, p1 = pts2.get((k, h0)), pts2.get((k, h0 + 3600))
    if p0 is None or p1 != p0: continue
    wide = 'wide' if a - b > 0.10 + 1e-9 else 'narrow'
    res[wide]['mid'].append(abs(p0 - (a + b) / 2)); res[wide]['last'].append(abs(p0 - l))
for w in ('narrow', 'wide'):
    for c in ('mid', 'last'):
        print('tick books', w, c, summ(res[w][c]) if res[w][c] else 'none')
