"""Optional account-level take-profit / stop-loss exits on executable bid depth.

Disabled until the owner explicitly enables the policy. A threshold is not a
guaranteed exit price; orders recheck the book and may fill only partially.
"""
import datetime as dt
import time
import uuid

import requests

from common import rest, rest_all, rpc, log_run
from paper_execution import number, simulate
from venue import venue_read


def _book_or_reason(capture_book, order):
    """The book for this position, or why there is not one.

    THE VENUE STOPS SERVING A TOKEN ONCE ITS MARKET RESOLVES, and an exit
    cycle reaches those positions constantly - a position is only still open
    here because settlement has not closed it yet. capture_book called
    raise_for_status, nothing caught it, and one 404 killed the whole step:

      requests.exceptions.HTTPError: 404 Client Error: Not Found for url:
      https://clob.polymarket.com/book?token_id=245778544946570701...

    That is the last step of pipeline_intraday, so every run since exits were
    switched on has finished red AFTER completing eleven minutes of real work
    - signals, proposals, fills and the settlement sweep all succeeded and the
    job still reported failure.

    A 404 is not an error here, it is an answer: this market is gone, the
    position belongs to settlement, and no exit can or should be priced. Any
    other failure is the venue being unreachable, which is temporary and must
    not cost the remaining positions their turn either.
    """
    try:
        return capture_book(order), None
    except requests.HTTPError as exc:
        status = getattr(exc.response, 'status_code', None)
        if status == 404:
            return None, 'resolved'
        return None, f'venue_http_{status or "error"}'
    except (requests.RequestException, ValueError) as exc:
        # ValueError is capture_book's own identity guard - a token that does
        # not match its market is a thing to record, never to trade against.
        return None, type(exc).__name__.lower()



def to_band_unit(temp_c, unit):
    """live_weather keeps Celsius; a US band is labelled in Fahrenheit."""
    if temp_c is None:
        return None
    return temp_c * 9.0 / 5.0 + 32.0 if str(unit or "C").upper() == "F" else temp_c


def band_contains(band, value):
    """Is the day's maximum inside this bucket? Open-ended buckets have one edge."""
    if value is None:
        return None
    lo, hi = band.get("band_lo"), band.get("band_hi")
    if band.get("open_low"):
        return None if hi is None else value < hi
    if band.get("open_high"):
        return None if lo is None else value >= lo
    if lo is None or hi is None:
        return None
    return lo <= value < hi


def certainly_lost(band, running_max_c, side, unit, day_decided):
    """True when this position can no longer win, so selling beats settling.

    A DAILY MAXIMUM ONLY GOES UP. That is the whole asymmetry, and it is a
    property of the instrument rather than a rule anyone chose:

      * once the running max has passed ABOVE a closed bucket, a YES on it can
        never win again - and that is certain long before the day is decided,
        which is the point. Waiting for settlement pays $0; the book usually
        still bids a cent or two.
      * on an open-high bucket ("95F or more") the reverse locks in: once the
        max reaches band_lo, YES has won permanently and the NO has lost.
      * everything else needs the day to be over, because the max can still
        move: a max sitting inside a bucket today may leave it this afternoon.

    WINNERS ARE NOT EXITED HERE. A position that is certain to win settles at
    $1.00 and selling it costs the venue fee on the way out, so holding is
    strictly better. This only ever recovers residual value from a loser.
    """
    # Read the way the venue settles: the whole degree in the band's unit
    # (venue.py, plan v2 P5.0 item 6). Buckets are [lo, hi) on whole degrees,
    # so a raw 69.6 F would still be "inside" a 68-69 F bucket the venue has
    # already left behind at 70.
    value = venue_read(to_band_unit(running_max_c, unit))
    if value is None:
        return False
    inside = band_contains(band, value)
    open_high, open_low = bool(band.get("open_high")), bool(band.get("open_low"))
    lo, hi = band.get("band_lo"), band.get("band_hi")

    if side == "YES":
        # The max has climbed past this bucket for good.
        if not open_high and hi is not None and value >= hi:
            return True
    else:
        # An open-high bucket the max has already reached can never un-win.
        if open_high and lo is not None and value >= lo:
            return True

    if not day_decided or inside is None:
        return False
    return (not inside) if side == "YES" else bool(inside)


def cycle(budget_seconds=60):
    from paper_worker import capture_book
    started, queued = time.monotonic(), 0
    skipped = {}
    exits_on_outcome = 0
    _markets, _weathers = {}, {}

    def _market(market_id):
        """city, date and unit for a band's market. Cached: an exit cycle walks
        many positions on the same handful of markets."""
        if market_id and market_id not in _markets:
            # Canonical (plan v2 P2.5): 667 markets carry a wrong raw unit.
            rows = rest('v_canonical_markets', {'market_id': 'eq.' + str(market_id),
                                                'select': 'city_key,resolution_date,unit'})
            _markets[market_id] = rows[0] if rows else None
        return _markets.get(market_id)

    def _weather(city_key):
        if city_key and city_key not in _weathers:
            rows = rest('live_weather', {'city_key': 'eq.' + str(city_key),
                                         'select': 'running_max_c,day_decided,local_date'})
            _weathers[city_key] = rows[0] if rows else None
        return _weathers.get(city_key)

    def done(note):
        """A run that stops early is still a run, and has to say so.

        log_run sat only at the bottom, so the budget exit returned without
        writing anything and the desk page showed a stale 'Exits' tile.
        """
        detail = {'exits_queued': queued, 'stopped': note,
                  'exits_on_decided_outcome': exits_on_outcome}
        if skipped:
            detail['skipped'] = dict(sorted(skipped.items()))
        log_run('paper_exits', 'ok', queued, detail)
        return {'exits_queued': queued, 'skipped': dict(sorted(skipped.items()))}
    accounts=rest_all('paper_accounts',{'mode':'eq.automatic','policy->>auto_exit_enabled':'eq.true'},order='account_id')
    for account in accounts:
        positions=rest_all('paper_positions',{'account_id':'eq.'+account['account_id'],'shares':'gt.0'},order='band_id,side')
        for pos in positions:
            if time.monotonic()-started>budget_seconds:
                return done(f'reached the {budget_seconds}s budget')
            if number(pos['cost_basis'])<=0:
                continue
            pending=rest('paper_orders',{'account_id':'eq.'+account['account_id'],'band_id':'eq.'+pos['band_id'],
                'side':'eq.'+pos['side'],'status':'in.(queued,working)','select':'order_id','limit':'1'})
            if pending:
                continue
            bands=rest('v_canonical_bands',{'band_id':'eq.'+pos['band_id'],
                'select':'token_yes,token_no,band_lo,band_hi,open_low,open_high,market_id'})
            if not bands:
                continue
            band=bands[0]
            order={'band_id':pos['band_id'],'side':pos['side'],'token_id':band['token_yes' if pos['side']=='YES' else 'token_no']}
            # HAS THIS ONE ALREADY LOST? A daily maximum only goes up, so that can
            # be certain hours before the venue resolves - see certainly_lost.
            # The live row must describe the SAME day the band resolves, or a
            # max set today would retire a position on tomorrow's ladder.
            lost=False
            market=_market(band.get('market_id'))
            if market:
                weather=_weather(market.get('city_key'))
                if weather and str(weather.get('local_date') or '')==str(market.get('resolution_date') or ''):
                    lost=certainly_lost(band, number(weather['running_max_c']) if weather.get('running_max_c') is not None else None,
                                        pos['side'], market.get('unit'), bool(weather.get('day_decided')))
            book, reason = _book_or_reason(capture_book, order)
            if book is None:
                skipped[reason] = skipped.get(reason, 0) + 1
                continue
            if not book.get('bids'):
                skipped['no_bids'] = skipped.get('no_bids', 0) + 1
                continue
            limit=min(number(x['price']) for x in book['bids'])
            now=dt.datetime.now(dt.timezone.utc)
            preview=simulate({**order,'action':'SELL','shares':pos['shares'],'limit_price':str(limit),'share_step':'.01',
                'max_book_age_seconds':120,'expires_at':(now+dt.timedelta(minutes=30)).isoformat()},book,now=now)
            if preview['status']!='filled':
                continue
            limit=min(number(x['price']) for x in preview['fills'])
            gain=(number(preview['notional'])-number(preview['fee']))/number(pos['cost_basis'])-1
            policy=account['policy']
            # A loser that cannot recover is sold whatever the thresholds say:
            # the alternative is settling it at $0. Winners still fall through
            # to the thresholds, because a certain winner settles at $1.00 and
            # selling one only pays the venue a fee.
            if not lost and -number(policy['stop_loss_fraction'])<gain<number(policy['take_profit_fraction']):
                continue
            if lost:
                exits_on_outcome+=1
            identity=f"{account['account_id']}:{pos['band_id']}:{pos['side']}:{pos['shares']}:{pos['cost_basis']}:{book['snapshot_id']}:{account['policy_version']}"
            rpc('queue_automatic_paper_exit',{'p_account':account['account_id'],'p_command':str(uuid.uuid5(uuid.NAMESPACE_URL,identity)),
                'p_band':pos['band_id'],'p_side':pos['side'],'p_limit':str(limit),'p_evidence':{**preview,'exit_reason':'outcome_decided' if lost else 'threshold'},'p_policy_version':account['policy_version']})
            queued+=1
    return done('considered every position')


if __name__=='__main__':
    print(cycle())
