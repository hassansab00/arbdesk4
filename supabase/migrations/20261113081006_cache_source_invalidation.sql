-- Only base-table changes invalidate display caches; refreshes cannot dirty themselves.
create or replace function arbdesk_private.page_cache_source_token()
returns text language sql stable security definer
set search_path = pg_catalog, pg_temp as $$
  select md5(concat_ws('|',
    date_trunc('hour', statement_timestamp() at time zone 'UTC')::text,
    (select stats_reset::text from pg_stat_database where datname=current_database()),
    (select string_agg(concat_ws(':',s.relid,s.n_tup_ins,s.n_tup_upd,s.n_tup_del),',' order by s.relid)
       from pg_stat_all_tables s join pg_class c on c.oid=s.relid
      where s.schemaname='public' and s.relname<>'ingest_log'
        and c.relkind in ('r','p'))));
$$;
revoke all on function arbdesk_private.page_cache_source_token() from public, anon, authenticated;
