"""Public Data API v2 trade tape, with explicit cursor and time-window proof.

Condition feeds ignore start/end on the server. Filter locally, preserve the
same condition cohort on every page, and never treat a short page as final.
Rows retain the established v1 field names internally so archived research
and the database's original deduplication identity remain compatible.
"""
import math
import time

import requests

from common import _retry_after

API = 'https://data-api.polymarket.com/v2/trades'
CONDITIONS = 20
PAGE = 1000
UA = {'User-Agent': 'arbdesk4-trades/2.0', 'Accept': 'application/json'}


class Tape(list):
    def __init__(self, rows=(), *, cursor=None, continuation=None):
        super().__init__(rows)
        self.cursor = cursor
        self.continuation = continuation


def normalize(row):
    """One taker fill keeps its original token, wallet and transaction hash."""
    if not isinstance(row, dict):
        raise ValueError('trade row is not an object')
    r = dict(row)
    for old, new in [('asset', 'token_id'), ('conditionId', 'condition_id'),
                     ('proxyWallet', 'proxy_wallet'), ('transactionHash', 'transaction_hash')]:
        if new in row:
            r[old] = row[new]
    stamp = float(r['timestamp'])
    if not math.isfinite(stamp):
        raise ValueError('trade timestamp is not finite')
    return r


def walk(ids, lower=None, upper=None, *, cursor=None, get=requests.get,
         fetch=None, wait_until=None, stats=None, sleep=time.sleep,
         clock=time.monotonic, page_size=PAGE):
    """Return a complete tape, or its prefix plus the exact next cursor.

    A budget stop is resumable. HTTP/protocol failures raise, so callers keep
    their previous cursor and can safely repeat inserts via existing dedupe.
    `fetch` is the research adapter for an already decoded HTTP response.
    """
    ids = list(dict.fromkeys(ids))
    if not ids or len(ids) > CONDITIONS:
        raise ValueError('trade request needs 1 to 20 conditions')
    if not 1 <= page_size <= PAGE:
        raise ValueError('invalid trade page size')
    out, seen, previous_oldest = [], set(), None
    while True:
        remaining = None if wait_until is None else wait_until-clock()
        if remaining is not None and remaining <= .05:
            if cursor is None:
                raise TimeoutError('trade page budget exhausted before first receipt')
            return Tape(out, cursor=cursor)
        params = {'condition': ','.join(ids), 'limit': page_size, 'taker_only': 'true'}
        if cursor is not None:
            params['cursor'] = cursor
        timeout = 20 if remaining is None else max(.01, min(20, remaining/2))
        if fetch:
            body = fetch(API, params, timeout)
        else:
            response = get(API, params=params, headers=UA, timeout=timeout)
            if getattr(response, 'status_code', 200) in (429, 503):
                after = _retry_after(response)
                delay = min(5 if after is None else max(0, after), 15)
                if wait_until is None or clock()+delay+.05 < wait_until:
                    sleep(delay)
                    if stats is not None:
                        stats['waits'] = stats.get('waits', 0)+1
                        stats['seconds'] = round(stats.get('seconds', 0)+delay, 1)
                    left = None if wait_until is None else wait_until-clock()
                    timeout = 20 if left is None else max(.01, min(20, left/2))
                    response = get(API, params=params, headers=UA, timeout=timeout)
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict) or not isinstance(body.get('data'), list):
            raise ValueError('trade API v2 returned an invalid data envelope')
        pagination = body.get('pagination')
        if not isinstance(pagination, dict) or type(pagination.get('has_more')) is not bool:
            raise ValueError('trade API v2 omitted exact pagination evidence')
        next_cursor = pagination.get('next_cursor')
        if (next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor)
                or pagination['has_more'] != (next_cursor is not None)):
            raise ValueError('trade API v2 has inconsistent cursor evidence')
        rows = [normalize(row) for row in body['data']]
        if any(r.get('conditionId') not in ids for r in rows):
            raise ValueError('trade API returned a different condition cohort')
        stamps = [float(r['timestamp']) for r in rows]
        if (stamps != sorted(stamps, reverse=True)
                or stamps and previous_oldest is not None and max(stamps) > previous_oldest):
            raise ValueError('trade API returned an unordered cursor walk')
        out.extend(r for r in rows if (lower is None or float(r['timestamp']) >= lower)
                   and (upper is None or float(r['timestamp']) <= upper))
        if next_cursor is None or stamps and lower is not None and min(stamps) < lower:
            return Tape(out)
        if next_cursor == cursor or next_cursor in seen:
            raise ValueError('trade API repeated a cursor')
        seen.add(next_cursor)
        if stamps:
            previous_oldest = min(stamps)
        cursor = next_cursor


def window(ids, start, end, *, continuation=None, **kwargs):
    """A possibly wider market is split by condition, never by ignored dates."""
    ids = list(dict.fromkeys(ids))
    if not ids or start > end:
        raise ValueError('invalid trade window or empty cohort')
    old = continuation or {}
    chunk, cursor = (old.get('chunk', 0), old.get('cursor')) if old.get('ids') == ids else (0, None)
    if type(chunk) is not int or chunk < 0 or chunk >= (len(ids)+CONDITIONS-1)//CONDITIONS:
        raise ValueError('invalid saved trade-window cohort')
    out = []
    for index in range(chunk, (len(ids)+CONDITIONS-1)//CONDITIONS):
        try:
            rows = walk(ids[index*CONDITIONS:(index+1)*CONDITIONS], start, end,
                        cursor=cursor if index == chunk else None, **kwargs)
        except TimeoutError:
            return Tape(out, continuation={'ids': ids, 'chunk': index,
                                           'cursor': cursor if index == chunk else None})
        out.extend(rows)
        if rows.cursor is not None:
            return Tape(out, continuation={'ids': ids, 'chunk': index, 'cursor': rows.cursor})
    return Tape(out)
