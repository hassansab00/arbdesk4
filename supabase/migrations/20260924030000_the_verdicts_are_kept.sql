-- ===========================================================================
-- THE VENUE'S VERDICTS ARE KEPT; ONLY THE PAYLOADS ARE ARCHIVED (plan v2 P4.5)
--
-- prune_resolution_evidence (ad4_74) archives a proof once its band outcome
-- is frozen in fact_band_outcome, and deletes the row. But
-- v_venue_band_resolution - the venue's answer every reader shares - is built
-- from those rows, so a pruned proof turned a confirmed band back into no
-- answer at all. Measured 24 Sep: of the 7,447 band outcomes banked since
-- 13 Sep, 2,948 had fallen out of v_verified_fact_band_outcome, 2,894 of them
-- because their band no longer had any venue row. Calibration, the
-- reliability haircut and the sigma multiplier learn from that view.
-- The settlement sweep also decides what it has already captured from the
-- same table, so it spent its budget fetching proofs it had pruned.
--
-- resolution_verdicts keeps what a verdict IS - the condition, the two tokens,
-- the winning token and when it was captured - and never the gamma / clob /
-- source_urls payloads, which are 45 MB of the table's 6,804 rows and are what
-- the archive exists for. Every proof written to paper_resolution_evidence is
-- copied here by trigger, the live ones are copied now, and
-- scripts/restore_verdicts.py adds back the ones already archived to
-- data/archive/resolution (proof_id null there: the export never carried it).
--
-- v_venue_band_resolution reads the ledger instead of the evidence table.
-- With the ledger holding exactly the live proofs it returns the same rows
-- (proven before applying: one REPEATABLE READ snapshot, EXCEPT ALL both
-- ways); every row the restore adds is a proof the platform had and lost.
--
-- Append-only; written by the service role; the browser reads the same six
-- verdict columns it may read on the evidence table, and nothing else.
-- ===========================================================================

create table if not exists public.resolution_verdicts (
  condition_id   text        not null,
  token_yes      text        not null,
  token_no       text        not null,
  winning_token  text        not null check (winning_token in (token_yes, token_no)),
  captured_at    timestamptz not null,
  proof_id       text,
  source         text        not null,
  recorded_at    timestamptz not null default clock_timestamp(),
  primary key (condition_id, token_yes, token_no, captured_at)
);

comment on table public.resolution_verdicts is
  'Every venue verdict ever captured - condition, tokens, winning token, time - kept after its proof''s payload is archived and pruned, so the venue''s answer never disappears from the platform (plan v2 P4.5). Append-only.';

drop trigger if exists resolution_verdicts_immutable on public.resolution_verdicts;
create trigger resolution_verdicts_immutable before update or delete on public.resolution_verdicts
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists resolution_verdicts_no_truncate on public.resolution_verdicts;
create trigger resolution_verdicts_no_truncate before truncate on public.resolution_verdicts
  for each statement execute function arbdesk_private.immutable_record();

alter table public.resolution_verdicts enable row level security;
revoke all on public.resolution_verdicts from public, anon, authenticated, service_role;
grant select, insert on public.resolution_verdicts to service_role;
-- The same read the evidence table gives the browser (20260913100000): the
-- verdict columns only, through security_invoker v_venue_band_resolution.
grant select (condition_id, token_yes, token_no, winning_token, captured_at, proof_id)
  on public.resolution_verdicts to anon, authenticated;
drop policy if exists resolution_verdicts_safe_read on public.resolution_verdicts;
create policy resolution_verdicts_safe_read on public.resolution_verdicts
  for select to anon, authenticated using (true);

-- Every new proof leaves its verdict behind.
create or replace function arbdesk_private.keep_resolution_verdict()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
begin
  insert into public.resolution_verdicts
    (condition_id, token_yes, token_no, winning_token, captured_at, proof_id, source)
  values (new.condition_id, new.token_yes, new.token_no, new.winning_token, new.captured_at,
          new.proof_id, 'evidence')
  on conflict do nothing;
  return new;
end $$;
revoke all on function arbdesk_private.keep_resolution_verdict() from public;

drop trigger if exists resolution_keeps_its_verdict on public.paper_resolution_evidence;
create trigger resolution_keeps_its_verdict after insert on public.paper_resolution_evidence
  for each row execute function arbdesk_private.keep_resolution_verdict();

-- The proofs still in the table.
insert into public.resolution_verdicts
  (condition_id, token_yes, token_no, winning_token, captured_at, proof_id, source)
select condition_id, token_yes, token_no, winning_token, captured_at, proof_id, 'evidence'
  from public.paper_resolution_evidence
on conflict do nothing;

-- The venue's answer, from every verdict ever captured. Unchanged but for the
-- source: same columns, same grouping, same security_invoker.
create or replace view public.v_venue_band_resolution
with (security_invoker = true)
as
with matched as (
  select b.band_id, b.market_id, b.condition_id, b.token_yes, b.token_no,
         e.proof_id, e.winning_token, e.captured_at
  from public.bands b
  join public.resolution_verdicts e
    on e.condition_id = b.condition_id
   and e.token_yes = b.token_yes
   and e.token_no = b.token_no
), summarized as (
  select band_id, market_id, condition_id, token_yes, token_no,
         count(*)::integer as evidence_count,
         count(distinct winning_token)::integer as winner_count,
         max(captured_at) as confirmed_at,
         min(winning_token) as winning_token
  from matched
  group by band_id, market_id, condition_id, token_yes, token_no
)
select band_id, market_id, condition_id, token_yes, token_no,
       evidence_count, confirmed_at,
       case when winner_count = 1 then winning_token end as winning_token,
       case when winner_count = 1 then winning_token = token_yes end as settled_yes,
       case when winner_count = 1 then 'confirmed' else 'disputed' end as resolution_state
from summarized;
