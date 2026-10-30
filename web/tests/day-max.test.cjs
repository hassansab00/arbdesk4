const assert=require('node:assert/strict');
const {fakePostgrest}=require('./fakePostgrest.cjs');
const {dayMaxCap,dayMaxStatus,DAY_MAX_STRATEGY}=require('../.route-test/lib/dayMax.js');
const route=require('../.route-test/app/api/paper-desk/route.js');
(async()=>{
  const choice={city_key:'fixture',checkpoint:'d1_eve',stake_usd:10};
  assert.equal(dayMaxCap([choice]),10);
  assert.throws(()=>dayMaxCap([choice,choice]),/Invalid/);
  assert.throws(()=>dayMaxCap([{...choice,stake_usd:11}]),/Invalid/);
  const db=await fakePostgrest({tables:{
    strategies:[{strategy_id:DAY_MAX_STRATEGY,enabled:true,extra:{day_max:{cities:[choice],activated_at:'2026-10-30T00:00:00Z'}}}],
    paper_accounts:[{strategy_id:DAY_MAX_STRATEGY,kind:'shadow',account_id:'desk',name:'Day Max Prediction',mode:'automatic',entries_paused:false,cash:990}],
    day_max_prediction_entries:[{city_key:'fixture',target_date:'2026-10-30',checkpoint_id:'cp',plan_id:'plan',created_at:new Date().toISOString(),snapshot:{
      top_band_id:'band',top_prob:.7,frozen_at:'2026-10-30T12:35:00Z',checkpoint:'noon',probs:{band:.7,other:.3},predictive_context:{private:'not a UI payload'}}}],
    v_day_max_original_calls:[],
    ingest_log:[{job:'P7.11_day_max_prediction',logged_at:new Date().toISOString(),status:'ok',detail:{allocated:1}}],
    cities:[{city_key:'fixture',display_name:'Fixture',timezone:'UTC'}],
    paper_trade_plans:[{plan_id:'plan',status:'queued',reason:null}],
    paper_orders:[{plan_id:'plan',action:'BUY',status:'filled',reason:null,result:{notional:'9.80',fee:'.19'}}],
    bands:[{band_id:'band',band_label:'20°C'}],
  }});
  try {
    process.env.NEXT_PUBLIC_SUPABASE_URL=db.url;process.env.SUPABASE_SERVICE_KEY='fake-service-key';
    const response=await route.GET(new Request('https://fixture/api/paper-desk?resource=day_max'));
    const payload=await response.json();
    assert.equal(response.status,200);assert.equal(payload.error,null);
    assert.equal(payload.data.entries[0].spent,9.99);
    assert.equal(payload.data.entries[0].status,'filled');assert.equal(payload.data.entries[0].label,'20°C');
    assert.equal(payload.data.entries[0].pick.frozen_at,'2026-10-30T12:35:00Z');
    assert.ok(!JSON.stringify(payload).includes('not a UI payload'));
    const d=payload.data;
    assert.match(dayMaxStatus(d,choice,'2026-10-31',new Date('2026-10-30T17:00:00Z')).status,/Waiting/);
    assert.match(dayMaxStatus(d,choice,'2026-10-30',new Date('2026-10-30T17:00:00Z')).status,/filled/);
    assert.match(dayMaxStatus(d,choice,'').status,/valid settlement date/);
    const da={...choice,checkpoint:'day_ahead'};
    assert.equal(dayMaxCap([da]),10);
    assert.match(dayMaxStatus(d,da,'2026-10-31',new Date('2026-10-31T00:37:00Z')).status,/awaiting original capture/);
    assert.match(dayMaxStatus(d,da,'2026-10-31',new Date('2026-10-31T12:00:00Z')).status,/Capture window passed/);
    d.selection_changed_at='2026-10-31T00:40:00Z';d.calls=[{city_key:'fixture',target_date:'2026-10-31',checkpoint:'day_ahead',frozen_at:'2026-10-31T00:37:00Z',inputs_ok:true,block_reason:null}];
    assert.match(dayMaxStatus(d,da,'2026-10-31',new Date('2026-10-31T00:45:00Z')).status,/predates timing change/);
    assert.ok(db.requests.every(r=>r.method==='GET'),'the panel never places orders');
  } finally {await db.close();}
  console.log('PASS: Day Max daily cap, original receipt, actual fee-inclusive fill and read-only panel');
})().catch(e=>{console.error(e);process.exitCode=1;});
