const {test}=require('node:test');const assert=require('node:assert/strict');
const auth=require('../.route-test/lib/operatorAuth.js');
const route=require('../.route-test/app/api/temperature-research/route.js');
const {projectTemperatureResearch:project,temperatureResearchPhase:phase}=require('../.route-test/lib/temperatureResearch.js');
const now=Date.parse('2026-11-06T12:00:00Z'),sha='a'.repeat(64);
const snapshot=()=>({models:[{state:'unfitted_reference',sha256:sha,registered_at:'2026-11-06T10:00:00Z',cohort:['one','two'],evaluation_first:'2026-11-08',evaluation_last:'2026-11-27',artifact:{coefficients:['private']}}],calls:0,scored:0,status:{updated_at:'2026-11-06T11:59:00Z',detail:{state:'unfitted_reference',model_sha256:sha,skipped:{}}},learning:{detail:{dates:0,eligible_rows:0}},raw_packets:['private']});
test('public summary excludes packets, artifacts, raw errors and coefficients',()=>{
 const input=snapshot();input.status.detail.skipped={'invalid_input:secret internals':2};input.status.detail.error='private database details';
 const result=project(input),text=JSON.stringify(result);
 assert.equal(result.model.cities,2);assert.equal(result.capture.blocked,2);assert.equal(result.capture.error,true);
 assert.ok(!text.includes('private')&&!text.includes('secret internals')&&!text.includes('raw_packets'));
 assert.equal(result.capture.skipped[0].label,'Input validation failed');
});
test('zero calls, a verification cap, stale reports and old-model heartbeats cannot appear validated',()=>{
 const input=snapshot();let result=project(input);
 assert.equal(phase(result,now).label,'Waiting for qualified captures');
 input.status.detail.skipped={bounded_verification:2};result=project(input);
 assert.equal(result.capture.blocked,0);assert.equal(phase(result,now).warning,false);
 input.status.detail.skipped={missing_received_weather:2};assert.equal(phase(project(input),now).label,'Capture inputs blocked');
 input.status.detail.skipped={};input.status.updated_at='2026-11-06T08:00:00Z';assert.equal(phase(project(input),now).label,'Capture report overdue');
 input.status.updated_at='2026-11-06T11:59:00Z';input.status.detail.model_sha256='b'.repeat(64);assert.equal(phase(project(input),now).label,'Awaiting current model run');
 input.status.detail.model_sha256=sha;input.calls=2000;input.scored=2000;input.learning.detail={dates:20,eligible_rows:2000,validated:true};
 assert.equal(phase(project(input),now).label,'Collecting training evidence');
});
test('route invokes only the safe status RPC and distinguishes unavailable from empty',async()=>{
 const prior=auth.serviceClient,called=[];
 try {
  auth.serviceClient=()=>null;assert.equal((await route.GET()).status,503);
  auth.serviceClient=()=>({rpc:async name=>{called.push(name);return {data:snapshot(),error:null};}});
  let response=await route.GET();assert.equal(response.status,200);assert.deepEqual(called,['temperature_snapshot']);assert.ok(!(await response.text()).includes('coefficients'));
  auth.serviceClient=()=>({rpc:async()=>({data:{},error:null})});assert.equal((await route.GET()).status,503);
  auth.serviceClient=()=>({rpc:async()=>{throw new Error('private failure');}});response=await route.GET();assert.equal(response.status,503);assert.ok(!(await response.text()).includes('private failure'));
 } finally {auth.serviceClient=prior;}
});
