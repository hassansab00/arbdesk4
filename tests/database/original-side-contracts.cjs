const assert=require('node:assert/strict');
module.exports=async function(db){
 const q=async(s,p)=>(await db.query(s,p)).rows;
 const fs=require('node:fs'),path=require('node:path');
 const v1fix=fs.readdirSync(path.join(__dirname,'../../supabase/migrations')).find(x=>x.endsWith('_day_max_v1_source_identity.sql'));
 await db.exec(fs.readFileSync(path.join(__dirname,'../../supabase/migrations',v1fix),'utf8'));
 const owner='f5555555-0000-0000-0000-000000000001';
 const mid='f5555555-0000-0000-0000-000000000002',band='f5555555-0000-0000-0000-000000000003',other='f5555555-0000-0000-0000-000000000004';
 const offset=12-new Date().getUTCHours(),zone=offset===0?'UTC':`Etc/GMT${offset>0?'-':'+'}${Math.abs(offset)}`;
 await db.exec(`insert into auth.users(id) values('${owner}');
  insert into public.cities(city_key,display_name,timezone,unit,status,icao) values('side_fixture','Side fixture','${zone}','C','active','TEST');
  insert into public.markets(market_id,closed,resolution_date,city_key,unit,event_slug,rules_text,rules_fetched_at)
   values('${mid}',false,(now() at time zone '${zone}')::date,'side_fixture','C','side-fixture','https://www.weather.gov/wrh/timeseries?site=TEST whole degrees Celsius hourly data',now()-interval '1 hour');
  insert into public.bands(band_id,market_id,band_index,band_label,band_lo,band_hi,open_low,open_high,token_yes,token_no,condition_id)
   values('${band}','${mid}',0,'20°C',20,21,false,false,'side-yes','side-no','side-condition'),
   ('${other}','${mid}',1,'21°C',21,22,false,false,'other-yes','other-no','other-condition');`);
 const config={version:'original-bucket-sides-v2.1',starting_cash:'100',funding:'settled_equal_compound',fixed_stake:'10',derived_no_execution:false,
  slots:[{city_key:'side_fixture',side:'NO',checkpoint:'noon'}]};
 const pid=(await q('select public.save_original_side_policy($1,$2) id',[owner,config]))[0].id;
 assert.equal((await q('select count(*) n from arbdesk_private.original_side_accounts'))[0].n,0,'saving never funds or activates');
 const account=(await q('select public.set_original_side_policy_state($1,$2,$3) r',[owner,pid,'activate']))[0].r.account_id;
 const again=(await q('select public.set_original_side_policy_state($1,$2,$3) r',[owner,pid,'activate']))[0].r;
 assert.equal(again.account_id,account,'activation is one initial contribution, never a top-up');
 const p=(await q(`insert into public.prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,engine_version,model_path,probs,top_band_id,top_prob,inputs_ok,predictive_context,forecast_issued_at,station)
  values('side_fixture',(now() at time zone '${zone}')::date,'noon',(now() at time zone '${zone}')::date+time '12:00','side-fixture','forecast',$1,$2,.7,true,$3,now()-interval '5 minutes','TEST') returning *`,[JSON.stringify({[band]:.7,[other]:.3}),band,
   {clim_peak_hour:18,band_ids:[band,other],bands_lo:'20;21',bands_hi:'21;22'}]))[0];
 const call=(await q('select public.register_original_side_call($1) r',[p.checkpoint_id]))[0].r;
 assert.equal((await q('select count(*) n from arbdesk_private.original_side_calls where call_id=$1',[p.checkpoint_id]))[0].n,1,'native AFTER INSERT retains identity without a later observation stage');
 assert.equal(call.eligibility,'eligible');assert.equal(call.identity.predicted_band_id,band);
 assert.equal(call.source.probs[band],.7,'canonical p stays YES probability');
 const ladder=[{band_id:band,band_lo:20,band_hi:21},{band_id:other,band_lo:21,band_hi:22}];
 const arrayContext={band_ids:[band,other],bands_lo:'20;21',bands_hi:'21;22'};
 const matches=async(ctx,bands=ladder)=>(await q('select arbdesk_private.original_side_ladder_matches($1,$2) ok',[ctx,bands]))[0].ok;
 assert.equal(await matches(arrayContext),true,'real intraday JSON IDs match the same saved geometry');
 assert.equal(await matches({...arrayContext,band_ids:[band,other].join(';')}),true,'legacy delimited IDs retain parity');
 assert.equal(await matches({...arrayContext,bands_lo:[20,21],bands_hi:[21,22]}),true);
 assert.equal(await matches({...arrayContext,band_ids:[other,band],bands_lo:'21;20',bands_hi:'22;21'}),true,'bounds follow saved ordinal IDs, not canonical sort order');
 assert.equal(await matches({band_ids:[band,other],bands_lo:';21',bands_hi:'21;'},[{band_id:band,band_lo:null,band_hi:21},{band_id:other,band_lo:21,band_hi:null}]),true,'open tails preserve null bounds');
 for(const ctx of [
  {...arrayContext,band_ids:[band,band]}, {...arrayContext,band_ids:[band]},
  {...arrayContext,band_ids:[band,'unknown']}, {...arrayContext,band_ids:null},
  {...arrayContext,band_ids:{id:band}}, {...arrayContext,bands_lo:'20'},
  {...arrayContext,bands_lo:'20;20'}, {...arrayContext,bands_hi:'21;23'},
  {...arrayContext,bands_lo:'bad;21'}, {...arrayContext,bands_hi:null},
 ]) assert.equal(await matches(ctx),false,'invalid encoding or genuinely changed geometry stays blocked');
 assert.equal(await matches({clim_peak_hour:18}),true,'older calls without saved geometry keep their existing probability-identity checks');
 const wrongSource='f5555555-0000-0000-0000-000000000014';
 await q(`insert into arbdesk_private.original_side_calls(call_id,city_key,target_date,checkpoint,source_kind,frozen_at,source,identity,eligibility,cutoff_at,registered_at)
  select $1::uuid,city_key,target_date,checkpoint,'day_max_day_ahead_calls',frozen_at,source,identity,eligibility,cutoff_at,registered_at
  from arbdesk_private.original_side_calls where call_id=$2`,[wrongSource,p.checkpoint_id]);
 await assert.rejects(()=>q('select public.reserve_original_side_date($1,$2)',[account,wrongSource]),/native_named_checkpoint/);
 function makeBook(side,price){
  const observed=new Date().toISOString();const raw={asset_id:side==='YES'?'side-yes':'side-no',timestamp:String(new Date(observed).getTime()),asks:[{price,size:'1000'}],bids:[{price:'.3',size:'1000'}],tick_size:'.001',min_order_size:'5'};
  return {token_id:raw.asset_id,quote_kind:'direct',observed_at:observed,received_at:observed,tradeable:true,asks:raw.asks,bids:raw.bids,tick_size:'.001',share_step:'.01',fee_rate:'0',fee_rounding:'half_up_5dp',min_order_size:'5',min_order_size_unit:'shares',raw_book:raw,
   current_rules_text:'https://www.weather.gov/wrh/timeseries?site=TEST whole degrees Celsius hourly data',current_rules_received_at:observed,rules_source:'https://gamma-api.polymarket.com/events/slug/side-fixture',
   market_metadata:{conditionId:'side-condition',clobTokenIds:['side-yes','side-no'],outcomes:['Yes','No'],active:true,closed:false,acceptingOrders:true,feesEnabled:false}};
 }
 const yesBook=makeBook('YES','.4'),noBook=makeBook('NO','.65');
 const quote=async(side,book,id)=>q('select public.record_original_side_quote($1,$2,$3,clock_timestamp(),$4,null) r',[p.checkpoint_id,side,id,book]);
 const yesId='f5555555-0000-0000-0000-000000000005',noId='f5555555-0000-0000-0000-000000000006';
 await quote('YES',yesBook,yesId);
 const emptyNo={...noBook,asks:[],raw_book:{...noBook.raw_book,asks:[]}};
 await quote('NO',emptyNo,'f5555555-0000-0000-0000-000000000010');
 assert.equal((await q('select arbdesk_private.original_side_observation_complete($1,$2) done',[p.checkpoint_id,'NO']))[0].done,false,'empty direct NO book remains retryable');
 await quote('NO',noBook,noId);await quote('NO',noBook,noId);
 assert.equal((await q('select arbdesk_private.original_side_observation_complete($1,$2) done',[p.checkpoint_id,'NO']))[0].done,true);
 assert.equal((await q('select count(*) n from arbdesk_private.original_side_quotes'))[0].n,3,'retry append identity is idempotent and missing depth remains visible');
 const calc=(await q('select arbdesk_private.original_side_calculate($1,10) r',[noBook]))[0].r;
 assert.equal(Number(calc.shares),15.38);assert.equal(Number(calc.filled_cost),9.997);assert.equal(Number(calc.profit_if_win),5.383);
 const multi={...yesBook,asks:[{price:'.4',size:'10'},{price:'.65',size:'100'}]};
 const multiCalc=(await q('select arbdesk_private.original_side_calculate($1,10) r',[multi]))[0].r;
 assert.equal(Number(multiCalc.best_ask),.4);assert.equal(Number(multiCalc.limit_price),.65);
 assert.ok(Math.abs(Number(multiCalc.vwap)-9.9995/19.23)<1e-14,'independent cost/shares agrees within JavaScript display precision');
 const feeCalc=(await q('select arbdesk_private.original_side_calculate($1,10) r',[{...yesBook,fee_rate:'.05'}]))[0].r;
 assert.equal(Number(feeCalc.shares),24.27);assert.equal(Number(feeCalc.filled_cost),9.99924);
 await assert.rejects(()=>quote('NO',{...noBook,token_id:'side-yes'},'f5555555-0000-0000-0000-000000000007'),/token|condition/);
 await assert.rejects(()=>quote('NO',{...noBook,quote_kind:'derived_scenario'},'f5555555-0000-0000-0000-000000000008'),/token|condition/);
 await assert.rejects(()=>q('select public.publish_original_side_order($1,$2,$3)',[account,p.checkpoint_id,yesId]),/outside_frozen_policy/);
 const published=(await q('select public.publish_original_side_order($1,$2,$3) r',[account,p.checkpoint_id,noId]))[0].r;
 const retry=(await q('select public.publish_original_side_order($1,$2,$3) r',[account,p.checkpoint_id,noId]))[0].r;
 assert.equal(published.order_id,retry.order_id);assert.equal(retry.duplicate,true);
 const decision=(await q('select * from arbdesk_private.original_side_decisions'))[0];
 assert.equal(Number(decision.model_prob_yes),.7);assert.equal(Number(decision.selected_side_probability),.3,'NO complement occurs exactly once');
 const order=(await q('select public.claim_original_side_order() r'))[0].r;
 assert.equal(order.token_id,'side-no');assert.equal(order.band_id,band);assert.equal(order.side,'NO');
 assert.equal((await q('select public.claim_original_side_order() r'))[0].r,null,'one live lease per account');
 // A fresh server receipt may observe byte-identical depth already captured
 // before the order. It must retain that depth identity, not reject it as old.
 const fillBook={...noBook,requested_at:new Date().toISOString()};
 const saved=(await q('select public.record_original_side_execution_book($1,$2) r',[order.order_id,fillBook]))[0].r;
 const fillCalc=(await q('select arbdesk_private.original_side_calculate($1,100) r',[fillBook]))[0].r;
 const result={status:'filled',shares:fillCalc.shares,notional:fillCalc.notional,fee:fillCalc.fee,fills:fillCalc.fills,snapshot_id:saved.snapshot_id,capture_attempt_id:saved.capture_attempt_id,simulated_at:new Date().toISOString(),engine_version:'synthetic-fixture'};
 assert.ok(saved.capture_attempt_id,'execution links its own post-order request/receipt');
 await assert.rejects(()=>q('select public.complete_original_side_order($1,$2,$3)',[order.order_id,order.lease_token,{...result,capture_attempt_id:noId}]),/fresh_direct_execution/);
 await assert.rejects(()=>q('select public.complete_original_side_order($1,$2,$3)',[order.order_id,order.lease_token,{...result,fee:'1'}]),/arithmetic|totals|ceiling/);
 await q('select public.complete_original_side_order($1,$2,$3)',[order.order_id,order.lease_token,result]);
 await q('select public.complete_original_side_order($1,$2,$3)',[order.order_id,order.lease_token,result]);
 assert.equal((await q('select count(*) n from arbdesk_private.original_side_execution_events'))[0].n,1);
 const balance=(await q('select cash,reserved_cash from arbdesk_private.original_side_accounts'))[0];
 assert.equal(Number(balance.reserved_cash),0);assert.ok(Number(balance.cash)>=0);
 const view=(await q("select public.read_original_side_history($1,current_date-1,current_date+1,array['side_fixture'],null,$2,10,0,100) r",[owner,account]))[0].r;
 assert.equal(view.total_calls,2,'two explicit synthetic source IDs stay distinct; YES/NO attempts do not multiply either prediction');
 const originalView=view.rows.find(r=>r.call_id===p.checkpoint_id);
 assert.equal(originalView.prediction_outcome,'pending');assert.equal(originalView.sides.NO.hypothetical_net_pnl,null);
 assert.equal(originalView.sides.NO.paper_orders[0].paper_net_pnl,null,'no zero for unverified settlement');
 assert.equal(originalView.sides.NO.quote_id,noId,'comparison uses the first executable own-side quote for this stake, after retaining empty-book attempts');
 assert.equal(originalView.sides.YES.paper_orders.length,0,'observing YES never spends it');
 const hist='f5555555-0000-0000-0000-000000000009';
 // Explicit synthetic historical fixture, never inserted by a live runner.
 await q(`insert into arbdesk_private.original_side_calls(call_id,city_key,target_date,checkpoint,source_kind,frozen_at,source,identity,eligibility,cutoff_at,registered_at)
  select $1::uuid,city_key,target_date-1,checkpoint,source_kind,frozen_at-interval '1 day',
   source||jsonb_build_object('checkpoint_id',($1::uuid)::text,'target_date',target_date-1,'frozen_at',frozen_at-interval '1 day','decided_at',(source->>'decided_at')::timestamptz-interval '1 day'),
   identity||jsonb_build_object('rules_received_at',(identity->>'rules_received_at')::timestamptz-interval '1 day'),eligibility,cutoff_at-interval '1 day',registered_at-interval '1 day'
  from arbdesk_private.original_side_calls where call_id=$2`,[hist,p.checkpoint_id]);
 const gamma={conditionId:'side-condition',closed:true,umaResolutionStatus:'resolved',clobTokenIds:['side-yes','side-no'],outcomes:['Yes','No'],outcomePrices:['0','1']};
 const clob={condition_id:'side-condition',closed:true,accepting_orders:false,tokens:[{token_id:'side-yes',winner:false},{token_id:'side-no',winner:true}]};
 const proof={gamma,clob,rules_text:'https://www.weather.gov/wrh/timeseries?site=TEST whole degrees Celsius hourly data',requested_at:new Date().toISOString(),response_received_at:new Date().toISOString(),
  gamma_source:'https://gamma-api.polymarket.com/markets?condition_ids=side-condition&closed=true',clob_source:'https://clob.polymarket.com/markets/side-condition',rules_source:'https://gamma-api.polymarket.com/events/slug/side-fixture'};
 const settle=async(receipt)=>q('select public.record_original_side_settlement($1,$2) r',[hist,receipt]);
 const settled=(await settle(proof))[0].r;assert.equal(settled.state,'verified_binary');
 assert.equal((await settle(proof))[0].r.duplicate,true,'identical settlement proof cannot pay twice');
 const project=async()=> (await q('select arbdesk_private.original_side_projection($1,$2,null,10) r',[hist,owner]))[0].r;
 let projected=await project();assert.equal(projected.prediction_outcome,'miss');assert.equal(projected.sides.NO.side_outcome,'won');assert.equal(projected.sides.YES.side_outcome,'lost');
 assert.equal(projected.sides.NO.hypothetical_net_pnl,null,'missing historical execution evidence never becomes a zero or imaginary fill');
 await settle({...proof,gamma:{...gamma,umaResolutionStatus:'disputed'}});
 projected=await project();assert.equal(projected.prediction_outcome,'unverified');assert.equal(projected.sides.NO.side_outcome,'disputed');
 const restoredProof=(await settle(proof))[0].r;assert.equal(restoredProof.state,'verified_binary');assert.notEqual(restoredProof.settlement_id,settled.settlement_id,'returning to an earlier proof appends a new revision');
 await settle({...proof,gamma:{...gamma,outcomePrices:['1','0']},clob:{...clob,tokens:[{token_id:'side-yes',winner:true},{token_id:'side-no',winner:false}]}});
 projected=await project();assert.equal(projected.prediction_outcome,'hit');assert.equal(projected.sides.NO.side_outcome,'lost');
 await settle({...proof,gamma:{...gamma,outcomePrices:['.5','.5']},clob:{...clob,tokens:[{token_id:'side-yes',winner:false},{token_id:'side-no',winner:false}]}});
 projected=await project();assert.equal(projected.prediction_outcome,'pending');assert.equal(projected.sides.NO.side_outcome,'pending','a split is never falsely inverted into NO winning');
 // Synthetic payout protocol fixture. Both independent providers bind the
 // same confirmed block, condition, collateral, collections and token IDs.
 const splitId='f5555555-0000-0000-0000-000000000011',condition='0x'+'ab'.repeat(32);
 await q(`insert into arbdesk_private.original_side_calls(call_id,city_key,target_date,checkpoint,source_kind,frozen_at,source,identity,eligibility,cutoff_at,registered_at)
  select $1::uuid,city_key,target_date,checkpoint,source_kind,frozen_at,source,
   identity||jsonb_build_object('condition_id',$3::text,'token_yes','101','token_no','202'),eligibility,cutoff_at,registered_at
  from arbdesk_private.original_side_calls where call_id=$2`,[splitId,hist,condition]);
 const word=n=>BigInt(n).toString(16).padStart(64,'0'),ctf='0x4d97dcd97ec945f40cf65f87097ace5ea0476045',collateral='0x2791bca1f2de4661ed88a30c99a7a9449aa84174';
 const calls=[{key:'denominator',data:'0xdd34de67'+condition.slice(2),result:'0x'+word(2)},
  ...[0,1].map(i=>({key:'numerator_'+i,data:'0x0504c814'+condition.slice(2)+word(i),result:'0x'+word(1)})),
  ...['YES','NO'].flatMap((side,i)=>[{key:'collection_'+side,data:'0x856296f7'+word(0)+condition.slice(2)+word(1<<i),result:'0x'+word(i?22:11)},
   {key:'position_'+side,data:'0x39dd7530'+collateral.slice(2).padStart(64,'0')+word(i?22:11),result:'0x'+word(i?202:101)}])].map(x=>({...x,to:ctf}));
 const ctfProof={protocol:'polygon-legacy-ctf-v1',ctf,collateral,received_at:new Date().toISOString(),
  providers:['https://polygon-bor-rpc.publicnode.com','https://polygon.drpc.org'].map(source=>({source,chain_id:'0x89',latest_block:4096,
   block:{number:'0xfc0',hash:'0x'+'cc'.repeat(32),timestamp:'0x'+Math.floor(Date.now()/1000).toString(16)},calls}))};
 const splitProof={...proof,ctf_payout:ctfProof,response_received_at:new Date().toISOString(),
  gamma:{...gamma,conditionId:condition,clobTokenIds:['101','202'],outcomePrices:['.5','.5']},
  clob:{...clob,condition_id:condition,tokens:[{token_id:'101',winner:false},{token_id:'202',winner:false}]},
  gamma_source:'https://gamma-api.polymarket.com/markets?condition_ids='+condition+'&closed=true',clob_source:'https://clob.polymarket.com/markets/'+condition};
 // Explicit synthetic already-entered historical position: entry gates above
 // are tested prospectively. Here we independently exercise payout revisions
 // on 15.38 NO shares costing 9.997 from a funded 100 account.
 const settlementPolicy=(await q('select public.save_original_side_policy($1,$2) id',[owner,{...config,funding:'fixed'}]))[0].id;
 const settlementAccount=(await q("select public.set_original_side_policy_state($1,$2,'activate') r",[owner,settlementPolicy]))[0].r.account_id;
 const plan=(await q(`insert into arbdesk_private.original_side_allocation_plans(account_id,policy_id,target_date,available_settled_cash,allocation_per_slot,rounding_remainder)
  select $1,$2,target_date,100,10,90 from arbdesk_private.original_side_calls where call_id=$3 returning plan_id`,[settlementAccount,settlementPolicy,splitId]))[0].plan_id;
 const allocation=(await q(`insert into arbdesk_private.original_side_allocations(plan_id,city_key,checkpoint,timezone,side,reserved_amount)
  values($1,'side_fixture','noon',$2,'NO',10) returning allocation_id`,[plan,zone]))[0].allocation_id;
 await q(`insert into arbdesk_private.original_side_ledger(account_id,event_key,event_type,cash_delta,reserved_delta)
  values($1,'reserve:'||$2::text,'synthetic_historical_reserve',0,10)`,[settlementAccount,plan]);
 await q('update arbdesk_private.original_side_accounts set reserved_cash=10 where account_id=$1',[settlementAccount]);
 const quoteId='f5555555-0000-0000-0000-000000000012';
 await q(`insert into arbdesk_private.original_side_quotes(attempt_id,call_id,side,quote_kind,status,requested_at,received_at,snapshot_id)
  select $1,$2,'NO','direct','captured',frozen_at+interval '1 second',frozen_at+interval '2 seconds',$3
  from arbdesk_private.original_side_calls where call_id=$2`,[quoteId,splitId,saved.snapshot_id]);
 const decisionId=(await q(`insert into arbdesk_private.original_side_decisions(allocation_id,account_id,policy_id,call_id,side,quote_id,model_prob_yes,selected_side_probability,snapshot)
  values($1,$2,$3,$4,'NO',$5,.7,.3,'{"synthetic_historical_settlement_fixture":true}') returning decision_id`,[allocation,settlementAccount,settlementPolicy,splitId,quoteId]))[0].decision_id;
 const position=(await q(`insert into arbdesk_private.original_side_orders(command_key,account_id,decision_id,allocation_id,call_id,band_id,token_id,side,shares,limit_price,cash_ceiling,share_step,expires_at,requested_at)
  select gen_random_uuid(),$1,$2,$3,call_id,$4,'202','NO',15.38,.65,10,.01,cutoff_at,frozen_at+interval '3 seconds'
  from arbdesk_private.original_side_calls where call_id=$5 returning order_id`,[settlementAccount,decisionId,allocation,band,splitId]))[0].order_id;
 const settledFill={status:'filled',shares:'15.38',notional:'9.997',fee:'0',fills:[{price:'.65',shares:'15.38',notional:'9.997',fee:'0'}],engine_version:'synthetic_historical_fixture'};
 await q('insert into arbdesk_private.original_side_execution_events(order_id,result) values($1,$2)',[position,settledFill]);
 await q(`insert into arbdesk_private.original_side_ledger(account_id,event_key,event_type,order_id,call_id,cash_delta,reserved_delta)
  values($1,'fill:'||$2::text,'paper_execution',$2::uuid,$3,-9.997,-10)`,[settlementAccount,position,splitId]);
 await q('update arbdesk_private.original_side_accounts set cash=90.003,reserved_cash=0 where account_id=$1',[settlementAccount]);
 await q("update arbdesk_private.original_side_orders set status='filled',result=$2 where order_id=$1",[position,settledFill]);
 const cash=async()=>Number((await q('select cash from arbdesk_private.original_side_accounts where account_id=$1',[settlementAccount]))[0].cash);
 const split=(await q('select public.record_original_side_settlement($1,$2) r',[splitId,splitProof]))[0].r;
 assert.equal(split.state,'verified_split','actual token payouts need independently matching CTF receipts');
 const payouts=(await q('select payout_yes,payout_no from arbdesk_private.original_side_settlements where settlement_id=$1',[split.settlement_id]))[0];
 assert.equal(Number(payouts.payout_yes),.5);assert.equal(Number(payouts.payout_no),.5);
 for(const mutate of [x=>{delete x.ctf_payout.received_at;},x=>{for(const p of x.ctf_payout.providers)delete p.block.hash;},
  x=>{for(const p of x.ctf_payout.providers)p.latest_block='NaN';}]){
  const bad=JSON.parse(JSON.stringify(splitProof));mutate(bad);
  const held=(await q(`select arbdesk_private.original_side_fractional_payout(identity||jsonb_build_object('target_date',target_date),$2) r
    from arbdesk_private.original_side_calls where call_id=$1`,[splitId,bad]))[0].r;
  assert.equal(held,null,'missing provenance or non-finite finality cannot verify a payout');
 }
 assert.equal(await cash(),97.693,'100 - 9.997 + 15.38*.5');
 await q('select public.record_original_side_settlement($1,$2)',[splitId,splitProof]);assert.equal(await cash(),97.693,'retry cannot pay twice');
 const wrongProof=JSON.parse(JSON.stringify(splitProof));wrongProof.ctf_payout.providers[1].calls[0].result='0x'+word(3);
 wrongProof.response_received_at=new Date().toISOString();
 assert.equal((await q('select public.record_original_side_settlement($1,$2) r',[splitId,wrongProof]))[0].r.state,'pending','provider disagreement cannot release payout cash');
 assert.equal(await cash(),90.003,'unverified revision withdraws the prior payout once');
 const noWins={...splitProof,response_received_at:new Date().toISOString(),gamma:{...splitProof.gamma,outcomePrices:['0','1']},
  clob:{...splitProof.clob,tokens:[{token_id:'101',winner:false},{token_id:'202',winner:true}]}};
 await q('select public.record_original_side_settlement($1,$2)',[splitId,noWins]);assert.equal(await cash(),105.383,'100 - 9.997 + 15.38');
 const settledProjection=(await q('select arbdesk_private.original_side_projection($1,$2,$3,10) r',[splitId,owner,settlementAccount]))[0].r;
 assert.equal(settledProjection.prediction_outcome,'miss');assert.equal(Number(settledProjection.sides.NO.paper_orders[0].paper_net_pnl),5.383);
 const archive=(await q('select public.begin_original_side_archive() r'))[0].r;
 const manifest=JSON.parse(archive.payload),packets=[];
 for(const entity of manifest.entities){
  let rows=entity.mutable_snapshot??[];
  for(let i=0;i<(entity.immutable_ids?.length??0);i+=25){rows.push(...(await q('select public.read_original_side_archive_rows($1,$2) r',[entity.table,entity.immutable_ids.slice(i,i+25)]))[0].r);}
  packets.push({table:entity.table,rows});
 }
 const crypto=require('node:crypto'),payload=JSON.stringify({manifest:archive,packets});
 const restored=await require('../../tools/restore_original_sides.cjs').restore({payload,sha256:crypto.createHash('sha256').update(payload).digest('hex')});
 assert.ok(restored.verified&&restored.rows>0,'full private snapshot restores with exact original row arithmetic and references');
 const corrupt=JSON.parse(payload),accountPacket=corrupt.packets.find(x=>x.table==='original_side_accounts');
 const changed=JSON.parse(accountPacket.rows[0]);changed.cash=999;accountPacket.rows[0]=JSON.stringify(changed);
 const changedPayload=JSON.stringify(corrupt);
 await assert.rejects(()=>require('../../tools/restore_original_sides.cjs').restore({payload:changedPayload,
  sha256:crypto.createHash('sha256').update(changedPayload).digest('hex')}),/reconcile.*ledger/);
 await db.exec(fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261111214539_original_call_side_evidence.sql'),'utf8'));
 const encodingFix=fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261112133331_original_side_ladder_encoding.sql'),'utf8');
 await db.exec(encodingFix);await db.exec(encodingFix);
 assert.equal((await q('select arbdesk_private.original_side_source($1) r',[p.checkpoint_id]))[0].r.eligibility,'eligible','repeat installation still accepts the real intraday shape');
 const reinstalled=JSON.parse((await q('select public.begin_original_side_archive() r'))[0].r.payload);
 assert.deepEqual(reinstalled.entities,manifest.entities,'reinstall retains every original receipt, account/order projection and ledger row');
 const session=(await q('select public.issue_original_side_session() r'))[0].r;
 assert.equal((await q('select public.verify_original_side_session($1) id',[session.token]))[0].id,session.owner_id);
 assert.equal((await q('select public.verify_original_side_session($1) id',['0'.repeat(64)]))[0].id,null);
 await assert.rejects(()=>q('update arbdesk_private.original_side_orders set side=\'YES\' where order_id=$1',[order.order_id]),/immutable/);
 await assert.rejects(()=>q('update arbdesk_private.original_side_accounts set cash=cash+100'),/ledger/);
 await db.exec('set role anon');
 await assert.rejects(()=>q('select public.read_original_side_desk($1)',[owner]),/permission denied/);
 await assert.rejects(()=>q('select * from arbdesk_private.original_side_books'),/permission denied/);
 await assert.rejects(()=>q('select arbdesk_private.original_side_ladder_matches($1,$2)',[arrayContext,ladder]),/permission denied/);
 await db.exec('reset role');
 console.log('original sides: original bucket, own NO token, private evidence, numeric sizing/fees, inactive policy, one contribution, reservations, idempotent leases/fills and unresolved nulls passed');
};
