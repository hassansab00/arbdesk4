-- ===========================================================================
-- THE MARKET SCORES BANKED BEFORE THE FIX (audit repair 3, 29 Sep).
--
-- fact_checkpoint_outcome is immutable. Its first 98 rows (banked 25 Sep
-- 11:14Z - 26 Sep 05:15Z) scored the market before public.book_mark() existed:
-- a one-sided book was priced at its last trade, so a bucket offered at 0.001
-- that last traded at 0.999 became the favourite. 20260926090000 (applied live
-- 26 Sep 16:38:33Z) fixed the banking and made v_checkpoint_calls recompute
-- the market side of every row, and left those 98 rows as written.
--
-- Nothing on the table said so. Read directly, it still gives the market 5 hits
-- in those 98 rows where the fixed rule gives 86, and two readers took it at
-- its word: the 29 Sep external audit (an hour after the peak, model 63.1% vs
-- market 68.0%, where v_checkpoint_calls gives the market 89.3% on the same
-- 103 city-days) and PLAN_PROGRESS's own 28 Sep headline (0.642 vs 0.653).
-- Measured 29 Sep against v_checkpoint_calls: of the 98, 91 differ in at least
-- one market column (market_hit 81, the favourite 90, probability on the
-- winner, Brier and log loss 91 each); of the 898 rows banked after the fix,
-- 0 differ in any. The fill below reproduces the view on all 91 (0 differ).
--
--   public.fact_checkpoint_outcome_market_fix  the market columns of those
--       rows scored by book_mark() from the books prediction_checkpoints kept,
--       with the reason; append-only and immutable like the table it corrects.
--       Filled here for rows banked before the fix whose stored market columns
--       differ from the recomputation; the originals are not touched.
--   public.v_checkpoint_outcome  every fact_checkpoint_outcome row with the
--       corrected market columns where a correction exists, and
--       market_corrected saying which.
--   Comments on the table and its market columns point a direct reader here.
--
-- Idempotent: the fill takes each row at most once (on conflict do nothing).
-- ===========================================================================

create table if not exists public.fact_checkpoint_outcome_market_fix (
  checkpoint_id          uuid        primary key references public.fact_checkpoint_outcome (checkpoint_id),
  market_top_band_id     text,
  market_hit             boolean,
  market_complete        boolean     not null,
  market_prob_on_winner  numeric,
  brier_market           numeric,
  log_loss_market        numeric,
  reason                 text        not null,
  corrected_at           timestamptz not null default clock_timestamp(),
  constraint fact_checkpoint_outcome_market_fix_scored_whole check (
    market_complete = (brier_market is not null and log_loss_market is not null
                       and market_prob_on_winner is not null))
);

comment on table public.fact_checkpoint_outcome_market_fix is
  'Audit repair 3 (29 Sep): the market columns of fact_checkpoint_outcome rows banked before public.book_mark() (26 Sep 16:38Z), rescored by book_mark() from the books prediction_checkpoints kept. Only rows whose stored market columns differ are here; the originals stay in fact_checkpoint_outcome. Read v_checkpoint_outcome.';

drop trigger if exists fact_checkpoint_outcome_market_fix_immutable on public.fact_checkpoint_outcome_market_fix;
create trigger fact_checkpoint_outcome_market_fix_immutable before update or delete on public.fact_checkpoint_outcome_market_fix
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists fact_checkpoint_outcome_market_fix_no_truncate on public.fact_checkpoint_outcome_market_fix;
create trigger fact_checkpoint_outcome_market_fix_no_truncate before truncate on public.fact_checkpoint_outcome_market_fix
  for each statement execute function arbdesk_private.immutable_record();

alter table public.fact_checkpoint_outcome_market_fix enable row level security;
revoke all on public.fact_checkpoint_outcome_market_fix from public, anon, authenticated, service_role;
grant select on public.fact_checkpoint_outcome_market_fix to service_role;   -- only this migration writes it

-- The fill: v_checkpoint_calls' own market arithmetic, keyed by checkpoint_id.
with cp as (
  select p.checkpoint_id, p.probs, p.market, f.winner_band_id, f.ladder_has_winner, f.banked_at,
         f.market_top_band_id as s_band, f.market_hit as s_hit, f.market_complete as s_complete,
         f.market_prob_on_winner as s_prob, f.brier_market as s_brier, f.log_loss_market as s_ll
    from public.prediction_checkpoints p
    join public.fact_checkpoint_outcome f using (checkpoint_id)
   where f.banked_at < timestamptz '2026-09-26 16:38:33+00'
),
px as (
  select cp.checkpoint_id, k.band_id, (k.band_id = cp.winner_band_id) as won,
         public.book_mark((cp.market -> k.band_id ->> 'bid')::numeric,
                          (cp.market -> k.band_id ->> 'ask')::numeric,
                          (cp.market -> k.band_id ->> 'last')::numeric) as px
    from cp
   cross join lateral (select e.key as band_id from jsonb_each_text(cp.probs) e) k
),
mkt as (
  select x.checkpoint_id,
         (array_agg(x.band_id order by x.px desc, x.band_id desc) filter (where x.px is not null))[1] as band_id,
         bool_and(x.px is not null) and coalesce(sum(x.px), 0) > 0 as complete,
         sum(x.px) as px_total
    from px x group by x.checkpoint_id
),
scored as (
  select x.checkpoint_id,
         coalesce(max(x.px / m.px_total) filter (where x.won), 0)             as m_win,
         sum(power(x.px / m.px_total - case when x.won then 1 else 0 end, 2)) as brier
    from px x join mkt m using (checkpoint_id)
   where m.complete
   group by x.checkpoint_id
),
fixed as (
  select cp.checkpoint_id,
         m.band_id                                                              as market_top_band_id,
         case when m.band_id is null then null else m.band_id = cp.winner_band_id end as market_hit,
         coalesce(m.complete, false)                                            as market_complete,
         case when m.complete then round(s.m_win, 6) end                        as market_prob_on_winner,
         case when m.complete then round(s.brier + case when cp.ladder_has_winner then 0 else 1 end, 6) end
                                                                                as brier_market,
         case when m.complete then round(-ln(greatest(s.m_win, 1e-6)), 6) end   as log_loss_market,
         cp.s_band, cp.s_hit, cp.s_complete, cp.s_prob, cp.s_brier, cp.s_ll
    from cp
    left join mkt m using (checkpoint_id)
    left join scored s using (checkpoint_id)
)
insert into public.fact_checkpoint_outcome_market_fix
  (checkpoint_id, market_top_band_id, market_hit, market_complete, market_prob_on_winner,
   brier_market, log_loss_market, reason)
select checkpoint_id, market_top_band_id, market_hit, market_complete, market_prob_on_winner,
       brier_market, log_loss_market,
       'banked before book_mark() (20260926090000, live 26 Sep 16:38:33Z): a one-sided book was priced at its last trade; rescored by book_mark() from prediction_checkpoints.market'
  from fixed
 where s_band is distinct from market_top_band_id
    or s_hit is distinct from market_hit
    or s_complete is distinct from market_complete
    or round(s_prob, 6) is distinct from market_prob_on_winner
    or round(s_brier, 6) is distinct from brier_market
    or round(s_ll, 6) is distinct from log_loss_market
on conflict (checkpoint_id) do nothing;

create or replace view public.v_checkpoint_outcome as
select f.checkpoint_id, f.city_key, f.target_date, f.checkpoint, f.engine_version,
       f.local_decision_time, f.decided_at, f.winner_band_id, f.n_bands, f.ladder_has_winner,
       f.model_prob_on_winner, f.hit, f.brier_model, f.log_loss_model,
       case when x.checkpoint_id is null then f.market_top_band_id    else x.market_top_band_id    end as market_top_band_id,
       case when x.checkpoint_id is null then f.market_hit            else x.market_hit            end as market_hit,
       case when x.checkpoint_id is null then f.market_complete       else x.market_complete       end as market_complete,
       case when x.checkpoint_id is null then f.market_prob_on_winner else x.market_prob_on_winner end as market_prob_on_winner,
       case when x.checkpoint_id is null then f.brier_market          else x.brier_market          end as brier_market,
       case when x.checkpoint_id is null then f.log_loss_market       else x.log_loss_market       end as log_loss_market,
       f.brier_uniform, f.log_loss_uniform, f.settled_at, f.banked_at,
       x.checkpoint_id is not null                                                                  as market_corrected
  from public.fact_checkpoint_outcome f
  left join public.fact_checkpoint_outcome_market_fix x using (checkpoint_id);

comment on view public.v_checkpoint_outcome is
  'fact_checkpoint_outcome with the market columns of rows banked before book_mark() taken from fact_checkpoint_outcome_market_fix (market_corrected). Read this, or v_checkpoint_calls, for the market''s record; the table keeps what was written.';

revoke all on public.v_checkpoint_outcome from public, anon, authenticated;
grant select on public.v_checkpoint_outcome to service_role;

comment on table public.fact_checkpoint_outcome is
  'One row per prediction checkpoint once the venue confirmed the winner: whether the engine''s and the market''s top pick won, and both ladders'' Brier and log loss against a uniform guess (plan v2 P4.3). Append-only. The market columns of the rows banked before 26 Sep 16:38Z were scored before book_mark() and are wrong on 91 of them: read v_checkpoint_outcome (or v_checkpoint_calls).';
comment on column public.fact_checkpoint_outcome.market_hit is
  'Scored before book_mark() on the 98 rows banked before 26 Sep 16:38Z, and wrong on 91 of them: those are corrected in fact_checkpoint_outcome_market_fix and served by v_checkpoint_outcome.';
comment on column public.fact_checkpoint_outcome.market_top_band_id is
  'Scored before book_mark() on the 98 rows banked before 26 Sep 16:38Z, and wrong on 91 of them: those are corrected in fact_checkpoint_outcome_market_fix and served by v_checkpoint_outcome.';
comment on column public.fact_checkpoint_outcome.market_prob_on_winner is
  'Scored before book_mark() on the 98 rows banked before 26 Sep 16:38Z, and wrong on 91 of them: those are corrected in fact_checkpoint_outcome_market_fix and served by v_checkpoint_outcome.';
comment on column public.fact_checkpoint_outcome.brier_market is
  'Scored before book_mark() on the 98 rows banked before 26 Sep 16:38Z, and wrong on 91 of them: those are corrected in fact_checkpoint_outcome_market_fix and served by v_checkpoint_outcome.';
comment on column public.fact_checkpoint_outcome.log_loss_market is
  'Scored before book_mark() on the 98 rows banked before 26 Sep 16:38Z, and wrong on 91 of them: those are corrected in fact_checkpoint_outcome_market_fix and served by v_checkpoint_outcome.';
