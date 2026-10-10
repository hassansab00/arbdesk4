"""WXPredict build, step 2.4: does the venue keep every past trade of these markets?

The training table has each bucket's hourly price (prices-history) but no
volume. Polymarket's data API (`/trades`, the one scripts/ingest_trades.py
reads every hour) is asked for the trades of settled events, and each
bucket's trades are checked against Gamma's own lifetime `volume` for it:
the sum of the trades' sizes (shares) must equal it. A bucket that does is
COMPLETE: every trade the venue counted is in the answer.

THE SAMPLE. EVENTS_PER_MONTH events from every month of
data/training/market_history/events.csv.gz (closed, one winner, the "main"
listing), drawn with random.Random(SEED), so the same events are asked
again on a re-run. A month with fewer events gives all of them.

THE REQUESTS, measured 10 Oct before this was written:
  * Gamma `/events?slug=` gives each bucket's conditionId and `volume`;
  * `/trades?market=<the event's conditionIds>` pages with limit/offset;
    the API refuses an offset over 10,000 (scripts/ingest_trades.py), so an
    event whose answer reaches the cap is asked again bucket by bucket, and
    a bucket that reaches it is asked in halves of its life with `start` and
    `end` (unix seconds, inclusive; one NYC bucket of 20 Sep: 603 + 185 =
    788 trades);
  * one request at a time, PAUSE_S apart.

A BUCKET IS ONE OF THREE: `complete` (sizes sum to Gamma's volume within
TOL), `no_gamma_volume` (Gamma gives no volume, so it cannot be checked), or
`mismatch` (it differs; the ratio is kept, and it runs both ways, so Gamma's
figure is not exact truth either).

AGAINST OUR OWN CAPTURE (`archive`). scripts/ingest_trades.py has stored the
same API's prints every hour, archived in data/archive/trades. For the
sampled events whose whole life falls inside the archive's unbroken span
(ARCHIVE_FROM on), the API is asked again and every trade matched on the
archive's own key (condition, second, price, size, wallet): what the API
serves that we never stored, and what we stored that it no longer serves.

WHAT IT WRITES: data/eval/wxpredict/trade_history_probe.json, the sample's
event ids, every bucket's trade count, size sum and Gamma volume, the
summary, and the archive comparison. Nothing is written to the database.

    python3 tools/wxpredict/trade_history.py            # asks the venue (about 10 min)
    python3 tools/wxpredict/trade_history.py summarise  # the summary again from the file
    python3 tools/wxpredict/trade_history.py archive    # the archive comparison (asks again)
"""
import collections
import csv
import datetime as dt
import gzip
import json
import os
import random
import sys
import time

import requests

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
EVENTS = os.path.join(ROOT, 'data', 'training', 'market_history', 'events.csv.gz')
BANDS = os.path.join(ROOT, 'data', 'training', 'market_history', 'bands.csv.gz')
OUT = os.path.join(ROOT, 'data', 'eval', 'wxpredict', 'trade_history_probe.json')
GAMMA = 'https://gamma-api.polymarket.com/events'
TRADES = 'https://data-api.polymarket.com/trades'
UA = {'User-Agent': 'arbdesk4-wxpredict/1.0', 'Accept': 'application/json'}
EVENTS_PER_MONTH = 10
SEED = 24
PAGE = 1000
MAX_OFFSET = 10000
PAUSE_S = 0.25
TIMEOUT = 30
TOL = 0.01          # shares; Gamma's volume and the sizes are given to 6 decimals
# The archive (data/archive/trades) is unbroken from its 27 Sep 22:34Z file on;
# an event dated ARCHIVE_EVENTS_FROM or later was created inside it.
ARCHIVE_FROM = dt.datetime(2026, 9, 27, 22, 40, tzinfo=dt.timezone.utc)
ARCHIVE_EVENTS_FROM = '2026-10-01'
ARCHIVE_EVENTS_TO = '2026-10-06'


class Session:
    def __init__(self):
        self.s = requests.Session()
        self.requests = 0
        self.refused = collections.Counter()

    def get(self, url, params):
        for attempt in range(4):
            time.sleep(PAUSE_S)
            self.requests += 1
            r = self.s.get(url, params=params, headers=UA, timeout=TIMEOUT)
            if r.status_code == 429 or r.status_code >= 500:
                self.refused[r.status_code] += 1
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f'{url} refused 4 times: {dict(self.refused)}')


def pages(sess, params):
    """Every trade for `params`, or (trades, False) when the offset cap stops it."""
    out, off = [], 0
    while True:
        js = sess.get(TRADES, {**params, 'limit': PAGE, 'offset': off})
        out += js
        if len(js) < PAGE:
            return out, True
        off += PAGE
        if off > MAX_OFFSET:
            return out, False


def bucket_trades(sess, cid, lo, hi, depth=0):
    """Every trade of one bucket between lo and hi (unix s), halving on the cap."""
    got, whole = pages(sess, {'market': cid, 'start': lo, 'end': hi})
    if whole or depth >= 12 or hi - lo < 2:
        return got, whole
    mid = (lo + hi) // 2
    a, wa = bucket_trades(sess, cid, lo, mid, depth + 1)
    b, wb = bucket_trades(sess, cid, mid + 1, hi, depth + 1)
    return a + b, wa and wb


def sample():
    ev = [e for e in csv.DictReader(gzip.open(EVENTS, 'rt'))
          if e['closed'] == '1' and e['listing'] == 'main']
    winners = collections.Counter(b['event_id'] for b in csv.DictReader(gzip.open(BANDS, 'rt'))
                                  if b['winner'] == '1')
    ev = [e for e in ev if winners[e['event_id']] == 1]
    by_month = collections.defaultdict(list)
    for e in sorted(ev, key=lambda x: (x['date'], x['event_id'])):
        by_month[e['date'][:7]].append(e)
    rng = random.Random(SEED)
    picked = []
    for m in sorted(by_month):
        pool = by_month[m]
        picked += pool if len(pool) <= EVENTS_PER_MONTH else rng.sample(pool, EVENTS_PER_MONTH)
    return picked, {m: len(v) for m, v in sorted(by_month.items())}


def kind(b):
    if b['complete']:
        return 'complete'
    return 'no_gamma_volume' if not b['gamma_volume'] else 'mismatch'


def summarise(rows, months):
    buckets = [b for r in rows for b in r['buckets']]
    per_month = {}
    for r in rows:
        m = per_month.setdefault(r['date'][:7], {'events': 0, 'buckets': 0, 'trades': 0, 'buckets_with_no_trade': 0,
                                                 'complete': 0, 'no_gamma_volume': 0, 'mismatch': 0})
        m['events'] += 1
        m['trades'] += r['trades']
        for b in r['buckets']:
            m['buckets'] += 1
            m[kind(b)] += 1
            m['buckets_with_no_trade'] += b['trades'] == 0
    ratios = sorted(b['size'] / b['gamma_volume'] for b in buckets if kind(b) == 'mismatch')
    gv = sum(b['gamma_volume'] for b in buckets)
    return {
        'events': len(rows), 'buckets': len(buckets),
        'kinds': dict(collections.Counter(kind(b) for b in buckets)),
        'mismatch_ratio': {'n': len(ratios), 'min': ratios[0] if ratios else None,
                           'median': ratios[len(ratios) // 2] if ratios else None,
                           'max': ratios[-1] if ratios else None,
                           'above_1': sum(x > 1 for x in ratios)},
        'mismatch_net_shares_short': round(sum(b['gamma_volume'] - b['size'] for b in buckets
                                               if kind(b) == 'mismatch'), 2),
        'gamma_volume_shares': round(gv, 2),
        'trades': sum(r['trades'] for r in rows),
        'events_asked_by_bucket': sum(r['asked_by'] == 'bucket' for r in rows),
        'trades_before_created': sum(r['before_created'] for r in rows),
        'trades_after_day_plus_1': sum(r['after_day_plus_1'] for r in rows),
        'per_month': per_month, 'events_in_record_per_month': months,
        'priors': {'events_per_month': EVENTS_PER_MONTH, 'seed': SEED, 'page': PAGE, 'max_offset': MAX_OFFSET,
                   'pause_s': PAUSE_S, 'tol_shares': TOL},
    }


def key_api(t):
    return (t['conditionId'], int(t['timestamp']), round(float(t['price']), 6), round(float(t['size']), 6),
            t['proxyWallet'].lower())


def archive(doc):
    """Every trade the API serves for the sampled events inside the archive's span, against the archive."""
    import glob
    evs = [e for e in doc['events'] if ARCHIVE_EVENTS_FROM <= e['date'] <= ARCHIVE_EVENTS_TO]
    cids = {b['condition_id'] for e in evs for b in e['buckets']}
    stored = set()
    for f in sorted(glob.glob(os.path.join(ROOT, 'data', 'archive', 'trades', '*.csv.gz'))):
        for r in csv.DictReader(gzip.open(f, 'rt')):
            if r['condition_id'] in cids:
                t = int(dt.datetime.fromisoformat(r['traded_at']).timestamp())
                stored.add((r['condition_id'], t, round(float(r['price']), 6), round(float(r['size']), 6),
                            r['proxy_wallet'].lower()))
    sess = Session()
    keys = []
    for e in evs:
        got, whole = pages(sess, {'market': ','.join(b['condition_id'] for b in e['buckets'])})
        if not whole:
            raise RuntimeError(f"{e['slug']}: the offset cap; ask it bucket by bucket")
        keys += [key_api(t) for t in got]
    start = ARCHIVE_FROM.timestamp()
    inside = [k for k in keys if k[1] >= start]
    missed = [k for k in inside if k not in stored]
    distinct = set(keys)
    by_minute = collections.defaultdict(lambda: [0, 0])
    for k in inside:
        m = dt.datetime.fromtimestamp(k[1], dt.timezone.utc).minute // 10 * 10
        by_minute[m][1] += 1
        by_minute[m][0] += k not in stored
    return {
        'asked_on': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'events': [e['slug'] for e in evs], 'archive_from': ARCHIVE_FROM.isoformat(),
        'api_trades': len(keys), 'api_trades_inside': len(inside),
        'api_trades_sharing_a_key': len(keys) - len(distinct),
        'never_stored': len(missed), 'never_stored_share': round(len(missed) / len(inside), 4) if inside else None,
        'stored': len(stored), 'stored_no_longer_served': len(stored - distinct),
        'never_stored_by_minute_of_hour': {f'{m:02d}-{m + 9:02d}': {'missed': v[0], 'all': v[1]}
                                           for m, v in sorted(by_minute.items())},
        'requests': sess.requests,
    }


def main():
    t0 = time.time()
    asked_on = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
    sess = Session()
    picked, months = sample()
    rows, problems = [], []
    for e in picked:
        g = sess.get(GAMMA, {'slug': e['slug']})
        if not g:
            problems.append({'event_id': e['event_id'], 'why': 'gamma has no event for the slug'})
            continue
        ms = g[0].get('markets') or []
        vol = {m['conditionId']: float(m.get('volume') or 0) for m in ms}
        trades, whole = pages(sess, {'market': ','.join(vol)})
        how = 'event'
        if not whole:
            how, trades = 'bucket', []
            created = int(dt.datetime.fromisoformat(e['created_at'].replace('Z', '+00:00')).timestamp())
            end = int(dt.datetime.fromisoformat(e['date']).replace(tzinfo=dt.timezone.utc).timestamp()) + 4 * 86400
            for cid in vol:
                got, w = bucket_trades(sess, cid, created - 86400, end)
                trades += got
                if not w:
                    problems.append({'event_id': e['event_id'], 'condition_id': cid, 'why': 'cap reached at depth 12'})
        n = collections.Counter(t['conditionId'] for t in trades)
        size = collections.defaultdict(float)
        for t in trades:
            size[t['conditionId']] += float(t['size'])
        created = dt.datetime.fromisoformat(e['created_at'].replace('Z', '+00:00')).timestamp()
        day_end = dt.datetime.fromisoformat(e['date']).replace(tzinfo=dt.timezone.utc).timestamp() + 2 * 86400
        ts = [t['timestamp'] for t in trades]
        rows.append({
            'event_id': e['event_id'], 'slug': e['slug'], 'date': e['date'], 'unit': e['unit'], 'asked_by': how,
            'buckets': [{'condition_id': c, 'trades': n[c], 'size': round(size[c], 6), 'gamma_volume': vol[c],
                         'complete': abs(size[c] - vol[c]) <= TOL} for c in vol],
            'trades': len(trades),
            'first_trade': min(ts) if ts else None, 'last_trade': max(ts) if ts else None,
            'before_created': sum(1 for x in ts if x < created),
            'after_day_plus_1': sum(1 for x in ts if x > day_end),
        })
        print(f"{e['date']} {e['slug'][:60]:60s} {len(trades):6d} trades, "
              f"{sum(b['complete'] for b in rows[-1]['buckets'])}/{len(vol)} buckets complete ({how})",
              file=sys.stderr)
    summary = summarise(rows, months)
    summary.update({'requests': sess.requests, 'refused': dict(sess.refused), 'asked_on': asked_on,
                    'seconds': round(time.time() - t0, 1), 'problems': problems})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, 'w') as f:
        json.dump({'summary': summary, 'events': rows}, f, indent=1, sort_keys=True)
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'probe'
    if cmd == 'probe':
        main()
    else:
        with open(OUT) as f:
            doc = json.load(f)
        if cmd == 'summarise':
            keep = {k: doc['summary'].get(k) for k in ('requests', 'refused', 'seconds', 'problems', 'asked_on')}
            doc['summary'] = {**summarise(doc['events'], doc['summary']['events_in_record_per_month']), **keep}
        elif cmd == 'archive':
            doc['archive'] = archive(doc)
        else:
            sys.exit(f'unknown command {cmd}')
        with open(OUT, 'w') as f:
            json.dump(doc, f, indent=1, sort_keys=True)
        print(json.dumps(doc['summary'] if cmd == 'summarise' else doc['archive'], indent=1))
