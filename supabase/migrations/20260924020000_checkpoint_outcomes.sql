-- ===========================================================================
-- EVERY CHECKPOINT, SCORED ONCE THE VENUE HAS SPOKEN (plan v2 P4.3)
--
-- databank froze the newest band_probabilities row and the newest edge with
-- no time limit: 302 of 496 frozen ladders were priced after the local close,
-- where the market's winner averages 0.863 and nothing is left to predict.
-- fact_band_outcome stays as it is, for continuity. This is the honest record:
-- one row per prediction_checkpoints row - a ladder published at a fixed
-- moment on the city's clock (P4.2) - joined to the winner the venue
-- CONFIRMED, with the scores the scoreboard (P4.6) grades.
--
--   hit                  the engine's top pick is the winning bucket
--   market_hit           the market's favourite at that moment is
--   brier_* / log_loss_* multiclass, over the checkpoint's ladder, for the
--                        engine, the market and a uniform guess
--
-- THE MARKET'S DISTRIBUTION is each band's mid (the last trade where the book
-- is one-sided), normalised over the ladder. It is scored only when every band
-- of the ladder had a price (market_complete); market_hit needs only the
-- favourite, so it is kept either way.
--
-- A probability of zero on the winner would make log loss infinite: it is
-- floored at 1e-6, the engine's own PROB_FLOOR. A winner missing from the
-- ladder (ladder_has_winner false) adds its own (0 - 1)^2 to every Brier and
-- scores every log loss at the floor; the scoreboard reads such rows apart.
--
-- Append-only. bank_checkpoint_outcomes() adds the checkpoints whose market
-- has been confirmed since the last call and touches nothing already written.
-- ===========================================================================

create table if not exists public.fact_checkpoint_outcome (
  checkpoint_id          uuid        primary key references public.prediction_checkpoints (checkpoint_id),
  city_key               text        not null,
  target_date            date        not null,
  checkpoint             text        not null,
  engine_version         text        not null,
  local_decision_time    timestamp   not null,
  decided_at             timestamptz not null,
  winner_band_id         text        not null,
  n_bands                integer     not null check (n_bands > 0),
  ladder_has_winner      boolean     not null,
  model_prob_on_winner   numeric     not null,
  hit                    boolean     not null,
  brier_model            numeric     not null,
  log_loss_model         numeric     not null,
  market_top_band_id     text,
  market_hit             boolean,
  market_complete        boolean     not null,
  market_prob_on_winner  numeric,
  brier_market           numeric,
  log_loss_market        numeric,
  brier_uniform          numeric     not null,
  log_loss_uniform       numeric     not null,
  settled_at             timestamptz,
  banked_at              timestamptz not null default clock_timestamp(),
  constraint fact_checkpoint_outcome_market_scored_whole check (
    market_complete = (brier_market is not null and log_loss_market is not null
                       and market_prob_on_winner is not null))
);

create index if not exists fact_checkpoint_outcome_day
  on public.fact_checkpoint_outcome (target_date, checkpoint);

comment on table public.fact_checkpoint_outcome is
  'One row per prediction checkpoint once the venue confirmed the winner: whether the engine''s and the market''s top pick won, and both ladders'' Brier and log loss against a uniform guess (plan v2 P4.3). Append-only.';

drop trigger if exists fact_checkpoint_outcome_immutable on public.fact_checkpoint_outcome;
create trigger fact_checkpoint_outcome_immutable before update or delete on public.fact_checkpoint_outcome
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists fact_checkpoint_outcome_no_truncate on public.fact_checkpoint_outcome;
create trigger fact_checkpoint_outcome_no_truncate before truncate on public.fact_checkpoint_outcome
  for each statement execute function arbdesk_private.immutable_record();

alter table public.fact_checkpoint_outcome enable row level security;
revoke all on public.fact_checkpoint_outcome from public, anon, authenticated, service_role;
grant select, insert on public.fact_checkpoint_outcome to service_role;

create or replace function public.bank_checkpoint_outcomes()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare n integer;
begin
  with todo as (
    select p.*
      from public.prediction_checkpoints p
     where not exists (select 1 from public.fact_checkpoint_outcome f where f.checkpoint_id = p.checkpoint_id)
       and p.target_date < current_date + 1
  ),
  settled as (
    -- exactly one confirmed market for the city-day, or nothing is banked
    select t.checkpoint_id, min(mr.winning_band_id::text) as winner, min(mr.confirmed_at) as confirmed_at
      from todo t
      join public.v_venue_market_resolution mr
        on mr.city_key = t.city_key and mr.resolution_date = t.target_date
       and mr.resolution_state = 'confirmed' and mr.winning_band_id is not null
     group by t.checkpoint_id
    having count(*) = 1
  ),
  bands as (
    select t.checkpoint_id, e.key as band_id, e.value::numeric as p,
           (e.key = s.winner) as won,
           case when (t.market -> e.key ->> 'bid') is not null and (t.market -> e.key ->> 'ask') is not null
                then ((t.market -> e.key ->> 'bid')::numeric + (t.market -> e.key ->> 'ask')::numeric) / 2
                else (t.market -> e.key ->> 'last')::numeric end as px
      from todo t
      join settled s using (checkpoint_id)
      cross join lateral jsonb_each_text(t.probs) e
  ),
  per as (
    select checkpoint_id,
           count(*)                                         as n_bands,
           bool_or(won)                                     as ladder_has_winner,
           coalesce(max(p) filter (where won), 0)           as p_win,
           sum(power(p - case when won then 1 else 0 end, 2)) as brier_model,
           bool_and(px is not null) and coalesce(sum(px), 0) > 0 as market_complete,
           sum(px)                                          as px_total
      from bands group by checkpoint_id
  ),
  mkt as (
    select b.checkpoint_id,
           max(b.px / nullif(p.px_total, 0)) filter (where b.won)                         as m_win,
           sum(power(b.px / nullif(p.px_total, 0) - case when b.won then 1 else 0 end, 2)) as brier_market
      from bands b join per p using (checkpoint_id)
     where p.market_complete
     group by b.checkpoint_id
  )
  insert into public.fact_checkpoint_outcome (
    checkpoint_id, city_key, target_date, checkpoint, engine_version, local_decision_time, decided_at,
    winner_band_id, n_bands, ladder_has_winner, model_prob_on_winner, hit, brier_model, log_loss_model,
    market_top_band_id, market_hit, market_complete, market_prob_on_winner, brier_market, log_loss_market,
    brier_uniform, log_loss_uniform, settled_at)
  select t.checkpoint_id, t.city_key, t.target_date, t.checkpoint, t.engine_version, t.local_decision_time, t.decided_at,
         s.winner, p.n_bands, p.ladder_has_winner, p.p_win, t.top_band_id = s.winner,
         round(p.brier_model + case when p.ladder_has_winner then 0 else 1 end, 6),
         round(-ln(greatest(p.p_win, 1e-6)), 6),
         t.market_top_band_id,
         case when t.market_top_band_id is null then null else t.market_top_band_id = s.winner end,
         p.market_complete,
         case when p.market_complete then round(coalesce(m.m_win, 0), 6) end,
         case when p.market_complete then round(m.brier_market + case when p.ladder_has_winner then 0 else 1 end, 6) end,
         case when p.market_complete then round(-ln(greatest(coalesce(m.m_win, 0), 1e-6)), 6) end,
         round(case when p.ladder_has_winner then 1 - 1.0 / p.n_bands else 1 + 1.0 / p.n_bands end, 6),
         round(case when p.ladder_has_winner then ln(p.n_bands::numeric) else -ln(1e-6) end, 6),
         s.confirmed_at
    from todo t
    join settled s using (checkpoint_id)
    join per p using (checkpoint_id)
    left join mkt m using (checkpoint_id)
  on conflict (checkpoint_id) do nothing;
  get diagnostics n = row_count;
  return n;
end $$;

revoke all on function public.bank_checkpoint_outcomes() from public, anon, authenticated;
grant execute on function public.bank_checkpoint_outcomes() to service_role;
