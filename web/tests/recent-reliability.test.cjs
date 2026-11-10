const assert = require('node:assert/strict');
const { recentReliability } = require('../.route-test/lib/recentReliability.js');
const row = (city, date, predicted, market, hit, marketHit, head_to_head=true) =>
  ({ city_key:city, for_date:date, model_call:predicted, market_call:market,
    model_hit:hit, market_hit:marketHit, head_to_head });
const rs = [
  row('a','2026-11-08','2','2',true,true),
  row('a','2026-11-09','2','3',false,true),
  row('b','2026-11-09','2','3',true,false),
  row('c','2026-11-09','2','3',null,null), // pending is never a loss
  row('a','2026-11-09','2','3',false,true,false), // unpaired row excluded
  row('a','2026-11-07','2','3',false,true), // outside two-date window
  row('b','2026-11-09','2','3',true,false), // duplicate city-day is not double counted
];
assert.deepEqual(recentReliability(rs,['a','b','c'],2), {
  dates:['2026-11-09','2026-11-08'], n:3, modelHits:2, marketHits:2,
  disagreements:2, modelDisagreementHits:1, marketDisagreementHits:1,
});
assert.equal(recentReliability(rs,['c'],7).n,0);
assert.equal(recentReliability(rs,[],7).n,0);
