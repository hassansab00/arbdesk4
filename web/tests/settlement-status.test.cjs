const assert = require('node:assert/strict');
const { settlementCoverage } = require('../.route-test/lib/predictive.js');
const cities = [
  {city_key:'jeddah',display_name:'Jeddah',timezone:'Asia/Riyadh'},
  {city_key:'london',display_name:'London',timezone:'Europe/London'},
  {city_key:'nyc',display_name:'NYC',timezone:'America/New_York'},
];
const markets = cities.flatMap(c => ['2026-10-20','2026-10-21'].map(date => ({
  city_key:c.city_key,resolution_date:date,
  resolution_verified_at:date==='2026-10-20' && c.city_key!=='london'?'2026-10-21T12:00:00Z':null,
})));
const calls = [
  {city_key:'nyc',for_date:'2026-10-20',called_when:'noon'},
  {city_key:'jeddah',for_date:'2026-10-20',called_when:'day_ahead'},
];
const now = Date.parse('2026-10-21T21:14:00Z');
const coverage = settlementCoverage([...markets, markets[0]],cities,calls,'noon',now);
assert.deepEqual(coverage.map(r=>r.date),['2026-10-21','2026-10-20']);
const [today,yesterday] = coverage;
assert.equal(today.tracked,3);
assert.equal(today.awaiting_venue,1,'Jeddah has ended its local day');
assert.equal(today.day_not_ended,2,'London and NYC are still on the target local day');
assert.equal(today.recorded,0,'a pending date is visible without entering the record');
assert.equal(yesterday.tracked,3,'duplicate market metadata does not inflate coverage');
assert.equal(yesterday.recorded,1,'only the selected prediction time counts');
assert.equal(yesterday.confirmed,2);
assert.equal(yesterday.awaiting_score,1);
assert.equal(yesterday.awaiting_venue,1);
assert.equal(settlementCoverage(markets,[cities[0]],calls,'day_ahead',now)[1].recorded,1);
assert.equal(settlementCoverage(markets,[cities[0]],calls,'noon',now)[1].awaiting_score,1);
assert.equal(settlementCoverage(markets,[{...cities[1],timezone:null}],[],'noon',now)[0].clock_unavailable,1);
assert.equal(settlementCoverage(markets,[{...cities[1],timezone:'invalid'}],[],'noon',now)[0].clock_unavailable,1);
assert.deepEqual(settlementCoverage(markets,[],calls,'noon',now),[]);
console.log('PASS: recent pending dates remain visible with city-local clocks and selected-moment coverage');
