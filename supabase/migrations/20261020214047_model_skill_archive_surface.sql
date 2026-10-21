-- Superseded per-model skill measurements join the existing nightly archive.
-- Keep the newest row of EVERY evidence scope, including null/legacy scope.
-- Current verified and fallback pricing must retain exactly the same rowf.
create or replace view public.v_prunable_model_skill
with (security_invoker = true) as
select jsonb_build_array(f.city_key, f.model, f.lead_days,
         to_char(f.computed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US'))::text as skill_key,
       f.city_key,f.model,f.computed_at,f.lead_days,f.n_days,f.mae_c,f.bias_c,
       f.p90_abs_err_c,f.mae_bands,f.pct_within_one_band,f.band_width_c,f.evidence_scope,
       -- Compatibility alias for the existing archive API's date filter.
       f.computed_at as captured_at
from public.derived_forecast_skill_model f
where exists (
  select 1 from public.derived_forecast_skill_model n
  where n.city_key = f.city_key and n.model = f.model and n.lead_days = f.lead_days
    and n.evidence_scope is not distinct from f.evidence_scope
    and n.computed_at > f.computed_at
);
comment on view public.v_prunable_model_skill is
  'Superseded per-model skill rows only; each city/model/lead/evidence-scope latest stays live. Age is applied by the archive caller. Service role only.';
revoke all on public.v_prunable_model_skill from public, anon, authenticated;
grant select on public.v_prunable_model_skill to service_role;
