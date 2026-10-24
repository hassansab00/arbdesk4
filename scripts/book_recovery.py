"""Recover missing or stale full books before the existing edge calculation.

The scheduled collector stays active. This bounded second pass only requests
current priceable bands that need repair and appends confirmed source reads.
"""
import datetime as dt
import json
import math
import time

from common import _post, insert, log_run, request_deadline, rest
from market_state import classify

JOB = 'book_recovery'
URL = 'https://clob.polymarket.com/books'
BUDGET_S = 40
MAX_BANDS = 200
BATCH_TOKENS = 80


def levels(book, side):
    raw = book.get(side)
    if not isinstance(raw, list):
        raise ValueError('missing order-book side')
    out = []
    for level in raw:
        price, size = float(level['price']), float(level['size'])
        if not math.isfinite(price) or not math.isfinite(size) or not 0 < price < 1 or size <= 0:
            raise ValueError('invalid price or size')
        out.append(dict(price=price, size=size))
    return sorted(out, key=lambda x: x['price'], reverse=side == 'bids')


def checked(book, token, condition):
    if str(book.get('asset_id')) != str(token) or str(book.get('market')).lower() != str(condition).lower():
        raise ValueError('order-book identity mismatch')
    bids, asks = levels(book, 'bids'), levels(book, 'asks')
    if not bids and not asks:
        raise ValueError('no executable book')
    if bids and asks and bids[0]['price'] > asks[0]['price']:
        raise ValueError('crossed order book')
    return dict(book, bids=bids, asks=asks)


def depth(values, side):
    touch = values[0]['price'] if values else 0
    out = {f'c{n}': 0.0 for n in (1, 2, 5, 10, 25)}
    out['total'] = 0.0
    for level in values:
        distance = (level['price']-touch) if side == 'asks' else (touch-level['price'])
        usd = level['price']*level['size']
        out['total'] += usd
        for cents in (1, 2, 5, 10, 25):
            if distance <= cents/100+1e-9:
                out[f'c{cents}'] += usd
    return {key: round(value, 6) for key, value in out.items()}


def snapshot(band, yes, no, received):
    bids, asks = yes['bids'], yes['asks']
    bid = bids[0]['price'] if bids else None
    ask = asks[0]['price'] if asks else None
    state = classify(bid, ask)
    row = dict(band_id=band['band_id'], observed_at=received.isoformat(),
        snapshot_hour_utc=received.hour, best_bid=bid, best_ask=ask,
        mid=round((bid+ask)/2, 6) if bid is not None and ask is not None else None,
        spread=round(ask-bid, 6) if bid is not None and ask is not None else None,
        market_state=state, tradeable=state in ('LIVE', 'WIDE'),
        bid_levels=len(bids), ask_levels=len(asks), raw_book=yes,
        no_best_bid=None, no_best_ask=None, no_book=None)
    for side, values in (('bid', bids), ('ask', asks)):
        tiers = depth(values, side+'s')
        row[side+'_total_usd'] = tiers['total']
        for cents in (1, 2, 5, 10, 25):
            row[f'{side}_usd_{cents}c'] = tiers[f'c{cents}']
    if no:
        nb, na = no['bids'], no['asks']
        row['no_best_bid'] = nb[0]['price'] if nb else None
        row['no_best_ask'] = na[0]['price'] if na else None
        row['no_book'] = dict(no, observed_at=received.isoformat(),
            best_bid=row['no_best_bid'], best_ask=row['no_best_ask'],
            market_state=classify(row['no_best_bid'], row['no_best_ask']),
            bid_levels=len(nb), ask_levels=len(na),
            ask_usd=depth(na, 'asks'), bid_usd=depth(nb, 'bids'))
    return row


def recover(bands, books, now, max_age, budget_s=BUDGET_S):
    """Return only rows whose append was acknowledged; failures stay visible."""
    started = time.monotonic()
    deadline = started+budget_s
    try:
        # The normalized latest-book view intentionally omits NO columns.
        # Read only its exact snapshot IDs, never the whole book history.
        by_id = {str(row['snapshot_id']): row for row in books.values()
                 if row.get('snapshot_id') is not None and 'no_book' not in row}
        ids = sorted(by_id)
        with request_deadline(deadline):
            for offset in range(0, len(ids), 100):
                got_no = rest('book_snapshots', [('select','snapshot_id,no_book'),
                    ('snapshot_id','in.('+','.join(ids[offset:offset+100])+')')])
                for row in got_no:
                    if str(row['snapshot_id']) in by_id:
                        by_id[str(row['snapshot_id'])]['no_book'] = row.get('no_book')
    except Exception as exc:
        detail = dict(scope='priceable bands', recovered=0,error=type(exc).__name__+': '+str(exc)[:120])
        log_run('book_recovery', 'attention', 0, detail)
        return [], detail
    def at(row):
        try:
            return dt.datetime.fromisoformat(str(row.get('observed_at')).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    def needs(band):
        book = books.get(band['band_id'], {})
        stamp = at(book)
        return (not book or stamp.tzinfo is None or not dt.timedelta(0) <= now-stamp <= max_age
                or bool(band.get('token_no') and not book.get('no_book')))
    pending = sorted([b for b in bands if needs(b)], key=lambda b: (str(books.get(b['band_id'], {}).get('observed_at') or ''), b['band_id']))
    selected = pending[:MAX_BANDS]
    detail = dict(scope='priceable bands', needed=len(pending), selected=len(selected),
                  deferred=len(pending)-len(selected), recovered=0, failed=0, errors=[])
    expected = {}
    for band in selected:
        for key in ('token_yes', 'token_no'):
            if band.get(key) and band.get('condition_id'):
                expected[str(band[key])] = band['condition_id']
    got, received = {}, {}
    try:
        with request_deadline(deadline):
            # Retry only missing tokens, never replay a successful response.
            for attempt in range(2):
                missing = sorted(set(expected)-set(got))
                for offset in range(0, len(missing), BATCH_TOKENS):
                    if deadline-time.monotonic() < 5:
                        break
                    tokens = missing[offset:offset+BATCH_TOKENS]
                    try:
                        response = _post(URL, headers={'Content-Type':'application/json','User-Agent':'ArbDesk4 book recovery'},
                            data=json.dumps([{'token_id':token} for token in tokens]),
                            timeout=min(8, (deadline-time.monotonic()-3)/2))
                        response.raise_for_status()
                        data = response.json()
                        if not isinstance(data, list):
                            raise ValueError('invalid batch order-book response')
                        stamp = dt.datetime.now(dt.timezone.utc)
                        seen = set()
                        for book in data:
                            token = str(book.get('asset_id'))
                            if token not in tokens or token in seen:
                                raise ValueError('unrequested or duplicate asset')
                            seen.add(token)
                        for book in data:
                            token = str(book['asset_id'])
                            try:
                                got[token] = checked(book, token, expected[token])
                                received[token] = stamp
                            except (ValueError, TypeError, KeyError):
                                continue
                    except Exception as exc:
                        detail['errors'].append(type(exc).__name__+': '+str(exc)[:120])
            rows = []
            for band in selected:
                yes = got.get(str(band.get('token_yes')))
                no = got.get(str(band.get('token_no')))
                if yes and (not band.get('token_no') or no):
                    stamp = max(received[str(band['token_yes'])], received.get(str(band.get('token_no')), received[str(band['token_yes'])]))
                    rows.append(snapshot(band, yes, no, stamp))
            if rows:
                insert('book_snapshots', rows)
            detail['recovered'] = len(rows)
    except Exception as exc:
        rows = []  # An unacknowledged write cannot repair the in-memory book.
        detail['errors'].append(type(exc).__name__+': '+str(exc)[:120])
    detail['failed'] = len(selected)-len(rows)
    detail['seconds'] = round(time.monotonic()-started, 2)
    log_run('book_recovery', 'attention' if detail['failed'] or detail['deferred'] else 'ok', len(rows), detail)
    return rows, detail
