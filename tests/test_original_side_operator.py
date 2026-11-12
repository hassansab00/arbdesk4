"""Bounded queue draining and independent settlement recovery (synthetic)."""
import original_side_paper as operator


def test_more_than_four_paper_orders_are_drained_before_their_expiry(monkeypatch):
    pending=[dict(order_id=str(i),call_id=str(i),account_id='synthetic') for i in range(6)]
    filled=[]
    def rpc(name, params=None):
        if name=='original_side_runtime':return dict(calls=[],accounts=[])
        if name=='claim_original_side_order':return pending.pop(0) if pending else None
        if name=='original_side_settlement_candidates':return []
        return None
    monkeypatch.setattr(operator,'rpc',rpc)
    monkeypatch.setattr(operator,'log_run',lambda *a,**k:None)
    monkeypatch.setattr(operator,'fill_order',lambda order:(filled.append(order['order_id']) or dict(status='filled')))
    result=operator.run()
    assert len(filled)==6 and not pending
    assert result['counts']['paper_filled']==6


def test_fill_completion_failure_does_not_skip_settlement_recovery(monkeypatch):
    order=dict(order_id='synthetic',call_id='call',account_id='account')
    pending=[order];calls=[]
    def rpc(name, params=None):
        calls.append(name)
        if name=='original_side_runtime':return dict(calls=[],accounts=[])
        if name=='claim_original_side_order':return pending.pop() if pending else None
        if name=='original_side_settlement_candidates':return []
        return None
    monkeypatch.setattr(operator,'rpc',rpc)
    monkeypatch.setattr(operator,'log_run',lambda *a,**k:None)
    def fail(order):raise TimeoutError('synthetic RPC timeout')
    monkeypatch.setattr(operator,'fill_order',fail)
    result=operator.run()
    assert 'original_side_settlement_candidates' in calls
    assert result['failures']['fill_completion_TimeoutError']==1
