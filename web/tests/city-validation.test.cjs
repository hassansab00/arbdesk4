const {test}=require('node:test');const assert=require('node:assert/strict');
const {rankCities,evidenceWords,HORIZONS}=require('../.route-test/lib/cityValidation');
test('ranking retains missing evidence and does not mutate source',()=>{
 const rows=[{display_name:'empty',n:0,hit_rate:null},{display_name:'b',n:20,hit_rate:.5},{display_name:'a',n:30,hit_rate:.5},{display_name:'top',n:2,hit_rate:1}];
 assert.deepEqual(rankCities(rows).map(r=>r.display_name),['top','a','b','empty']);assert.equal(rows[0].display_name,'empty');
});
test('diagnostic says sample limits and reviewed denominators',()=>{
 const r={n:5,dates:5,why:{missed_dates:3,reviewed_dates:1,weather:{cloud:1},mean_peak_shift_h:2}};
 assert.match(evidenceWords(r),/Limited/);assert.match(evidenceWords(r),/1\/3/);assert.match(evidenceWords(r),/cloud/);
 assert.ok(HORIZONS.find(([h])=>h==='postpeak_1h'));
});
