-- ===========================================================================
-- THE LIVE RECORD KEEPS THE EVENING'S FORECASTS (WXPredict build WP3b,
-- 12 Oct).
--
-- 20261011210000_the_live_record.sql keeps best_match's hourly
-- `_previous_day1` forecast ('hourly'). A decision's row also reads the
-- day-ahead daily forecast at lead 2 (every row of D-1) and the seven models'
-- daily maxima (every row) - tools/wxpredict/features.py pick_daily - and the
-- record had neither, so no live row could carry them. scripts/live_capture.py
-- now also keeps, the same way (once per change, with the first time seen),
-- keyed '<city>|<hour UTC>' like 'hourly':
--
--   kind 'hourly2'  best_match's seven variables at `_previous_day2`, every UTC
--                   hour from yesterday to 41 h ahead (an hour is known
--                   48 - PUBLISH_H h before it), asked in the same request as
--                   'hourly'
--   kind 'models1'  the seven models' temperature at `_previous_day1`, to 17 h
--   kind 'models2'  the same at `_previous_day2`, to 41 h; one more request a
--                   chunk of cities, asked as the nightly record asks it
--                   (honest_record.previous_request). A payload is the seven
--                   values in honest_record.MODELS order.
--
-- Measured before the change (12 Oct ~13:00Z): the record's 'hourly' rows grow
-- by exactly 47 a tick (one new hour a city; no stored hour changed over the
-- four ticks 09-12Z), and both wider requests answered 200 for 12 cities over
-- four days through pg_net (121,323 and 114,695 bytes).
--
-- Only the kinds widen: the table's check and live_capture_put's list. The
-- archive, the mirror and the prune already take every row by captured_at,
-- whatever its kind. The definitions are the ones in
-- sql/ad4_prune_live_capture.sql. Re-runnable. Service role only.
-- ===========================================================================

alter table public.live_capture drop constraint if exists live_capture_kind_check;
alter table public.live_capture add constraint live_capture_kind_check
  check (kind in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books'));

comment on table public.live_capture is
  'WXPredict WP3 (11 Oct): every live input the hourly tick had, as it arrived (captured_at): station reports with every training field, the hourly _previous_day1 forecast, every open bucket''s book; from 12 Oct also best_match at _previous_day2 and the seven models'' temperature at both leads (WP3b). A report or forecast value is stored once per change. Two days here; older rows in data/archive/live_capture.';

-- One batch of one kind: p_rows is [{"key": ..., "payload": ...}]. A report
-- or forecast row whose payload equals its key's newest stored payload is not
-- stored again; a key repeated inside the batch is stored once per distinct
-- payload. Returns {"rows": asked, "new": stored}.
create or replace function public.live_capture_put(p_kind text, p_captured_at timestamptz, p_rows jsonb)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $lc$
declare
  v_rows int;
  v_new  int;
begin
  if p_kind is null or p_kind not in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books') then
    raise exception 'live_capture_put: % is not a kind the live record keeps', p_kind using errcode = '22023';
  end if;
  if p_captured_at is null or p_captured_at > now() + interval '5 minutes' then
    raise exception 'live_capture_put: captured_at % is missing or in the future', p_captured_at using errcode = '22023';
  end if;
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'live_capture_put: p_rows must be a JSON array' using errcode = '22023';
  end if;

  with r as (
    select distinct x ->> 'key' as key, x -> 'payload' as payload
      from jsonb_array_elements(p_rows) x
     where x ->> 'key' is not null and x -> 'payload' is not null
  ), ins as (
    insert into public.live_capture (kind, key, captured_at, payload)
    select p_kind, r.key, p_captured_at, r.payload
      from r
     where p_kind = 'books'
        or r.payload is distinct from (
             select l.payload from public.live_capture l
              where l.kind = p_kind and l.key = r.key
              order by l.captured_at desc, l.capture_id desc
              limit 1)
    returning 1
  )
  select (select count(*) from jsonb_array_elements(p_rows)), (select count(*) from ins)
    into v_rows, v_new;
  return jsonb_build_object('rows', v_rows, 'new', v_new);
end;
$lc$;

revoke all on function public.live_capture_put(text, timestamptz, jsonb) from public, anon, authenticated;
grant execute on function public.live_capture_put(text, timestamptz, jsonb) to service_role;

notify pgrst, 'reload schema';
