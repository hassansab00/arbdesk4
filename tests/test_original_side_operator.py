"""Bounded queue draining and independent settlement recovery (synthetic)."""
import original_side_paper as operator
import pytest


@pytest.mark.parametrize('used,limit,allowed,state',[
    (79,100,True,'ok'),(80,100,True,'warning'),(99,100,True,'warning'),
    (100,100,True,'hold'),(1,100,False,'hold'),(True,100,True,'unknown'),
    (10,0,True,'unknown'),(-1,100,True,'unknown'),(10.5,100,True,'unknown'),
    (10,100,'true','unknown'),
])
def test_capacity_receipt_is_validated(used,limit,allowed,state):
    assert operator.evidence_capacity_state(dict(bytes=used,limit_bytes=limit,capture_allowed=allowed))==state


def test_temporary_headroom_never_hides_missing_cold_tier():
    capacity=dict(bytes=10,limit_bytes=100,capture_allowed=True,
                  temporary_bridge=True,cold_tier_ready=False)
    assert operator.evidence_capacity_state(capacity)=='warning'


@pytest.mark.parametrize('capacity,state',[
    (dict(bytes=80,limit_bytes=100,capture_allowed=True),'warning'),
    (dict(bytes=100,limit_bytes=100,capture_allowed=False),'hold'),(None,'unknown'),
    (dict(bytes=10,limit_bytes=100,capture_allowed=True),'ok'),
])
def test_capacity_pressure_is_reported_without_skipping_execution_or_settlement(monkeypatch,capacity,state):
    calls=[];logs=[]
    def rpc(name,params=None):
        calls.append(name)
        if name=='original_side_runtime':return dict(calls=[],accounts=[],capacity=capacity)
        if name=='original_side_settlement_candidates':return []
        return None
    monkeypatch.setattr(operator,'rpc',rpc)
    monkeypatch.setattr(operator,'log_run',lambda *args,**kwargs:logs.append(args))
    result=operator.run()
    assert result['evidence_capacity_state']==state
    assert logs[0][1]==('ok' if state=='ok' else 'attention')
    assert 'claim_original_side_order' in calls and 'original_side_settlement_candidates' in calls
    assert 'capacity' not in result # no raw private capacity packet in public logs


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


def test_lease_failure_does_not_skip_independent_settlement_recovery(monkeypatch):
    calls=[]
    def rpc(name, params=None):
        calls.append(name)
        if name=='original_side_runtime':return dict(calls=[],accounts=[])
        if name=='claim_original_side_order':raise TimeoutError('synthetic lease read failure')
        if name=='original_side_settlement_candidates':return []
        return None
    monkeypatch.setattr(operator,'rpc',rpc)
    monkeypatch.setattr(operator,'log_run',lambda *a,**k:None)
    result=operator.run()
    assert 'original_side_settlement_candidates' in calls
    assert result['failures']['order_claim_TimeoutError']==1


def test_failed_fill_and_stage_receipt_still_recover_settlement(monkeypatch):
    pending=[dict(order_id='synthetic',call_id='call',account_id='account')];calls=[]
    def rpc(name, params=None):
        calls.append(name)
        if name=='original_side_runtime':return dict(calls=[],accounts=[])
        if name=='claim_original_side_order':return pending.pop() if pending else None
        if name=='record_original_side_stage':raise TimeoutError('synthetic receipt write failure')
        if name=='original_side_settlement_candidates':return []
        return None
    monkeypatch.setattr(operator,'rpc',rpc)
    monkeypatch.setattr(operator,'log_run',lambda *a,**k:None)
    def fail(order):raise TimeoutError('synthetic fill completion failure')
    monkeypatch.setattr(operator,'fill_order',fail)
    result=operator.run()
    assert 'original_side_settlement_candidates' in calls
    assert result['failures']['fill_stage_receipt_TimeoutError']==1
