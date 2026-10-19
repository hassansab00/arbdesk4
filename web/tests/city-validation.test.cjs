const {test}=require('node:test');const assert=require('node:assert/strict');
const {rankCities,recentCityScores,evidenceWords,HORIZONS}=require('../.route-test/lib/cityValidation');
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
