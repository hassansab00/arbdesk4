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


def cycle(budget_seconds=60):
    from paper_worker import capture_book
    started, queued = time.monotonic(), 0
    skipped = {}

    def done(note):
        """A run that stops early is still a run, and has to say so.

        log_run sat only at the bottom, so the budget exit returned without
        writing anything and the desk page showed a stale 'Exits' tile.
        """
        detail = {'exits_queued': queued, 'stopped': note}
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
            bands=rest('bands',{'band_id':'eq.'+pos['band_id'],'select':'token_yes,token_no'})
            if not bands:
                continue
            order={'band_id':pos['band_id'],'side':pos['side'],'token_id':bands[0]['token_yes' if pos['side']=='YES' else 'token_no']}
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
                'max_book_age_seconds':120,'expires_at':(now+dt.timedelta(minutes=5)).isoformat()},book,now=now)
            if preview['status']!='filled':
                continue
            limit=min(number(x['price']) for x in preview['fills'])
            gain=(number(preview['notional'])-number(preview['fee']))/number(pos['cost_basis'])-1
            policy=account['policy']
            if -number(policy['stop_loss_fraction'])<gain<number(policy['take_profit_fraction']):
                continue
            identity=f"{account['account_id']}:{pos['band_id']}:{pos['side']}:{pos['shares']}:{pos['cost_basis']}:{book['snapshot_id']}:{account['policy_version']}"
            rpc('queue_automatic_paper_exit',{'p_account':account['account_id'],'p_command':str(uuid.uuid5(uuid.NAMESPACE_URL,identity)),
                'p_band':pos['band_id'],'p_side':pos['side'],'p_limit':str(limit),'p_evidence':preview,'p_policy_version':account['policy_version']})
            queued+=1
    return done('considered every position')


if __name__=='__main__':
    print(cycle())
