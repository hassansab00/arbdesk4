-- Read weather inputs at the requested decision instants without transferring
-- every superseded payload in the 40-hour replay window. History stays intact.
create or replace function public.live_capture_asof(
  p_first timestamptz, p_until timestamptz, p_times timestamptz[], p_prefixes text[],
  p_current_receipts boolean default true,
  p_after_id bigint default 0, p_limit integer default 1000
) returns jsonb
language plpgsql stable security invoker
set search_path = public, pg_temp
set statement_timeout = '8s'
as $$
declare
  v_first timestamptz;
  v_last timestamptz;
  v_result jsonb;
begin
  select min(t), max(t) into v_first, v_last from unnest(p_times) t;
  if p_first is null or p_until is null or v_first is null or v_last is null
     or cardinality(p_times) not between 1 and 30
     or array_position(p_times,null) is not null
     or p_first > v_first or v_last > p_until or p_until - p_first > interval '48 hours'
     or cardinality(p_prefixes) not between 1 and 200
     or p_prefixes is null or exists (
       select 1 from unnest(p_prefixes) p where p is null or length(p)<2 or right(p,1)<>'|')
     or p_after_id is null or p_after_id<0
     or p_current_receipts is null or p_limit is null or p_limit not between 1 and 1000 then
    raise exception 'Use 1-30 instants within 48 hours, 1-200 literal prefixes ending |, and limit 1-1000';
  end if;
  with scoped as materialized (
    select c.capture_id,c.kind,c.key,
      case when p_current_receipts and c.kind in ('hourly0','models0')
           then greatest(c.captured_at,c.written_at) else c.captured_at end as available
    from public.live_capture c
    where c.captured_at>=p_first and c.captured_at<=p_until
      and c.kind in ('report','hourly','hourly2','models1','models2','hourly0','models0','awc_metar')
      and (not p_current_receipts or c.kind not in ('hourly0','models0') or c.written_at is not null)
      and exists(select 1 from unnest(p_prefixes) p where starts_with(c.key,p))
  ), first_seen as (
    select kind,key,min(capture_id) as first_capture_id from scoped group by kind,key
  ), winners as (
    select distinct w.capture_id
    from unnest(p_times) t
    cross join lateral (
      select distinct on (s.kind,s.key) s.capture_id
      from scoped s where s.available<=t
      order by s.kind,s.key,s.available desc,s.capture_id desc
    ) w
  ), page as materialized (
    select c.capture_id,c.kind,c.key,c.captured_at,c.written_at,c.payload,f.first_capture_id
    from public.live_capture c join winners w using(capture_id)
    join first_seen f on f.kind=c.kind and f.key=c.key
    where c.capture_id>p_after_id order by c.capture_id limit p_limit+1
  ), visible as (
    select * from page order by capture_id limit p_limit
  )
  select jsonb_build_object(
    'rows',coalesce((select jsonb_agg(to_jsonb(v) order by v.capture_id) from visible v),'[]'::jsonb),
    'has_more',(select count(*)>p_limit from page),
    'next_after_id',coalesce((select max(capture_id) from visible),p_after_id)
  ) into v_result;
  return v_result;
end $$;
revoke all on function public.live_capture_asof(timestamptz,timestamptz,timestamptz[],text[],boolean,bigint,integer)
  from public, anon, authenticated;
grant execute on function public.live_capture_asof(timestamptz,timestamptz,timestamptz[],text[],boolean,bigint,integer)
  to service_role;
