"""Current context is time-bounded, hashed, and separate from frozen features."""
import copy
import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1] / "tools"))
from wxpredict import current_forecasts as cf, live_reader as lr, decision_inputs as di


def test_real_arrivals_full_days_and_frozen_inputs():
    rows=[]
    for hour in range(24):
        key=f"city|2026-10-16T{hour:02d}:00"
        rows.extend([("hourly0",key,"2026-10-16T09:00:00Z",[float(hour),0,0,0,0,0,0]),
                     ("models0",key,"2026-10-16T09:00:00Z",[float(hour)]*7)])
    rows.append(("hourly0","city|2026-10-16T20:00","2026-10-16T09:20:00Z",[99.,0,0,0,0,0,0]))
    record=lr.Record(rows)
    event={"city":"city","date":"2026-10-16"}
    stamp=lambda s: dt.datetime.fromisoformat(s).timestamp()
    now=stamp("2026-10-16T09:10:00+00:00")
    c=cf.context(record,event,now,dt.timezone.utc)
    assert c["current_hours"]==c["expected_hours"]==24
    assert c["current_max_c"]==c["current_rest_max_c"]==23.
    assert c["current_models_n"]==7 and c["current_models_mean_c"]==23.
    assert cf.context(record,event,stamp("2026-10-16T09:30:00+00:00"),dt.timezone.utc)["current_max_c"]==99.
    legacy=[("hourly","city|2026-10-16T12:00","2026-10-16T09:00:00Z",[15.,0,0,0,0,0,0])]
    assert lr.hourly_series(lr.Record(legacy+rows),now)==lr.hourly_series(lr.Record(legacy),now)
    assert lr.daily_rows(lr.Record(legacy+rows),{"city":"UTC"},now)==lr.daily_rows(lr.Record(legacy),{"city":"UTC"},now)
    short=cf.context(lr.Record(rows[2:]),event,now,dt.timezone.utc)
    assert short["current_hours"]==23 and short["current_max_c"] is None
    empty=cf.context(record,event,stamp("2026-10-16T08:59:00+00:00"),dt.timezone.utc)
    assert empty["current_hours"]==0 and empty["current_models_n"]==0
    assert cf.context(lr.Record([]),{"city":"city","date":"2026-03-29"},now,__import__("zoneinfo").ZoneInfo("Europe/London"))["expected_hours"]==23


def test_context_has_its_own_integrity_check_without_adding_outcomes():
    call={"checkpoint_id":1,"city_key":"city","target_date":"2026-10-16","checkpoint":"morning",
          "decided_at":"2026-10-16T09:10:00Z"}
    row={"decision_utc":int(di.unix(call["decided_at"])),"city_key":"city","date":"2026-10-16"}
    event={"band_ids":["low","high"]}
    current=dict.fromkeys(cf.FIELDS,None)
    old=di.pack(call,event,row,"a"*40,0)["payload"]
    new=di.pack(call,event,row,"a"*40,0,current=current)["payload"]
    assert old["row_sha256"]==new["row_sha256"]
    assert di.eligible(new,"2026-10-16T09:11:00Z")
    bad=copy.deepcopy(new); bad["current_forecasts"]["current_max_c"]=99.
    with pytest.raises(ValueError,match="current-forecast input hash"):
        di.eligible(bad,"2026-10-16T09:11:00Z")
    with pytest.raises(ValueError,match="unexpected"):
        di.pack(call,event,row,"a"*40,0,current={**current,"winner":1})


def test_compact_snapshots_preserve_every_hour_and_its_response_time(monkeypatch):
    import live_capture as lc
    rows=[{"key":f"city|2026-10-16T{h:02d}:00","payload":[h,0,0,0,0,0,0]} for h in range(24)]
    compact=lc.current_snapshots(rows)
    assert len(compact)==1 and len(compact[0]["payload"]["hours"])==24
    flat=lr.Record([("hourly0",r["key"],"2026-10-16T09:00:00Z",r["payload"]) for r in rows])
    packed=lr.Record([("hourly0",compact[0]["key"],"2026-10-16T09:00:00Z",compact[0]["payload"])])
    event={"city":"city","date":"2026-10-16"}
    now=di.unix("2026-10-16T09:10:00Z")
    assert cf.context(flat,event,now,dt.timezone.utc)==cf.context(packed,event,now,dt.timezone.utc)
    assert cf.context(packed,event,now-3600,dt.timezone.utc)["current_hours"]==0
    sent=[]
    monkeypatch.setattr(lc,"rpc",lambda name,p,timeout:sent.append(p) or {"new":len(p["p_rows"])})
    assert lc.put("hourly0",dt.datetime(2026,10,16,9,tzinfo=dt.timezone.utc),rows,False)==(1,1)
    assert sent[0]["p_rows"]==compact
    with pytest.raises(ValueError,match="conflicting"):
        lc.current_snapshots(rows+[{"key":rows[0]["key"],"payload":[99]}])
