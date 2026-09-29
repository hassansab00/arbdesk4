-- ===========================================================================
-- THE MARKS THE PRUNE TOOK COME BACK (plan v2 P1.6 phase 3, step 3.1b, 29 Sep)
--
-- Hassan, 29 Sep: "PROCEED WIT TE NEXT STEP".
--
-- 20260929220000 froze each band's eve and pre-day YES edge from here on. The
-- marks the edges prune had already taken are not lost: the archive exported
-- every pruned edge to data/archive/edges first (computed 3-5 and 12-21 Sep;
-- the engine priced nothing 6-11 Sep). Measured 29 Sep 18:35Z: 15,618 marks
-- due (cutoff six hours past) of 7,919 priced bands are not in
-- derived_edge_marks.
--
-- restore_edge_marks writes them from the archive rows
-- tools/restore_edge_marks.py sends (whole bands per call), through
-- .github/workflows/restore_edge_marks.yml, which holds the service key. The
-- same text as sql/ad4_97. Writes nothing by itself.
-- ===========================================================================

create or replace function public.restore_edge_marks(p_rows jsonb, p_dry_run boolean default true)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_rows int;
  v_bands int;
  v_unknown int;
  v_found int;
  v_frozen int;
  v_live int;
  v_written int := 0;
begin
  drop table if exists pg_temp._restore_marks;
  create temp table _restore_marks on commit drop as
  with arch as (
    select x.band_id, x.computed_at, x.market_price, x.source
      from jsonb_to_recordset(p_rows) as x(band_id uuid, computed_at timestamptz, market_price numeric, source text)
  ),
  cut as (
    select b.band_id, v.mark, v.cutoff_at
      from (select distinct band_id from arch) a
      join bands b on b.band_id = a.band_id
      join markets m on m.market_id = b.market_id
      left join cities c on c.city_key = m.city_key
      cross join lateral (values
        ('eve'::text, (((m.resolution_date - 1)::timestamp + interval '18 hours')
                        at time zone coalesce(c.timezone, 'UTC'))),
        ('day'::text, (m.resolution_date::timestamp at time zone coalesce(c.timezone, 'UTC')))
      ) v(mark, cutoff_at)
     where m.resolution_date is not null
       and v.cutoff_at < now() - interval '6 hours'
  )
  select k.band_id, k.mark, k.cutoff_at, x.computed_at, x.market_price, x.source,
         exists (select 1 from derived_edge_marks d
                  where d.band_id = k.band_id and d.mark = k.mark) as frozen,
         exists (select 1 from edges e
                  where e.band_id = k.band_id and e.side = 'YES'
                    and e.computed_at >= x.computed_at
                    and e.computed_at <= k.cutoff_at
                    and (k.mark = 'eve' or e.computed_at < k.cutoff_at)) as live_holds
    from cut k
    cross join lateral (
      select a.computed_at, a.market_price, a.source
        from arch a
       where a.band_id = k.band_id
         and a.computed_at <= k.cutoff_at
         and (k.mark = 'eve' or a.computed_at < k.cutoff_at)
       order by a.computed_at desc
       limit 1) x;

  select count(*), count(distinct band_id) into v_rows, v_bands
    from jsonb_to_recordset(p_rows) as x(band_id uuid);
  select count(distinct x.band_id) into v_unknown
    from jsonb_to_recordset(p_rows) as x(band_id uuid)
   where not exists (select 1 from bands b where b.band_id = x.band_id);
  select count(*), count(*) filter (where frozen), count(*) filter (where live_holds and not frozen)
    into v_found, v_frozen, v_live
    from _restore_marks;

  if not p_dry_run then
    insert into derived_edge_marks (band_id, mark, cutoff_at, computed_at, market_price, edge_id, source)
    select band_id, mark, cutoff_at, computed_at, market_price, null, source
      from _restore_marks
     where not frozen and not live_holds
    on conflict (band_id, mark) do nothing;
    get diagnostics v_written = row_count;
  end if;

  return jsonb_build_object(
    'ok', true, 'dry_run', p_dry_run,
    'rows', v_rows, 'bands', v_bands, 'bands_unknown', v_unknown,
    'marks_found', v_found, 'already_frozen', v_frozen, 'edges_holds_it', v_live,
    'to_write', v_found - v_frozen - v_live, 'written', v_written,
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.restore_edge_marks(jsonb, boolean) is
  'Write each band''s missing eve and pre-day YES mark into derived_edge_marks from archived edges (data/archive/edges, all of a band''s YES rows in one call): only a cutoff six hours past, never over a frozen mark, never where edges still holds that mark; edge_id NULL, source the file. Dry run by default (plan v2 P1.6 phase 3, step 3.1b; tools/restore_edge_marks.py).';

revoke all on function public.restore_edge_marks(jsonb, boolean) from public, anon, authenticated;
grant execute on function public.restore_edge_marks(jsonb, boolean) to service_role;
