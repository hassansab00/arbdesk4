const {test}=require('node:test');const assert=require('node:assert/strict');
const {rankCities,recentCityScores,evidenceWords,HORIZONS,timingView,captureDelay,timingReason,readinessWords}=require('../.route-test/lib/cityValidation');
test('ranking retains missing evidence and does not mutate source',()=>{
 const rows=[{display_name:'empty',n:0,hit_rate:null},{display_name:'b',n:20,hit_rate:.5},{display_name:'a',n:30,hit_rate:.5},{display_name:'top',n:2,hit_rate:1}];
 assert.deepEqual(rankCities(rows).map(r=>r.display_name),['top','a','b','empty']);assert.equal(rows[0].display_name,'empty');
});
test('daily results retain a bad cohort and exclude missing and pending calls from accuracy',()=>{
 const rows=[{city:'a',recent_dates:[{date:'2026-10-18',n:1,hits:0,pending:0},{date:'2026-10-19',n:0,hits:0,pending:1}]},
 {city:'b',recent_dates:[{date:'2026-10-18',n:1,hits:1,pending:null}]},{city:'missing'}];
 const original=JSON.stringify(rows),dates=recentCityScores(rows);
 assert.deepEqual(dates.map(d=>d.date),['2026-10-19','2026-10-18']);
 assert.equal(dates[0].hit_rate,null);assert.equal(dates[0].cities,0);assert.equal(dates[0].pending,1);
 assert.equal(dates[1].hit_rate,.5);assert.equal(dates[1].n,2);assert.equal(dates[1].cities,2);assert.equal(dates[1].pending,null);
 assert.equal(JSON.stringify(rows),original);
});
test('diagnostic says sample limits and reviewed denominators',()=>{
 const r={n:5,dates:5,why:{missed_dates:3,reviewed_dates:1,weather:{cloud:1},mean_peak_shift_h:2}};
 assert.match(evidenceWords(r),/Limited/);assert.match(evidenceWords(r),/1\/3/);assert.match(evidenceWords(r),/cloud/);
 assert.ok(HORIZONS.find(([h])=>h==='postpeak_1h'));
});
test('strict timing view never falls back to scheduled scores or leaks full-cohort diagnostics',()=>{
 const row={city:'a',n:2,hits:2,hit_rate:1,paired_audit:{calls:2},early_comparison:{calls:2},observed_peak_timing:{calls:2},
   capture_timing:{calls:2,eligible_calls:1,excluded_calls:1,strict:{n:1,hits:0,hit_rate:0,recent_dates:[{date:'2026-10-24',n:1,hits:0,pending:0}]}}};
 const before=JSON.stringify(row),strict=timingView(row,true);
 assert.equal(strict.n,1);assert.equal(strict.hits,0);assert.equal(strict.hit_rate,0);
 assert.equal(strict.paired_audit,undefined);assert.equal(strict.early_comparison,undefined);
 assert.equal(strict.observed_peak_timing,undefined);
 assert.equal(timingView({...row,capture_timing:undefined},true).hit_rate,null);
 assert.equal(timingView({...row,capture_timing:undefined},true).n,0);
 assert.equal(recentCityScores([strict])[0].hit_rate,0);
 assert.equal(timingView(row,false),row);assert.equal(JSON.stringify(row),before);
});
test('capture delay uses the city date and DST rather than the viewer timezone',()=>{
 assert.equal(captureDelay('2026-10-25 12:00:00','2026-10-25T11:37:00Z','Europe/Paris'),37);
 assert.equal(captureDelay('2026-10-24 12:00:00','2026-10-24T10:37:00Z','Europe/Paris'),37);
 assert.equal(captureDelay('2026-10-24 09:00:00','2026-10-24T03:51:00Z','Asia/Kolkata'),21);
 assert.equal(captureDelay(null,'2026-10-25T11:37:00Z','UTC'),null);
 assert.equal(captureDelay('2026-10-25 12:00:00','invalid','UTC'),null);
 assert.equal(captureDelay('2026-10-25 12:00:00','2026-10-25T12:37:00Z'),null);
 assert.equal(timingReason('at_or_after_expected_peak'),'At / after expected peak');
});
test('strict market comparison cannot retain a full-cohort win',()=>{
 const row={market_comparison:{n:10,model:{hit:1}},capture_timing:{strict:{n:2,hits:0,hit_rate:0,market_comparison:{n:1,model:{hit:0}}}}};
 assert.equal(timingView(row,true).market_comparison.n,1);
 assert.equal(timingView(row,true).market_comparison.model.hit,0);
 assert.equal(timingView({...row,capture_timing:undefined},true).market_comparison,undefined);
});
test('readiness distinguishes sample requirements from passed validation',()=>{
 assert.match(readinessWords(undefined),/awaiting/);
 const thin={sample_ready:false,validation_window_days:14,counts:[{name:'older training dates',available:0,required:20,missing:20}]};
 assert.match(readinessWords(thin),/0\/20 \(20 missing\)/);
 assert.match(readinessWords({...thin,sample_ready:true}),/validation must still pass/);
});
