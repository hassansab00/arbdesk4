const {test}=require('node:test'),assert=require('node:assert/strict');
const auth=require('../.route-test/lib/operatorAuth.js');
const route=require('../.route-test/app/api/original-sides/route.js');
const adapter=require('../.route-test/lib/originalSides.js');
const React=require('react'),{renderToStaticMarkup}=require('react-dom/server');
const view=require('../.route-test/components/OriginalSideResults.js');
const site='https://desk.example',owner='f0000000-0000-0000-0000-000000000001',token='a'.repeat(64);
function request(url='/api/original-sides?op=desk',body,authorized=false){return new Request(site+url,{method:body?'POST':'GET',headers:{origin:site,...(authorized?{cookie:'ad4-original-side-owner='+token}:{}),'content-type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});}
test('anonymous callers never reach private evidence, regardless of legacy sign-in mode',async()=>{
 const prior=auth.serviceClient;let calls=[];auth.serviceClient=()=>({rpc:async(n)=>{calls.push(n);return{data:null,error:null};}});
 try{for(const mode of [undefined,'required']){if(mode)process.env.OPERATOR_SIGN_IN=mode;else delete process.env.OPERATOR_SIGN_IN;
  assert.equal((await route.GET(request())).status,401);
  assert.equal((await route.POST(request('/api/original-sides',{op:'resolve',rows:[]}))).status,401);
 }assert.deepEqual(calls,[]);}finally{auth.serviceClient=prior;delete process.env.OPERATOR_SIGN_IN;}
});
test('owner receipt installs a private HttpOnly cookie and never funds or activates a policy',async()=>{
 const prior=auth.serviceClient,calls=[];auth.serviceClient=()=>({rpc:async(n)=>{calls.push(n);return{data:owner,error:null};}});
 try{const response=await route.POST(request('/api/original-sides',{op:'session',token}));assert.equal(response.status,200);
  assert.match(response.headers.get('set-cookie'),/HttpOnly/i);assert.match(response.headers.get('set-cookie'),/Secure/i);
  assert.match(response.headers.get('set-cookie'),/SameSite=strict/i);assert.match(response.headers.get('cache-control'),/private.*no-store/);
  assert.deepEqual(calls,['verify_original_side_session']);assert.deepEqual((await response.json()).data,{authorized:true});
 }finally{auth.serviceClient=prior;}
});
test('bounded shared history preserves decimal strings, nulls and truncation',async()=>{
 const prior=auth.serviceClient,calls=[];const payload={rows:[{call_id:'immutable',sides:{YES:{hypothetical_net_pnl:null},NO:{paper_orders:[]}}}],truncated:true,comparison_stake:'12.500000'};
 auth.serviceClient=()=>({rpc:async(n,p)=>{calls.push([n,p]);return{data:n==='verify_original_side_session'?owner:payload,error:null};}});
 try{const response=await route.GET(request('/api/original-sides?from=2026-11-01&to=2026-11-11&cities=amsterdam&stake=12.500000',null,true));
  assert.equal(response.status,200);assert.deepEqual((await response.json()).data,payload);
  assert.equal(calls[1][0],'read_original_side_history');assert.equal(calls[1][1].p_owner,owner);assert.equal(calls[1][1].p_stake,'12.500000');assert.equal(calls[1][1].p_limit,100);
  assert.equal((await route.GET(request('/api/original-sides?from=2026-01-01&to=2026-11-11',null,true))).status,400);
  assert.equal((await route.POST(request('/api/original-sides',{op:'resolve',rows:Array(301).fill({})},true))).status,400);
 }finally{auth.serviceClient=prior;}
});
test('private write commands supply only the verified owner and saving stays inactive',async()=>{
 const prior=auth.serviceClient,gate=auth.requireOperator,calls=[];
 auth.requireOperator=async()=>({ok:true,email:'synthetic'});
 auth.serviceClient=()=>({rpc:async(n,p)=>{calls.push([n,p]);return{data:n==='verify_original_side_session'?owner:'new-policy',error:null};}});
 try{const response=await route.POST(request('/api/original-sides',{op:'save',config:adapter.MIXED_SIDE_PRESET,p_owner:'attacker'},true));
  assert.equal(response.status,200);assert.equal((await response.json()).data.state,'inactive');assert.equal(calls[1][1].p_owner,owner);
  assert.equal(calls.some(([name])=>name==='set_original_side_policy_state'),false);
 }finally{auth.serviceClient=prior;auth.requireOperator=gate;}
});
test('transport errors stay unavailable, not an empty successful history',async()=>{
 const prior=auth.serviceClient;auth.serviceClient=()=>({rpc:async()=>{throw Error('synthetic network error');}});
 try{assert.equal((await route.GET(request('/api/original-sides?op=desk',null,true))).status,503);}finally{auth.serviceClient=prior;}
});
test('render preserves the original bucket, NO winner and prediction miss independently',()=>{
 const side=s=>({side:s,token_id:s,quote_kind:'missing',quote_id:null,quote_status:'not_recorded',reason:null,quote_requested_at:null,quote_received_at:null,book_observed_at:null,snapshot_id:null,side_outcome:s==='NO'?'won':'lost',comparison_stake:'10',calculation_version:'synthetic',evaluation:null,paper_orders:[],hypothetical_net_pnl:null});
 const call={call_id:'synthetic-call',city_key:'amsterdam',target_date:'2026-11-11',checkpoint:'d1_eve',source_kind:'prediction_checkpoints',predicted_band_id:'original-band',predicted_band_label:'10°C',frozen_at:'2026-11-10T17:00:00Z',called_at:'2026-11-10T17:00:00Z',timezone:'Europe/Amsterdam',station:'EHAM',engine_version:'synthetic',eligibility:'eligible',prediction_outcome:'miss',settlement_state:'verified_binary',settlement_receipt_id:'synthetic-proof',sides:{YES:side('YES'),NO:side('NO')}};
 const html=renderToStaticMarkup(React.createElement(view.OriginalSideCallCard,{call}));
 assert.match(html,/Prediction:.*Miss/);assert.match(html,/Buy YES · 10°C/);assert.match(html,/Buy NO · 10°C/);
 assert.match(html,/won/);assert.doesNotMatch(html,/\$0\.00/);assert.match(html,/No recorded order or fill/);
 assert.equal(adapter.sideMoney(null),'Not recorded');assert.equal(adapter.sideMoney('0'),'$0.00');
 assert.equal(adapter.MIXED_SIDE_PRESET.slots.filter(x=>x.side==='NO').length,4);
});
