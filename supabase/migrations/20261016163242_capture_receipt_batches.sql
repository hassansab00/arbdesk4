-- Combine disjoint response chunks without changing when any value arrived.
-- Legacy callers still use p_captured_at for every row. Per-row receipts are
-- transport metadata, never inserted into the forecast payload.
create or replace function public.live_capture_put(p_kind text, p_captured_at timestamptz, p_rows jsonb)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $lc$
declare
  v_rows int;
  v_new int;
begin
  if p_kind is null or p_kind not in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books', 'focus_book', 'wx_features', 'hourly0', 'models0') then
    raise exception 'live_capture_put: % is not a kind the live record keeps', p_kind using errcode = '22023';
  end if;
  if p_captured_at is null or p_captured_at > now() + interval '5 minutes' then
    raise exception 'live_capture_put: captured_at % is missing or in the future', p_captured_at using errcode = '22023';
  end if;
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'live_capture_put: p_rows must be a JSON array' using errcode = '22023';
  end if;
  if exists (select 1 from jsonb_array_elements(p_rows) x
              where (x->>'captured_at')::timestamptz > now() + interval '5 minutes') then
    raise exception 'live_capture_put: a row receipt is in the future' using errcode = '22023';
  end if;
  -- A transport batch cannot collapse successive versions of one key.
  -- Duplicate identical rows remain legal, as for legacy callers.
  if exists (select 1 from jsonb_array_elements(p_rows) x where x ? 'captured_at')
     and exists (select 1 from (
       select distinct x->>'key' as key, x->'payload' as payload,
              coalesce((x->>'captured_at')::timestamptz,p_captured_at) as at
       from jsonb_array_elements(p_rows) x
       where x->>'key' is not null and x->'payload' is not null
     ) r group by key having count(*) > 1) then
    raise exception 'live_capture_put: successive versions need separate batches' using errcode = '22023';
  end if;
  with r as (
    select distinct x->>'key' as key, x->'payload' as payload,
           coalesce((x->>'captured_at')::timestamptz,p_captured_at) as captured_at
    from jsonb_array_elements(p_rows) x
    where x->>'key' is not null and x->'payload' is not null
  ), ins as (
    insert into public.live_capture(kind,key,captured_at,payload)
    select p_kind,r.key,r.captured_at,r.payload from r
    where p_kind='books' or r.payload is distinct from (
      select l.payload from public.live_capture l
      where l.kind=p_kind and l.key=r.key
      order by l.captured_at desc,l.capture_id desc limit 1)
    returning 1
  )
  select (select count(*) from jsonb_array_elements(p_rows)),(select count(*) from ins)
  into v_rows,v_new;
  return jsonb_build_object('rows',v_rows,'new',v_new);
end;
$lc$;
revoke all on function public.live_capture_put(text,timestamptz,jsonb) from public,anon,authenticated;
grant execute on function public.live_capture_put(text,timestamptz,jsonb) to service_role;
notify pgrst, 'reload schema';
