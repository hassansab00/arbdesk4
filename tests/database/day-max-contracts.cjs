const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
module.exports=async function(db) {
  const sid='s14_day_max_prediction';
  const b1='e4444444-0000-0000-0000-000000000001', b2='e4444444-0000-0000-0000-000000000002';
  const mid='e4444444-0000-0000-0000-000000000003';
  const offset=12-new Date().getUTCHours();
  const zone=offset===0?'UTC':`Etc/GMT${offset>0?'-':'+'}${Math.abs(offset)}`;
  await db.query(`insert into public.cities(city_key,display_name,timezone,unit,status)
    values('daymax_fixture','Day Max fixture',$1,'C','active')`,[zone]);
  await db.exec(`insert into public.markets(market_id,closed,resolution_date,city_key,unit)
    values('${mid}',false,(now() at time zone '${zone}')::date,'daymax_fixture','C');
    insert into public.bands(band_id,market_id,band_index,band_label,band_lo,band_hi,open_low,open_high,token_yes,token_no,condition_id)
    values('${b1}','${mid}',0,'20°C',20,21,false,false,'daymax-yes-1','daymax-no-1','daymax-condition-1'),
          ('${b2}','${mid}',1,'21°C',21,22,false,false,'daymax-yes-2','daymax-no-2','daymax-condition-2');
    update public.strategies set extra=extra||jsonb_build_object('day_max',jsonb_build_object(
      'policy','day-max-frozen-fixed-stake-v1','activated_at',now()-interval '1 hour',
      'cities',jsonb_build_array(jsonb_build_object('city_key','daymax_fixture','checkpoint','noon','stake_usd',10))))
      where strategy_id='${sid}';
    select public.set_strategy_state('${sid}','shadow','contract fixture');
    update public.paper_accounts set policy=policy||'{"min_edge":-1,"max_plan_usd":10,"auto_exit_enabled":false}',
      entries_paused=false,mode='automatic' where strategy_id='${sid}';
    update public.settings set value='{"halted":false}' where key='trading_halt';`);
  const p=(await db.query(`insert into public.prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,
      engine_version,model_path,probs,top_band_id,top_prob,inputs_ok,predictive_context,forecast_issued_at)
    values('daymax_fixture',(now() at time zone '${zone}')::date,'noon',
      (now() at time zone '${zone}')::date+time '12:00','fixture','forecast',
      $1::jsonb,$2,.7,true,'{"clim_peak_hour":18}',now()-interval '5 minutes') returning *`,
    [JSON.stringify({[b1]:.7,[b2]:.3}),b1])).rows[0];
  const leg={band_id:b1,side:'YES',shares:'20',limit_price:'.49',cash_ceiling:'10'};
  const evidence={quoted_cost_per_basket:'.49',net_edge_per_share:'.21'};
  const publish=(legs=[leg],id=p.checkpoint_id)=>db.query('select public.publish_day_max_prediction($1,$2::jsonb,$3::jsonb) result',
    [id,JSON.stringify(legs),JSON.stringify(evidence)]);
  await assert.rejects(()=>publish([{...leg,cash_ceiling:'10.01'}]),/ten-dollar/);
  await assert.rejects(()=>publish([{...leg,band_id:b2}]),/bucket/);
  await db.exec(`update public.strategies set extra=jsonb_set(extra,'{day_max,activated_at}',to_jsonb(now()+interval '1 day')) where strategy_id='${sid}'`);
  await assert.rejects(()=>publish(),/before activation/);
  await db.exec(`update public.strategies set extra=jsonb_set(extra,'{day_max,activated_at}',to_jsonb(now()-interval '1 hour')) where strategy_id='${sid}'`);
  const result=(await publish()).rows[0].result;
  assert.equal(result.allocated,true);
  const entry=(await db.query('select * from public.day_max_prediction_entries where checkpoint_id=$1',[p.checkpoint_id])).rows[0];
  const order=(await db.query('select * from public.paper_orders where plan_id=$1',[entry.plan_id])).rows[0];
  assert.ok(order,'the plan must really queue, rather than quietly block');
  assert.equal(order.side,'YES');assert.equal(order.band_id,b1);assert.ok(Number(order.cash_ceiling)<=10);
  assert.equal(new Date(entry.snapshot.frozen_at).getTime(),new Date(p.frozen_at).getTime());
  assert.equal(entry.snapshot.top_band_id,b1);
  assert.equal((await publish()).rows[0].result.allocated,false);
  assert.equal((await db.query('select count(*) n from public.paper_orders where plan_id=$1',[entry.plan_id])).rows[0].n,1);
  await assert.rejects(()=>db.query('update public.paper_orders set cash_ceiling=11 where order_id=$1',[order.order_id]),/fixed|ceiling/);
  await assert.rejects(()=>db.query('update public.day_max_prediction_entries set stake_ceiling=9 where checkpoint_id=$1',[p.checkpoint_id]),/immutable/);
  await assert.rejects(()=>db.query('delete from public.day_max_prediction_entries where checkpoint_id=$1',[p.checkpoint_id]),/immutable/);
  const d=(await db.query('select prediction_id,prediction_source from public.decisions where decision_id=$1',[entry.decision_id])).rows[0];
  assert.equal(d.prediction_id,p.checkpoint_id);assert.equal(d.prediction_source,'prediction_checkpoints');
  const replaced=await db.query(`insert into public.prediction_checkpoints(city_key,target_date,checkpoint,local_decision_time,
    engine_version,model_path,probs,top_band_id,top_prob,inputs_ok,predictive_context)
    select city_key,target_date,checkpoint,local_decision_time,'later','forecast',$1::jsonb,$2,.9,true,predictive_context
    from public.prediction_checkpoints where checkpoint_id=$3 returning checkpoint_id`,
    [JSON.stringify({[b1]:.1,[b2]:.9}),b2,p.checkpoint_id]);
  assert.equal(replaced.rows.length,0,'the existing freeze trigger refuses a replacement forecast');
  const job=(await db.query('select public.claim_account_order($1) job',[entry.account_id])).rows[0].job;
  assert.equal(job.order_id,order.order_id);
  await db.query(`insert into public.paper_book_evidence(snapshot_id,token_id,observed_at,payload)
    values('daymax-fixture-book',$1,now(),'{}')`,[job.token_id]);
  await db.query('select public.complete_paper_order($1,$2,$3::jsonb)',[job.order_id,job.lease_token,JSON.stringify({
    status:'filled',reason:null,shares:'20',notional:'9.80',fee:'.19',snapshot_id:'daymax-fixture-book',
    fills:[{shares:'20',price:'.49',notional:'9.80',fee:'.19'}]})]);
  const position=(await db.query('select entry_decision_id,p_at_entry from public.paper_positions where account_id=$1',[entry.account_id])).rows[0];
  assert.equal(Number(position.entry_decision_id),Number(entry.decision_id));assert.equal(Number(position.p_at_entry),.7);
  assert.equal((await publish()).rows[0].result.allocated,false,'a completed fill cannot re-enter the same date');
  await db.exec('set role anon');
  await assert.rejects(()=>db.query('select * from public.day_max_prediction_entries'),/permission denied/);
  await assert.rejects(()=>publish(),/permission denied/);
  await db.exec('reset role');
  await db.exec(fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261030080013_day_max_prediction_paper.sql'),'utf8'));
  assert.equal((await db.query('select enabled from public.strategies where strategy_id=$1',[sid])).rows[0].enabled,true);
  assert.equal((await db.query('select count(*) n from public.day_max_prediction_entries')).rows[0].n,1);
  console.log('day-max: queued original YES, ten-dollar ceiling, one allocation, immutable receipt, private RPC and repeat install passed');
};
