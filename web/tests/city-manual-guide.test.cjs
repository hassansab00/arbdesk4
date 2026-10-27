// Entirely synthetic evidence. No captured predictions or trading measurements.
const assert=require('node:assert/strict');
const {createClient}=require('@supabase/supabase-js');
const {fakePostgrest}=require('./fakePostgrest.cjs');
const lib=require('../.route-test/lib/cityManualGuide.js');
const {cityInstant}=require('../.route-test/lib/time.js');
const now=Date.parse('2001-01-15T12:00:00Z'),target='2001-01-15';
const dates=Array.from({length:12},(_,i)=>`2001-01-${String(i+1).padStart(2,'0')}`);
function report(h,hits=6,verified=12,ds=dates){return {city_key:'fixture',scope:'served',checkpoint:h,as_of:'2001-01-15T11:00:00Z',
  report:{city:'fixture',scope:'served',horizon:h,period_end:target,why:{weather:{wind:2},reviewed_dates:3,missed_dates:6},
    recent_dates:ds.map((date,i)=>({date,n:1,hits:i<hits?1:0,pending:0})),
    capture_timing:{policy:'before-expected-peak-v1',recent_calls:ds.map((date,i)=>({date,checkpoint:h,hit:i<hits?1:0,
      before_expected_peak:i<verified,reason:i<verified?'eligible':'missing_server_receipt',
      frozen_at:i<verified?date+'T11:30:00Z':null}))}}};}
const rows=[report('morning',3),report('noon',8),report('prepeak_1h',10),report('postpeak_1h',12)];
const card={city_key:'fixture',name:'Fixture city',for_date:target,unit:'C',timezone:'UTC',
  priced_at:'2001-01-15T11:45:00Z',edges_at:'2001-01-15T11:45:00Z',best:{side:'YES',band:'Synthetic bucket',
    price:.4,edge:.03,depth:10},live:{source_kind:'station'},repriced:null,stale:null,disagrees:false};
const status={city_key:'fixture',target_date:target,checkpoint:'morning',status:'candidate',reason:'Synthetic ready state'};
const args={card,moment:'morning',rows,history:[],status,now};
const preserved=JSON.stringify(args);
let g=lib.buildManualGuide(args);
assert.equal(g.state,'Review only');assert.deepEqual(g.reference.leaders,['prepeak_1h']);
assert.equal(g.reference.table.length,3,'post-peak never competes for the forecast timing reference');
assert.equal(g.reference.dates.length,12);assert.equal(JSON.stringify(args),preserved);
assert.equal(g.reference.table[0].verifiedBeforePeak,12);
// Perfect predictions made today or later cannot improve the historical reference.
const leaked=structuredClone(rows);leaked[0].report.recent_dates.push(...['2001-01-15','2001-01-16'].map(date=>({date,n:1,hits:1})));
assert.deepEqual(lib.checkpointReference(leaked,'fixture',target,now),g.reference);
assert.equal(lib.checkpointReference([...rows,report('morning',12)],'fixture',target,now).table.some(r=>r.checkpoint==='morning'),false,
  'ambiguous checkpoint reports do not turn into selected perfect replacements');
const unequal=[report('morning',10,12,dates.slice(0,10)),report('noon',10)];
assert( lib.checkpointReference(unequal,'fixture',target,now).table.every(r=>r.n===10),'compare only common dates');
assert.deepEqual(lib.checkpointReference(unequal,'fixture',target,now).leaders,['morning','noon'],'ties are explicit');
assert(!lib.checkpointReference([report('morning',1,1,dates.slice(0,1)),report('noon')],'fixture',target,now).comparable);
assert(!lib.checkpointReference(rows.map(r=>({...r,scope:'wxrd3_raw'})),'fixture',target,now).comparable,'no shadow/raw scope substitution');
assert(!lib.checkpointReference(rows.map(r=>({...r,as_of:'2001-01-01T00:00:00Z'})),'fixture',target,now).comparable);
assert(!lib.checkpointReference(rows.map(r=>({...r,as_of:'2001-01-16T00:00:00Z'})),'fixture',target,now).comparable);
const late=rows.map(r=>r.checkpoint==='prepeak_1h'?report('prepeak_1h',10,0):r);
assert.equal(lib.buildManualGuide({...args,rows:late}).state,'Wait','late/legacy timing must not authorize an entry rule');
const invalid=structuredClone(rows);invalid[2].report.capture_timing.policy='unknown';
assert.equal(lib.buildManualGuide({...args,rows:invalid}).reference.table[0].verifiedBeforePeak,0);
for(const change of [{best:null},{stale:{standing:'one_below'}},{edges_at:null},
  {priced_at:'2001-01-15T13:00:00Z'},{edges_at:'2001-01-15T08:00:00Z'},
  {best:{...card.best,depth:0}},{best:{...card.best,edge:-.01}},{timezone:null}]){
  assert.equal(lib.buildManualGuide({...args,card:{...card,...change}}).state,'Wait',JSON.stringify(change));
}
assert.equal(lib.buildManualGuide({...args,status:null}).state,'Wait');
assert.equal(lib.buildManualGuide({...args,status:{...status,target_date:'2001-01-14'}}).state,'Wait');
assert.equal(lib.buildManualGuide({...args,status:{...status,status:'unavailable'}}).state,'Wait');
assert(lib.buildManualGuide({...args,card:{...card,timezone:'Asia/Tokyo',for_date:'2001-01-14'}})
  .blockers.some(x=>x.includes('day has ended')),'city date, not viewer date');
assert(lib.buildManualGuide({...args,card:{...card,live:{source_kind:'model'}}}).cautions.some(x=>x.includes('not an observation')));
const history=dates.slice(0,3).map(for_date=>({city_key:'fixture',for_date,called_when:'morning',hit:false,forecast_error_c:1}));
let t=lib.selectedTrend([...history,{...history[0],for_date:target,forecast_error_c:99}], 'fixture','morning',target);
assert.equal(t.n,3);assert.equal(t.biasC,1,'positive is actual minus forecast: cold tendency');
assert.equal(lib.selectedTrend(history.map(r=>({...r,forecast_error_c:-1})),'fixture','morning',target).biasC,-1);
assert.equal(lib.selectedTrend([...history,history[0]],'fixture','morning',target).n,2,'duplicates excluded');
assert.deepEqual(lib.guideReviewTime('d1_eve',12),{dayOffset:-1,hour:18});
assert.deepEqual(lib.guideReviewTime('prepeak_1h',12),{dayOffset:0,hour:11});
assert.equal(lib.guideReviewTime('prepeak_1h',null),null);
assert.equal(lib.guideReviewTime('postpeak_1h',14),null);
assert.equal(cityInstant('2001-01-15',9,'Asia/Kolkata').toISOString(),'2001-01-15T03:30:00.000Z');
// Render the actual new view; no browser auth, private endpoint or new write path.
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const Module=require('module'),old=Module._resolveFilename;
Module._resolveFilename=function(name,...rest){if(name.startsWith('@/'))name=require('node:path').join(__dirname,'../.route-test',name.slice(2));return old.call(this,name,...rest);};
const {CityManualGuideView}=require('../.route-test/components/CityManualGuide.js');
const render=(guide,extra={})=>renderToStaticMarkup(React.createElement(CityManualGuideView,{guide,card,moment:'morning',
  peak:{peak_hour_local:12,window_width_h:2,n_days:20},loading:false,error:null,...extra}));
let html=render(lib.buildManualGuide({...args,history}));
for(const phrase of ['Manual trading guide','Review only','1 h before peak','11:00','cold tendency','not an executable ask',
  'not validated on later unseen dates','server receipt before the saved expected peak']) assert(html.includes(phrase),phrase);
assert(!html.includes('Buy now'));assert(!html.includes('Guaranteed'));
assert(render(lib.buildManualGuide({...args,history:history.map(r=>({...r,forecast_error_c:-1}))})).includes('a warm tendency'));
const dayReference=lib.buildManualGuide({...args,rows:[report('day_ahead',12),report('morning',2)]});
assert(render(dayReference).includes('start of the target day'),'day ahead ends at the beginning of the target day, not its end');
assert(render(g,{error:'Synthetic failed read'}).includes('History unavailable'));
assert(!render(g,{error:'Synthetic failed read'}).includes('Strongest recorded checkpoint'));
assert(!render(g,{loading:true}).includes('Strongest recorded checkpoint'));
(async()=>{
  for(const fail of [false,true]){
    const db=await fakePostgrest({tables:{v_city_prediction_validation:rows},fail:new Set(fail?['v_city_prediction_validation']:[])});
    try{const client=createClient(db.url,'synthetic-anon-key',{auth:{persistSession:false,autoRefreshToken:false}});
      const result=await lib.readGuideReports(client,'fixture');
      if(fail){assert(result.error);assert.equal(result.data,null);}else{assert.equal(result.error,null);assert.equal(result.data.length,3);}
      assert(db.requests.every(r=>r.method==='GET'&&r.path==='/rest/v1/v_city_prediction_validation'));
    }finally{await db.close();}
  }
  const db=await fakePostgrest({tables:{v_city_prediction_validation:Array.from({length:7},()=>report('morning'))}});
  try{const client=createClient(db.url,'synthetic-anon-key',{auth:{persistSession:false,autoRefreshToken:false}});
    const r=await lib.readGuideReports(client,'fixture');assert(r.error);assert.equal(r.data,null);
  }finally{await db.close();}
  console.log('PASS: city manual guide: matched past dates, no lookahead, timing proof, observed bias, quote/read failures, local clocks and read-only public views');
})().catch(e=>{console.error(e);process.exit(1);});
