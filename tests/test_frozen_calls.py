import pytest
from frozen_calls import freeze_insert


def test_one_expired_shadow_does_not_discard_fresh_cities():
    rows=[dict(city_key=c,target_date='2026-10-17',checkpoint='noon') for c in ['ended','valid']]
    def writer(t,rows,key):
        if any(r['city_key']=='ended' for r in rows): raise RuntimeError('Freeze refused: confirmed')
        return len(rows)
    refused=[]
    assert freeze_insert('variant_shadow_checkpoints',rows,'key',writer=writer,refused=refused)==1
    assert len(refused)==1 and 'ended' in refused[0]
    def broken(*a): raise RuntimeError('network down')
    with pytest.raises(RuntimeError,match='network'):
        freeze_insert('t',rows,'k',writer=broken,refused=[])
