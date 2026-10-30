-- Reuse immutable market scores instead of expanding every saved book on every read.
-- v_checkpoint_outcome includes the linked correction for the original quote rule.
-- Original calls, labels, scores and materialized rows are not rewritten here.
do $migration$
declare old_def text; previous_options text[]; differences bigint;
begin
  if to_regclass('public.v_checkpoint_calls') is null
     or to_regclass('public.v_checkpoint_outcome') is null then return; end if;
  old_def := regexp_replace(pg_get_viewdef('public.v_checkpoint_calls'::regclass,true),';\s*$','');
  select reloptions into previous_options from pg_class where oid='public.v_checkpoint_calls'::regclass;
  execute $view$create or replace view public.v_checkpoint_calls as
with cp as (
  select p.checkpoint_id,p.city_key,p.target_date,p.checkpoint,p.local_decision_time,p.decided_at,
         p.engine_version,p.top_band_id,p.top_prob,p.centre_c,p.sigma_c,p.running_max_c,p.market,
         f.winner_band_id,f.hit,f.n_bands,f.ladder_has_winner,f.model_prob_on_winner,
         f.brier_model,f.log_loss_model,f.brier_uniform,f.log_loss_uniform,f.settled_at,
         f.market_top_band_id,f.market_hit,f.market_complete,f.market_prob_on_winner,f.brier_market,f.log_loss_market,
         p.first_call,p.calls_at_checkpoint
    from (select q.checkpoint_id,q.city_key,q.target_date,q.checkpoint,q.local_decision_time,q.decided_at,
                 q.engine_version,q.top_band_id,q.top_prob,q.centre_c,q.sigma_c,q.running_max_c,q.market,
                 row_number() over(partition by q.city_key,q.target_date,q.checkpoint order by q.decided_at,q.checkpoint_id)=1 as first_call,
                 count(*) over(partition by q.city_key,q.target_date,q.checkpoint) as calls_at_checkpoint
            from public.prediction_checkpoints q) p
    join public.v_checkpoint_outcome f using(checkpoint_id)
)
select cp.city_key,cp.target_date as for_date,cp.checkpoint,
       case cp.checkpoint when 'd1_eve' then 1 when 'morning' then 2 when 'noon' then 3
         when 'prepeak_2h' then 4 when 'prepeak_1h' then 5 when 'postpeak_1h' then 6 end as checkpoint_order,
       cp.checkpoint='postpeak_1h' as after_peak,cp.local_decision_time,cp.decided_at,cp.engine_version,
       tb.band_label as called_band,round(cp.top_prob,4) as called_prob,wb.band_label as winner_band,cp.hit,
       round(cp.centre_c,2) as centre_c,round(cp.sigma_c,2) as sigma_c,cp.running_max_c,
       mb.band_label as market_band,
       round(public.book_mark((cp.market->cp.market_top_band_id->>'bid')::numeric,
                              (cp.market->cp.market_top_band_id->>'ask')::numeric,
                              (cp.market->cp.market_top_band_id->>'last')::numeric),4) as market_price,
       cp.market_hit,cp.market_complete,cp.n_bands,cp.ladder_has_winner,
       round(cp.model_prob_on_winner,6) as model_prob_on_winner,cp.brier_model,cp.log_loss_model,
       cp.market_prob_on_winner,cp.brier_market,cp.log_loss_market,cp.brier_uniform,cp.log_loss_uniform,
       cp.settled_at,cp.first_call,cp.calls_at_checkpoint::integer as calls_at_checkpoint
  from cp
  left join public.bands tb on tb.band_id=cp.top_band_id::uuid
  left join public.bands wb on wb.band_id=cp.winner_band_id::uuid
  left join public.bands mb on mb.band_id=cp.market_top_band_id::uuid;$view$;
  -- One statement snapshot, both directions, retaining duplicate multiplicity.
  -- Refuse installation if even one existing display field would change.
  execute 'with previous as materialized ('||old_def||'), current as materialized
    (select * from public.v_checkpoint_calls)
    select count(*) from ((select * from previous except all select * from current)
      union all (select * from current except all select * from previous)) differences'
    into differences;
  if differences<>0 then raise exception 'Stored checkpoint market scores differ from existing display: %',differences; end if;
  if (select reloptions from pg_class where oid='public.v_checkpoint_calls'::regclass) is distinct from previous_options then
    raise exception 'Checkpoint view options changed'; end if;
end $migration$;
