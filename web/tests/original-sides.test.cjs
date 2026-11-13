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
  assert.equal((await route.GET(request('/api/original-sides?op=regression-audit'))).status,401);
  assert.equal((await route.POST(request('/api/original-sides',{op:'resolve',rows:[]}))).status,401);
 }assert.deepEqual(calls,[]);}finally{auth.serviceClient=prior;delete process.env.OPERATOR_SIGN_IN;}
});
test('audit reads require an owner, preserve evidence and pass bounded keyset parameters only',async()=>{
 const prior=auth.serviceClient,calls=[],payload={rows:[{source_as_of:'synthetic',recent:{hit_rate:null,claimed:.725},daily_details_complete:false}],has_more:true,next_cursor:{city_key:'synthetic',checkpoint:'noon'}};
 auth.serviceClient=()=>({rpc:async(name,params)=>{calls.push([name,params]);return{data:name==='verify_original_side_session'?owner:payload,error:null};}});
 try{
  const response=await route.GET(request('/api/original-sides?op=regression-audit&city=synthetic&checkpoint=d1_eve&limit=5&flagged=false&after_city=earlier&after_checkpoint=noon&p_owner=attacker',null,true));
  assert.equal(response.status,200);assert.deepEqual((await response.json()).data,payload);
  assert.match(response.headers.get('cache-control'),/private.*no-store/);assert.equal(response.headers.get('vary'),'Cookie, Authorization');
  assert.deepEqual(calls[1],['predictive_regression_audit',{p_city_key:'synthetic',p_checkpoint:'d1_eve',p_limit:5,p_after_city:'earlier',p_after_checkpoint:'noon',p_flagged_only:false}]);
  calls.length=0;
  await route.GET(request('/api/original-sides?op=regression-audit',null,true));
  assert.deepEqual(calls[1][1],{p_city_key:null,p_checkpoint:null,p_limit:20,p_after_city:null,p_after_checkpoint:null,p_flagged_only:true});
 }finally{auth.serviceClient=prior;}
});
test('invalid audit scopes never reach the audit RPC',async()=>{
 const prior=auth.serviceClient,calls=[];auth.serviceClient=()=>({rpc:async name=>{calls.push(name);return{data:owner,error:null};}});
 try{for(const suffix of ['limit=0','limit=26','limit=1.5','limit=1e1','limit=','flagged=maybe','checkpoint=overall','city=bad%20city','after_city=a','after_checkpoint=noon','after_city=&after_checkpoint=noon','after_city=a&after_checkpoint=overall']){
  assert.equal((await route.GET(request('/api/original-sides?op=regression-audit&'+suffix,null,true))).status,400,suffix);
 }assert.equal(calls.includes('predictive_regression_audit'),false);}finally{auth.serviceClient=prior;}
});
test('audit size errors, missing data and revoked sessions do not masquerade as completed empty reports',async()=>{
 const prior=auth.serviceClient;let state='size',auditCalls=0;
 auth.serviceClient=()=>({rpc:async name=>{
  if(name==='verify_original_side_session')return{data:state==='revoked'?null:owner,error:null};
  auditCalls++;return state==='size'?{data:null,error:{message:'Audit exceeds 512 KiB; request a smaller page'}}:{data:null,error:null};
 }});
 try{let response=await route.GET(request('/api/original-sides?op=regression-audit',null,true));
  assert.equal(response.status,503);assert.match((await response.json()).error.message,/smaller page/);
  state='missing';response=await route.GET(request('/api/original-sides?op=regression-audit',null,true));assert.equal(response.status,503);
  state='revoked';response=await route.GET(request('/api/original-sides?op=regression-audit',null,true));assert.equal(response.status,401);assert.equal(auditCalls,2);
 }finally{auth.serviceClient=prior;}
});
test('private audit cards preserve nulls, uncertainty, probability confidence and cohort identities',()=>{
 const audit=require('../.route-test/components/RegressionAudit.js');
 const row={city_key:'synthetic_city',checkpoint:'d1_eve',source_as_of:'2026-01-02T03:04:05Z',source_report_fingerprint:'synthetic-proof',
  flags:['hit_rate_drop'],findings:['hit_and_probability_scores_diverge'],recent:{start:'2026-01-01',end_exclusive:'2026-01-08',n:5,hits:0,hit_rate:0,claimed:.725,pending:null,market:{n:2,model_hit_rate:0,market_hit_rate:.5}},prior:{},
  hit_rate_change:null,comparable_dates_ready:false,learning:[{phase:'synthetic',active:false,train_dates:0,validation_dates:2,readiness:{sample_ready:false,validation_window_days:14,counts:[{name:'training dates',available:0,required:20,missing:20}]}}],
  retained_daily_details:[{date:'2026-01-01',n:1,hits:0,hit_rate:0,pending:null,early_comparison:null}],daily_details_complete:false,causal_attribution_confirmed:false};
 const before=JSON.stringify(row),html=renderToStaticMarkup(React.createElement(audit.RegressionAuditCard,{row}));
 assert.match(html,/2026-01-08 \(end excluded\)/);assert.match(html,/mean stated confidence 72.5%/);
 assert.match(html,/Not measured pending/);assert.match(html,/0.0%/);assert.match(html,/synthetic-proof/);
 assert.match(html,/Evidence gap/);assert.match(html,/0\/20 \(20 missing\)/);assert.match(html,/Held \/ inactive/);
 assert.match(html,/not executable returns/);assert.match(html,/served adaptations are scored separately/);assert.match(html,/do not establish a cause/);
 assert.equal(JSON.stringify(row),before);
 const closed=renderToStaticMarkup(React.createElement(audit.default,{cities:['synthetic_city']}));
 assert.match(closed,/Open private audit/);assert.doesNotMatch(closed,/synthetic-proof|Cells per page/);
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

test('audit interaction keeps pages scoped, clears expired access and makes no collapsed or timer reads',async()=>{
 const {JSDOM}=require('jsdom'),dom=new JSDOM('<div id="audit-root"></div>',{url:site,pretendToBeVisual:true});
 const priorWindow=global.window,priorDocument=global.document,priorAct=global.IS_REACT_ACT_ENVIRONMENT;
 global.window=dom.window;global.document=dom.window.document;global.IS_REACT_ACT_ENVIRONMENT=true;
 const {createRoot}=require('react-dom/client'),{act}=React;
 const evidence=require('../.route-test/components/OriginalSideResults.js'),priorRead=evidence.privateSideRead;
 const Audit=require('../.route-test/components/RegressionAudit.js').default;
 const calls=[],page=label=>({policy:'synthetic',rows:[{city_key:label,checkpoint:'noon',source_as_of:'synthetic timestamp',source_report_fingerprint:'synthetic',flags:[],findings:[],recent:{},prior:{},hit_rate_change:null,comparable_dates_ready:false,learning:[],retained_daily_details:[],daily_details_complete:false,causal_attribution_confirmed:false}],has_more:label==='first',next_cursor:label==='first'?{city_key:'first',checkpoint:'noon'}:null,changes_serving:false,changes_training_gates:false,interpretation:'Synthetic test only'});
 let reply=async url=>({data:page(new URL(url,site).searchParams.has('after_city')?'second':'first'),error:null});
 evidence.privateSideRead=async url=>{calls.push(url);return reply(url);};
 const host=dom.window.document.getElementById('audit-root'),root=createRoot(host);
 const button=text=>[...host.querySelectorAll('button')].find(b=>b.textContent===text);
 const select=(label)=>[...host.querySelectorAll('label')].find(l=>l.textContent.startsWith(label)).querySelector('select');
 const click=async text=>{await act(async()=>button(text).dispatchEvent(new dom.window.MouseEvent('click',{bubbles:true})));};
 const change=async(label,value)=>{await act(async()=>{const s=select(label);s.value=value;s.dispatchEvent(new dom.window.Event('change',{bubbles:true}));});};
 const toggle=async open=>{await act(async()=>{const d=host.querySelector('details');d.open=open;d.dispatchEvent(new dom.window.Event('toggle'));});};
 try{
  await act(async()=>root.render(React.createElement(Audit,{cities:['alpha','beta']})));
  assert.equal(calls.length,0);await toggle(true);assert.equal(calls.length,1);assert.match(host.textContent,/Page 1/);
  assert.equal(new URL(calls[0],site).searchParams.get('limit'),'20');
  await click('Next audit page');assert.equal(calls.length,2);assert.match(host.textContent,/Page 2/);assert.match(host.textContent,/second ·/);
  const second=new URL(calls[1],site);assert.equal(second.searchParams.get('after_city'),'first');assert.equal(second.searchParams.get('after_checkpoint'),'noon');
  assert.equal(button('Next audit page').disabled,true);
  await click('Previous audit page');assert.match(host.textContent,/Page 1/);
  await click('Next audit page');await change('Audit city','alpha');
  assert.match(host.textContent,/Page 1/);assert.equal(new URL(calls.at(-1),site).searchParams.has('after_city'),false);assert.equal(new URL(calls.at(-1),site).searchParams.get('city'),'alpha');
  // A slower replacement page must never retain the prior cell under its new filter.
  let finish;reply=()=>new Promise(resolve=>{finish=resolve;});await change('Audit checkpoint','d1_eve');
  assert.match(host.textContent,/Reading private audit evidence/);assert.doesNotMatch(host.textContent,/first ·|second ·/);assert.equal(button('Next audit page').disabled,true);
  await act(async()=>finish({data:page('replacement'),error:null}));assert.match(host.textContent,/replacement ·/);
  reply=async()=>({data:null,error:{message:'Owner session expired'}});await click('Refresh audit from first page');
  assert.match(host.textContent,/Audit unavailable: Owner session expired/);assert.doesNotMatch(host.textContent,/replacement ·|end of this scope/);
  reply=async()=>({data:page('first'),error:null});await act(async()=>dom.window.dispatchEvent(new dom.window.Event('ad4-private-evidence')));
  assert.match(host.textContent,/Page 1/);await click('Next audit page');
  await act(async()=>dom.window.dispatchEvent(new dom.window.Event('ad4-private-evidence')));assert.match(host.textContent,/Page 1/);
  const before=calls.length;await act(async()=>dom.window.document.dispatchEvent(new dom.window.Event('visibilitychange')));assert.equal(calls.length,before);
  await toggle(false);assert.doesNotMatch(host.textContent,/first ·|Page 1/);assert.equal(calls.length,before);
  await toggle(true);assert.equal(calls.length,before+1);
  await change('Cells per page','5');assert.equal(new URL(calls.at(-1),site).searchParams.get('limit'),'5');
 }finally{
  await act(async()=>root.unmount());evidence.privateSideRead=priorRead;dom.window.close();
  global.window=priorWindow;global.document=priorDocument;global.IS_REACT_ACT_ENVIRONMENT=priorAct;
 }
});
