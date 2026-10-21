const assert = require('node:assert/strict');
const { settlementCoverage } = require('../.route-test/lib/predictive.js');
const cities = [
  {city_key:'jeddah',display_name:'Jeddah',timezone:'Asia/Riyadh'},
  {city_key:'london',display_name:'London',timezone:'Europe/London'},
  {city_key:'nyc',display_name:'NYC',timezone:'America/New_York'},
];
const markets = cities.flatMap(c => ['2026-10-20','2026-10-21'].map(date => ({
  city_key:c.city_key,resolution_date:date,event_slug:`highest-temperature-in-${c.city_key}-on-${date}`,
  resolution_verified_at:date==='2026-10-20' && c.city_key!=='london'?'2026-10-21T12:00:00Z':null,
})));
const calls = [
  {city_key:'nyc',for_date:'2026-10-20',called_when:'noon',actual_band:'68–69°F'},
  {city_key:'jeddah',for_date:'2026-10-20',called_when:'day_ahead',actual_band:'35°C'},
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
const nyc = yesterday.details.find(r=>r.city_key==='nyc');
assert.equal(nyc.actual_band,'68–69°F','the imported venue result is visible beside its city');
assert.equal(nyc.event_slug,'highest-temperature-in-nyc-on-2026-10-20');
const unread = settlementCoverage(markets,cities,[],'noon',now,false)[1];
assert.equal(unread.awaiting_score,0,'an unfinished or failed result read cannot claim records are missing');
assert.equal(unread.record_unavailable,2,'venue confirmations remain visible while result status is unknown');
assert.equal(unread.awaiting_venue,1,'upstream pending status does not depend on reading prediction history');
const stale = settlementCoverage(markets,cities,calls,'noon',now,false)[1];
assert.equal(stale.recorded,0,'results retained from another in-flight selection do not count as a complete read');
assert.equal(stale.details.find(r=>r.city_key==='nyc').actual_band,null);
const ungraded = settlementCoverage(markets,cities,[{city_key:'london',for_date:'2026-10-20',
  called_when:'noon',actual_band:null}],'noon',now)[1];
assert.equal(ungraded.recorded,0,'a checkpoint without a venue winner is not a recorded settlement');
assert.equal(ungraded.details.find(r=>r.city_key==='london').state,'awaiting_venue');
assert.equal(settlementCoverage(markets,[cities[0]],calls,'day_ahead',now)[1].recorded,1);
assert.equal(settlementCoverage(markets,[cities[0]],calls,'noon',now)[1].awaiting_score,1);
assert.equal(settlementCoverage(markets,[{...cities[1],timezone:null}],[],'noon',now)[0].clock_unavailable,1);
assert.equal(settlementCoverage(markets,[{...cities[1],timezone:'invalid'}],[],'noon',now)[0].clock_unavailable,1);
assert.deepEqual(settlementCoverage(markets,[],calls,'noon',now),[]);
console.log('PASS: recent pending dates remain visible with city-local clocks and selected-moment coverage');
