-- Same verified-first/newest skill selection as probability_engine's cache,
-- performed before transfer. History stays intact in its original tables.
create or replace function public.forecast_skill_snapshot()
returns jsonb language sql stable security invoker set search_path=''
set statement_timeout='4s' as $$
 with pooled as (
  select distinct on (city_key,lead_days)
   city_key,lead_days,n_days,mae_c,bias_c,computed_at,evidence_scope
  from public.derived_forecast_skill
  order by city_key,lead_days,
   case when evidence_scope='verified_outcomes_v1' then 0 else 1 end,computed_at desc
 ), models as (
  select distinct on (city_key,model,lead_days)
   city_key,model,lead_days,n_days,mae_c,bias_c,computed_at,evidence_scope
  from public.derived_forecast_skill_model
  order by city_key,model,lead_days,
   case when evidence_scope='verified_outcomes_v1' then 0 else 1 end,computed_at desc
 )
 select jsonb_build_object('policy','latest-forecast-skill-v1',
  'evidence_scope','verified_outcomes_v1',
  'pooled',coalesce((select jsonb_agg(to_jsonb(p) order by city_key,lead_days) from pooled p),'[]'::jsonb),
  'models',coalesce((select jsonb_agg(to_jsonb(m) order by city_key,model,lead_days) from models m),'[]'::jsonb))
$$;
revoke all on function public.forecast_skill_snapshot() from public,anon,authenticated;
grant execute on function public.forecast_skill_snapshot() to service_role;
notify pgrst,'reload schema';
