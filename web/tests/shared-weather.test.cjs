const {test}=require('node:test');const assert=require('node:assert/strict');
const auth=require('../.route-test/lib/operatorAuth.js');
const route=require('../.route-test/app/api/shared-weather/route.js');
test('missing server configuration is an explicit unavailable state',async()=>{
 const prior=auth.serviceClient;auth.serviceClient=()=>null;
 try{assert.equal((await route.GET()).status,503);}finally{auth.serviceClient=prior;}
});
test('the reader calls only the safe projection, never the private artifact RPC',async()=>{
 const prior=auth.serviceClient,called=[];
 auth.serviceClient=()=>({rpc:async name=>{called.push(name);return {data:{state:'forward_test',calls:[],scores:[]},error:null};}});
 try{const r=await route.GET();assert.equal(r.status,200);assert.deepEqual(called,['shared_weather_snapshot']);
 assert.equal((await r.json()).state,'forward_test');}finally{auth.serviceClient=prior;}
});
test('transport failures cannot look like an empty successful model',async()=>{
 const prior=auth.serviceClient;auth.serviceClient=()=>({rpc:async()=>{throw new Error('synthetic network failure')}});
 try{const r=await route.GET();assert.equal(r.status,503);assert.match((await r.json()).error,/Could not reach/);}finally{auth.serviceClient=prior;}
});
