const assert=require('node:assert/strict');const fs=require('node:fs');const path=require('node:path');
module.exports=async function(db){
 const sql=fs.readFileSync(path.join(__dirname,'../../supabase/migrations/20261030170828_day_max_original_early_calls.sql'),'utf8');
 await db.exec(sql);await db.exec(sql);
 const offset=-new Date().getUTCHours();const zone=offset===0?'UTC':`Etc/GMT+${Math.abs(offset)}`;
 const mid='e5555555-0000-0000-0000-000000000003',b1='e5555555-0000-0000-0000-000000000001',b2='e5555555-0000-0000-0000-000000000002';
 const sid='s14_day_max_prediction';
 await db.query(`insert into public.cities(city_key,display_name,timezone,unit,status) values('daymax_early','Early fixture',$1,'C','active')`,[zone]);
 await db.exec(`insert into public.markets(market_id,city_key,resolution_date,closed,unit) values('${mid}','daymax_early',(now() at time zone '${zone}')::date,false,'C');
 insert into public.bands(band_id,market_id,band_index,band_label,token_yes,token_no,condition_id)
 values('${b1}','${mid}',0,'20°C','early-yes-1','early-no-1','early-cond-1'),('${b2}','${mid}',1,'21°C','early-yes-2','early-no-2','early-cond-2');
 insert into public.derived_weather_peak(city_key,month,peak_hour_local,n_days,computed_at)
 values('daymax_early',extract(month from (now() at time zone '${zone}')),18,20,now()-interval '1 day');
 update public.strategies set extra=jsonb_set(extra,'{day_max}',jsonb_build_object(
 'policy','day-max-frozen-fixed-stake-v1','activated_at',now()-interval '1 day','selection_changed_at',now()-interval '1 minute',
 'cities',jsonb_build_array(jsonb_build_object('city_key','daymax_early','checkpoint','day_ahead','stake_usd',10)))) where strategy_id='${sid}';
 insert into public.band_probabilities(band_id,computed_at,raw_prob,calibrated_prob,input_forecast_run,centre_c,sigma_c)
 select b.band_id,((now() at time zone '${zone}')::date::timestamp at time zone '${zone}')-interval '2 hours',
 case when b.band_id='${b1}' then .7 else .3 end,case when b.band_id='${b1}' then .7 else .3 end,
 ((now() at time zone '${zone}')::date::timestamp at time zone '${zone}')-interval '4 hours',20,1
 from public.bands b where market_id='${mid}';
 -- A later partial pre-midnight write must never supply a mixed ladder.
 insert into public.band_probabilities(band_id,computed_at,raw_prob,calibrated_prob,input_forecast_run)
 values('${b1}',((now() at time zone '${zone}')::date::timestamp at time zone '${zone}')-interval '1 hour',1,1,now()-interval '5 hours');
 -- A complete observed-floor run also cannot become a day-ahead source.
 insert into public.band_probabilities(band_id,computed_at,raw_prob,calibrated_prob,input_forecast_run,observed_floor_c)
 select b.band_id,((now() at time zone '${zone}')::date::timestamp at time zone '${zone}')-interval '30 minutes',
 case when b.band_id='${b2}' then .9 else .1 end,case when b.band_id='${b2}' then .9 else .1 end,now()-interval '5 hours',30
 from public.bands b where market_id='${mid}';
 -- Today's post-midnight output favours the other bucket: it is ineligible.
 insert into public.band_probabilities(band_id,computed_at,raw_prob,calibrated_prob,input_forecast_run)
 select b.band_id,now(),case when b.band_id='${b2}' then .9 else .1 end,case when b.band_id='${b2}' then .9 else .1 end,now()-interval '1 hour'
 from public.bands b where market_id='${mid}';`);
 const capture=()=>db.query(`select public.capture_day_max_day_ahead('daymax_early',(now() at time zone '${zone}')::date) x`);
 await assert.rejects(db.query(`select public.capture_day_max_day_ahead('daymax_early',(now() at time zone '${zone}')::date+1)`),/capture window/);
 const first=(await capture()).rows[0].x;assert.equal(first.captured,true);
 const record=(await db.query('select record from public.day_max_day_ahead_calls where call_id=$1',[first.call_id])).rows[0].record;
 assert.equal(record.probs[b1],.7);assert.equal(record.probs[b2],.3);assert.equal(record.top_band_id,b1);
 assert.ok(new Date(record.predictive_context.source_computed_at)<new Date(record.predictive_context.cutoff_at));
 assert.ok(new Date(record.input_as_of)<=new Date(record.predictive_context.source_computed_at));
 assert.equal((await capture()).rows[0].x.captured,false);
 await assert.rejects(db.exec('update public.day_max_day_ahead_calls set record=\'{}\''),/immutable|Append-only/);
 await assert.rejects(db.exec('delete from public.day_max_day_ahead_calls'),/immutable|Append-only/);
 const leg={band_id:b1,side:'YES',shares:'20',limit_price:'.49',cash_ceiling:'10'};
 const publish=()=>db.query('select public.publish_day_max_prediction($1,$2::jsonb,$3::jsonb) x',[first.call_id,JSON.stringify([leg]),JSON.stringify({quoted_cost_per_basket:'.49'})]);
 assert.equal((await publish()).rows[0].x.allocated,true);
 assert.equal((await publish()).rows[0].x.allocated,false);
 const result=(await db.query(`select d.prediction_source,e.snapshot from public.day_max_prediction_entries e join public.decisions d using(decision_id) where e.checkpoint_id=$1`,[first.call_id])).rows[0];
 assert.equal(result.prediction_source,'day_max_day_ahead_calls');assert.deepEqual(result.snapshot.probs,record.probs);
 assert.equal((await db.query("select count(*)::int n from public.prediction_checkpoints where checkpoint='day_ahead'")).rows[0].n,0);
 await db.exec('set role anon');await assert.rejects(capture(),/permission denied/);
 await assert.rejects(db.exec('select * from public.v_day_max_original_calls'),/permission denied/);await db.exec('reset role');
 await db.exec(sql);assert.equal((await db.query('select count(*)::int n from public.day_max_day_ahead_calls')).rows[0].n,1);
 console.log('day-max early: original complete pre-midnight source, post-midnight exclusion, freeze once, immutable receipt, real queue, daily dedupe and private RPC passed');
};
