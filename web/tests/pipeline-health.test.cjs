const assert=require('node:assert/strict');
const {observationFeed,feedState}=require('../.route-test/lib/pipelineHealth.js');
const now=Date.parse('2026-10-18T16:45:00Z');
const feed={jobs:['tick','ingest_observations'],mode:'either',staleAfterH:2};
const tick={job:'tick',status:'attention',logged_at:'2026-10-18T16:37:00Z',rows:null,
 detail:{observations:{rows:400,cities:47,error:null,stale:[],silent:[],iem_stale:[]}}};
const daily={job:'ingest_observations',status:'ok',logged_at:'2026-10-18T05:36:00Z',rows:100};
assert.equal(feedState(feed,new Map([['tick',observationFeed(tick)],['ingest_observations',daily]]),now).status,'ok');
assert.equal(observationFeed({...tick,detail:{}}),null);
const failed=observationFeed({...tick,detail:{observations:{rows:0,cities:0,error:'station unavailable'}}});
assert.equal(feedState(feed,new Map([['tick',failed],['ingest_observations',daily]]),now).status,'attention');
assert.equal(feedState(feed,new Map([['ingest_observations',daily]]),now).status,'stale');
assert.equal(observationFeed({...tick,detail:{observations:{rows:400,cities:47,stale:['karachi']}}}).status,'attention');
const both={jobs:['probability_engine','edge_engine'],mode:'both',staleAfterH:8};
assert.equal(feedState(both,new Map([['probability_engine',{...daily,job:'probability_engine',logged_at:tick.logged_at}],
 ['edge_engine',{...daily,job:'edge_engine',status:'error',logged_at:tick.logged_at}]]),now).status,'attention');
console.log('PASS: station feed follows actual tick observations and exposes missing, stale and failed writers');
