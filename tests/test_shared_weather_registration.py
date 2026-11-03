"""A registration receipt is insufficient until the live reader verifies it."""
import copy

import pytest
import shared_weather_register as registration
from wxpredict import shared_weather as model, pre_day_pairs as pairs


def artifact():
    value=dict(policy=model.POLICY, features=list(model.FEATURES),
        coefficients=[0.0]*len(model.FEATURES), sources=model.source_hashes(),
        l2=model.L2, coefficient_bound=model.BOUND, max_logit_shift=model.LIMIT,
        changes_serving=False, eligible_for_promotion=False)
    value['sha256']=pairs.digest(value)
    return value


def transport(value, cohort, change=None):
    calls=[]
    receipt=dict(model_sha256=value['sha256'],registered_at='2026-11-03T22:00:00Z',cohort=cohort)
    def rpc(name, params=None, **kwargs):
        calls.append(name)
        if name=='shared_weather_register':
            assert params==dict(p_artifact=value,p_cohort=cohort)
            return receipt
        assert name=='shared_weather_inputs'
        result=dict(state='registered',artifact=copy.deepcopy(value),**{k:v for k,v in receipt.items() if k!='model_sha256'})
        if change:change(result)
        return result
    return rpc,calls


def test_registration_verifies_the_actual_reader_without_capturing_or_serving():
    value=artifact();cohort=[f'city{i}' for i in range(20)]
    rpc,calls=transport(value,cohort)
    receipt=registration.register(value,cohort,rpc)
    assert receipt['roundtrip_verified'] and not receipt['changes_serving']
    assert calls==['shared_weather_register','shared_weather_inputs']


def test_numerically_equal_but_fingerprint_changed_transport_is_not_success():
    value=artifact();cohort=[f'city{i}' for i in range(20)]
    rpc,calls=transport(value,cohort,lambda result:result['artifact'].update(coefficient_bound=2))
    with pytest.raises(ValueError,match='model identity mismatch'):
        registration.register(value,cohort,rpc)
    assert calls==['shared_weather_register','shared_weather_inputs']


def test_registration_rejects_another_latest_model_receipt():
    value=artifact();cohort=[f'city{i}' for i in range(20)]
    rpc,_=transport(value,cohort,lambda result:result.update(registered_at='2026-11-03T22:01:00Z'))
    with pytest.raises(ValueError,match='receipt does not match'):
        registration.register(value,cohort,rpc)


def test_invalid_private_artifact_or_cohort_never_reaches_registration():
    value=artifact();cohort=[f'city{i}' for i in range(20)];rpc,calls=transport(value,cohort)
    with pytest.raises(ValueError,match='unique city cohort'):
        registration.register(value,cohort[:-1]+[cohort[0]],rpc)
    value['coefficients'][0]=.1
    with pytest.raises(ValueError,match='model identity mismatch'):
        registration.register(value,cohort,rpc)
    assert calls==[]
