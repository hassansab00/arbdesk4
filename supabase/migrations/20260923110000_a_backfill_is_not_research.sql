-- A BACKFILL IS NOT RESEARCH (plan v2, step P1.4).
--
-- preserve_research_output copies a row into research_captures after every
-- INSERT OR UPDATE on six tables, band_probabilities among them. Its dedupe
-- is on the whole row's hash, so an UPDATE of any column - a backfill of a
-- label, a flag, a floor - makes a new payload and a new capture of a price
-- that did not change. The plan's audit found one untracked UPDATE on 22 Sep
-- that copied 66,345 rows in an hour. band_probabilities is 82,350 of the
-- 116,789 captures, 79 MB of 135 MB (live, 23 Sep:
-- `select source_relation, count(*), sum(pg_column_size(r.*)) from
-- research_captures r group by 1`).
--
-- Two changes, both to what gets captured and neither to what is kept:
--
--   1. band_probabilities: an INSERT is always captured (a new price is
--      research output). An UPDATE is captured only when a pricing column
--      actually changes: raw_prob, calibrated_prob, centre_c or sigma_c.
--   2. A session switch for deliberate backfills, on every table the trigger
--      is attached to:
--          begin; set local arbdesk.skip_capture = on; update ...; commit;
--      `set local` ends with the transaction, so it cannot leak into the
--      next statement on a pooled connection.
--
-- Idempotent: create or replace, and drop trigger if exists before create.

create or replace function arbdesk_private.archive_research_row() returns trigger
language plpgsql security definer set search_path='' as $$
declare payload jsonb; identity_key text; version text;
begin
  if current_setting('arbdesk.skip_capture', true) = 'on' then
    return new;
  end if;
  payload:=to_jsonb(new);
  identity_key:=coalesce(payload->>'prob_id',payload->>'signal_id',payload->>'version_id',
    payload->>'band_id',payload->>'city_key','row') || ':' || coalesce(payload->>'for_date','');
  version:=coalesce(payload->>'forecast_version',payload->>'calibration_version',
    payload->>'version_id','legacy-producer-unversioned');
  insert into public.research_captures(command_key,engine_version,provenance,source_relation,source_key,payload,payload_hash)
  values(gen_random_uuid()::text,version,'source_revision',tg_table_name,identity_key,payload,md5(payload::text))
  on conflict(source_relation,source_key,payload_hash) do nothing;
  return new;
end $$;
revoke all on function arbdesk_private.archive_research_row() from public;

do $$
begin
  if to_regclass('public.band_probabilities') is null then return; end if;
  drop trigger if exists preserve_research_output on public.band_probabilities;
  drop trigger if exists preserve_research_output_on_insert on public.band_probabilities;
  drop trigger if exists preserve_research_output_on_reprice on public.band_probabilities;
  create trigger preserve_research_output_on_insert
    after insert on public.band_probabilities
    for each row execute function arbdesk_private.archive_research_row();
  create trigger preserve_research_output_on_reprice
    after update of raw_prob, calibrated_prob, centre_c, sigma_c on public.band_probabilities
    for each row
    when ((old.raw_prob, old.calibrated_prob, old.centre_c, old.sigma_c)
          is distinct from (new.raw_prob, new.calibrated_prob, new.centre_c, new.sigma_c))
    execute function arbdesk_private.archive_research_row();
end $$;
