import base64
import copy
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import pytest
from wxpredict import temperature_points as points
from test_temperature_model import record


def evidence():
    start=dt.datetime(2026,8,1,tzinfo=dt.timezone.utc);received='2026-08-02T01:00:00Z'
    windows=[]
    for i in range(4):
        hour=5+i*6;value='0231' if i==1 else '0200'
        windows.append(dict(window_start=(start+dt.timedelta(hours=i*6)).isoformat(),window_end=(start+dt.timedelta(hours=(i+1)*6)).isoformat(),
            received_at=received,response=f'METAR TEST 01{hour:02}00Z 20/10 RMK T{value}0100'))
    p=dict(schema='awc-routine-metar-v2',completeness='bounded_windows_cross_checked',station_id='TEST',for_date='2026-08-01',timezone='UTC',
        display_unit='C',qualifying_max_c='23.1',supplementary_windows=windows)
    packed=json.dumps(p).encode()
    return dict(evidence_id='a'*64,city_key='synthetic',for_date='2026-08-01',station_id='TEST',record_status='verified',
        parser_version='weather-outcomes-v6',captured_at=received,payload_sha256=hashlib.sha256(packed).hexdigest(),
        raw_payload=dict(encoding='gzip+base64',data=base64.b64encode(gzip.compress(packed)).decode()))


def test_actual_routine_maximum_is_a_point_not_the_venue_bucket_midpoint():
    actual=points.measured_point(evidence(),record(),'b','2026-08-03T00:00:00Z')
    assert actual==23.1 and actual!=22.5
    with pytest.raises(ValueError,match='venue winner'):points.measured_point(evidence(),record(),'a','2026-08-03T00:00:00Z')


@pytest.mark.parametrize('change',[
    {'parser_version':'weather-outcomes-v5'},{'record_status':'disputed'},{'station_id':'KJFK'},
    {'captured_at':'2026-08-04T00:00:00Z'},{'payload_sha256':'b'*64},
])
def test_untrusted_or_later_points_are_not_used(change):
    ev=evidence();ev.update(change)
    with pytest.raises(ValueError):points.measured_point(ev,record(),'b','2026-08-03T00:00:00Z')


def test_bucket_only_outcome_produces_no_mae_or_crps():
    r=record();item=dict(call=dict(city_key=r['city'],target_date=r['date'],record=r),score={'winner_band_id':'b'})
    result=points.evaluate_points([item],[],'2026-08-03T00:00:00Z')
    assert result['by_horizon']=={} and result['exclusions']=={'missing_complete_verified_point':1}
