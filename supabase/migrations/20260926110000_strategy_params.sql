-- ===========================================================================
-- WHAT THE NIGHTLY LOOP LEARNED, ONE IMMUTABLE ROW PER VERSION (plan v2 P5.8)
--
-- scripts/strategy_learn.py fits the learned parameters after databank has
-- banked the day's outcomes, from target dates strictly before the day it
-- runs (walk-forward). Each fit is one row here: the parameter, its scope,
-- a deterministic version, the value, the sample it rests on, and the prior
-- and bounds it was held to (Rule 11). A version is written once and never
-- changed; a new night's fit is a new row, and every decision records the
-- version it used.
--
-- PRIORS STAY FROZEN UNTIL THE REPLAY CHECK. The plan: the learned
-- parameters must beat the fixed priors on out-of-sample dates in the replay
-- (P7.3), or "the loop ships with priors frozen and a flag to enable it".
-- The replay does not exist yet, so settings.strategy_learning starts with
-- enabled = false: the loop learns and records every night, and the loaders
-- (belief.load, execution_cost.load) keep returning the priors until the
-- flag is turned on.
-- ===========================================================================

create table if not exists public.strategy_params (
  param_id    bigserial   primary key,
  param       text        not null,
  scope       text        not null default 'all',
  version     text        not null,
  value       jsonb       not null,
  n           integer     not null check (n >= 0),
  prior       jsonb       not null,
  bounds      jsonb,
  as_of       date        not null,
  fitted_at   timestamptz not null default clock_timestamp(),
  constraint strategy_params_one_row_per_version unique (param, scope, version)
);

create index if not exists strategy_params_latest on public.strategy_params (param, scope, fitted_at desc);

comment on table public.strategy_params is
  'Plan v2 P5.8: one immutable row per learned parameter, scope and version - the value, the sample n it rests on, and the prior and bounds it was held to. Written nightly by scripts/strategy_learn.py from target dates before as_of. Read by the loaders only while settings.strategy_learning.enabled is true.';

drop trigger if exists strategy_params_immutable on public.strategy_params;
create trigger strategy_params_immutable before update or delete on public.strategy_params
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists strategy_params_no_truncate on public.strategy_params;
create trigger strategy_params_no_truncate before truncate on public.strategy_params
  for each statement execute function arbdesk_private.immutable_record();

alter table public.strategy_params enable row level security;
revoke all on public.strategy_params from public, anon, authenticated, service_role;
grant select, insert on public.strategy_params to service_role;
grant usage, select on sequence public.strategy_params_param_id_seq to service_role;

insert into public.settings (key, value)
values ('strategy_learning', jsonb_build_object(
  'enabled', false,
  'why', 'Plan v2 P5.8: learned parameters are used only after the replay (P7.3) shows they beat the fixed priors on out-of-sample dates. Until then the loop records and the loaders return the priors.'))
on conflict (key) do nothing;
