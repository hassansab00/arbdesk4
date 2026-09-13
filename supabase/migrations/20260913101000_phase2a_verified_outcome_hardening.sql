-- Phase 2A follow-up for projects where the base outcome-truth migration was
-- already applied. CREATE OR REPLACE preserves a view's old security option,
-- so set it explicitly and add the correction-chain FK index.
begin;

alter view public.v_calibration set (security_invoker = true);
alter view public.v_edge_realisation set (security_invoker = true);
alter view public.v_forecast_convergence set (security_invoker = true);
alter view public.v_prediction_scorecard set (security_invoker = true);
alter view public.v_edge_scaling set (security_invoker = true);

create index if not exists weather_resolution_supersedes
  on public.weather_resolution_evidence(supersedes_evidence_id)
  where supersedes_evidence_id is not null;

commit;
