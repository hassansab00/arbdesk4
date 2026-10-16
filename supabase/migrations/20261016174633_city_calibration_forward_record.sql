-- Actual forward checkpoint performance of served city-blend widths.
-- Never selects shadow predictors or re-grades training predictions. The first
-- call is selected across ALL engine versions before filtering for this width.
do $$
begin
  if to_regclass('public.prediction_checkpoints') is null
     or to_regclass('public.fact_checkpoint_outcome') is null
     or to_regclass('public.cities') is null then return; end if;
  execute $v$create or replace view public.v_city_calibration_forward as
  with calls as (
    select p.*, substring(p.priced_from from '(city-blend-width:[0-9-]{10}:[a-z0-9]+)') as width_version,
      substring(p.priced_from from 'city-blend-width:([0-9-]{10}):')::date as fitted_through
    from public.prediction_checkpoints p
    where p.priced_from like '%city-blend-width:%'
      and p.inputs_ok
      and not exists (
        select 1 from public.prediction_checkpoints earlier
        where earlier.city_key=p.city_key and earlier.target_date=p.target_date
          and earlier.checkpoint=p.checkpoint
          and (earlier.decided_at,earlier.checkpoint_id)<(p.decided_at,p.checkpoint_id)
      )
  ), scored as (
    select p.city_key,p.checkpoint,p.width_version,p.target_date,p.decided_at,
      f.checkpoint_id is not null
        and p.target_date < (now() at time zone coalesce(c.timezone,'UTC'))::date
        and f.settled_at is not null as completed,
      f.hit,f.brier_model,f.log_loss_model
    from calls p join public.cities c using(city_key)
    left join public.fact_checkpoint_outcome f using(checkpoint_id)
    where p.width_version is not null and p.target_date>p.fitted_through
  )
  select city_key,checkpoint,width_version,count(*)::integer as calls,
    count(*) filter(where completed)::integer as settled_calls,
    count(distinct target_date) filter(where completed)::integer as settled_dates,
    count(*) filter(where not completed)::integer as pending_calls,
    avg(hit::integer) filter(where completed) as hit_rate,
    avg(brier_model) filter(where completed) as brier,
    avg(log_loss_model) filter(where completed) as log_loss,
    max(decided_at) as last_called_at
  from scored group by city_key,checkpoint,width_version$v$;
  revoke all on public.v_city_calibration_forward from public;
  grant select on public.v_city_calibration_forward to anon,authenticated,service_role;
  comment on view public.v_city_calibration_forward is
    'Served city-blend width versions only: first original engine call per city/day/checkpoint, completed local days and confirmed venue outcomes. Descriptive forward scores, not a controlled comparison or evidence of improvement.';
end $$;
notify pgrst,'reload schema';
