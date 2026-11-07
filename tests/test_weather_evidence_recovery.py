"""Synthetic complete-source replay; no production payloads or winners."""
import base64
import copy
import dataclasses
import datetime as dt
import gzip
import hashlib
import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
import metar_contract as metar
import weather_outcomes as outcomes
import weather_evidence_recovery as recovery
from test_awc_weather_contract import RULES, URL


@pytest.fixture
def case(monkeypatch):
    now=dt.datetime.now(dt.timezone.utc)
    day=now.date()-dt.timedelta(days=4)
    end=dt.datetime.combine(day+dt.timedelta(days=1),dt.time(),dt.timezone.utc)
    source='https://aviationweather.gov/api/data/metar?ids=KAAA&format=raw&date='+end.isoformat().replace('+00:00','Z')+'&hours=27'
    rules=RULES.replace(URL,source).replace('KLGA','KAAA').replace('range in whole degrees Fahrenheit','range in whole degrees Celsius')
    lines=[f'METAR KAAA {day.day:02d}{hour:02d}00Z {temp:02d}/10' for hour,temp in [(1,20),(7,23),(13,29),(22,24)]]
    lines.append(f'METAR KAAA {(day+dt.timedelta(days=1)).day:02d}0100Z 21/10')
    raw='\n'.join(lines).encode()
    def get(session,url,**kw):
        q=parse_qs(urlsplit(url).query);right=dt.datetime.fromisoformat(q['date'][0].replace('Z','+00:00'))
        left=right-dt.timedelta(hours=int(q['hours'][0]))
        rows=metar.raw_reports(raw,'KAAA',right)
        return SimpleNamespace(content='\n'.join(r['raw'] for r in rows if left<=r['when']<=right).encode())
    with monkeypatch.context() as patch:
        patch.setattr(outcomes,'_get',get)
        out=outcomes.collect_awc(object(),outcomes.parse_rule_spec(rules,'C'),day,'UTC',now)
    candidate=json.loads(json.dumps(dataclasses.asdict(out),default=str))
    prior=dict(evidence_id='disputed-original',city_key='synthetic',for_date=str(day),station_id='KAAA',
               source_url=source,record_status='disputed',payload_sha256='f'*64,
               captured_at=(now-dt.timedelta(hours=1)).isoformat(),observed_max_c='23')
    monkeypatch.setattr(outcomes,'AWC_REQUEST_INTERVAL_S',0)
    return prior,candidate,rules,dt.datetime.now(dt.timezone.utc)+dt.timedelta(seconds=1)


def run(case):
    prior,candidate,rules,now=case
    return recovery.recover(prior,candidate,rules,'C','UTC',now=now)


def reseal(candidate,change):
    data=json.loads(gzip.decompress(base64.b64decode(candidate['raw_payload']['data'])))
    change(data)
    raw=json.dumps(data,sort_keys=True,separators=(',',':')).encode()
    candidate['payload_sha256']=hashlib.sha256(raw).hexdigest()
    candidate['raw_payload']=outcomes._packed_raw(raw,'application/json')


def test_complete_replay_proposes_a_deterministic_supersession_without_mutation(case):
    before=copy.deepcopy(case)
    row=run(case)
    assert row==run(case)
    assert row['observed_max_c']=='29.000000' and row['record_status']=='verified'
    assert row['supersedes_evidence_id']=='disputed-original' and 'captured_at' not in row
    assert row['raw_payload']['recovery_review']['certifies_complete_day'] is True
    assert case==before


@pytest.mark.parametrize('change',[
    lambda p,c:p.update(record_status='verified'),
    lambda p,c:p.update(station_id='KBBB'),
    lambda p,c:p.update(source_url=p['source_url']+'&wrong=1'),
    lambda p,c:p.update(captured_at=(dt.datetime.now(dt.timezone.utc)+dt.timedelta(days=1)).isoformat()),
    lambda p,c:c.update(payload_sha256='0'*64),
    lambda p,c:c.update(observed_max_c='30'),
    lambda p,c:c.update(observed_at='2000-01-01T00:00:00Z'),
    lambda p,c:reseal(c,lambda x:x['supplementary_windows'].pop()),
    lambda p,c:reseal(c,lambda x:x.update(read_at='2099-01-01T00:00:00Z')),
    lambda p,c:reseal(c,lambda x:x.update(qualifying_max_c='30')),
    lambda p,c:reseal(c,lambda x:x['supplementary_windows'][0].update(response='METAR KBBB 010100Z 30/10')),
    lambda p,c:reseal(c,lambda x:x['supplementary_windows'][0].update(response='METAR KAAA 01010Z 40/10')),
])
def test_unreviewed_tampered_incomplete_or_guessed_evidence_is_refused(case,change):
    change(case[0],case[1])
    with pytest.raises((ValueError,KeyError)):
        run(case)
