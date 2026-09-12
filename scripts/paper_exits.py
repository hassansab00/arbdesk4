"""Optional account-level take-profit / stop-loss exits on executable bid depth.

Disabled until the owner explicitly enables the policy. A threshold is not a
guaranteed exit price; orders recheck the book and may fill only partially.
"""
import datetime as dt
import time
import uuid
from common import rest, rest_all, rpc, log_run
from paper_execution import number, simulate


def cycle(budget_seconds=60):
    from paper_worker import capture_book
    started, queued = time.monotonic(), 0
    accounts=rest_all('paper_accounts',{'mode':'eq.automatic','policy->>auto_exit_enabled':'eq.true'},order='account_id')
    for account in accounts:
        positions=rest_all('paper_positions',{'account_id':'eq.'+account['account_id'],'shares':'gt.0'},order='band_id,side')
        for pos in positions:
            if time.monotonic()-started>budget_seconds:
                return {'exits_queued':queued}
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
            book=capture_book(order)
            if not book.get('bids'):
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
    log_run('paper_exits','ok',queued,{'exits_queued':queued})
    return {'exits_queued':queued}


if __name__=='__main__':
    print(cycle())
