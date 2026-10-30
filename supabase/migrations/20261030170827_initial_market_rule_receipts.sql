-- Keep the initial rules available before the prospective predictor freezes.
-- Recovery only fills a blank field; known rules are never overwritten here.
create table if not exists public.market_rule_receipts(
  receipt_id bigint generated always as identity primary key,
  market_id uuid not null,
  event_slug text not null,
  received_at timestamptz not null default clock_timestamp(),
  rules_text text not null check(length(btrim(rules_text))>0),
  source_url text not null
);
alter table public.market_rule_receipts enable row level security;
revoke all on public.market_rule_receipts from public,anon,authenticated,service_role;
grant select on public.market_rule_receipts to service_role;
drop policy if exists market_rule_receipt_read on public.market_rule_receipts;
create policy market_rule_receipt_read on public.market_rule_receipts for select to service_role using(true);
drop trigger if exists market_rule_receipt_immutable on public.market_rule_receipts;
create trigger market_rule_receipt_immutable before update or delete on public.market_rule_receipts
for each row execute function arbdesk_private.immutable_record();
drop trigger if exists market_rule_receipt_no_truncate on public.market_rule_receipts;
create trigger market_rule_receipt_no_truncate before truncate on public.market_rule_receipts
for each statement execute function arbdesk_private.immutable_record();

create or replace function public.record_missing_market_rules(p_market uuid,p_slug text,p_rules text)
returns jsonb language plpgsql security definer set search_path='' as $$
declare m public.markets; stamp timestamptz:=clock_timestamp();
begin
  select * into m from public.markets where market_id=p_market for update;
  if not found or m.event_slug is distinct from p_slug or p_slug is null or p_slug !~ '^[a-z0-9-]+$' then
    raise exception 'Rules event identity mismatch'; end if;
  if nullif(btrim(m.rules_text),'') is not null then
    return jsonb_build_object('stored',false,'rules_text',m.rules_text,'received_at',m.rules_fetched_at); end if;
  if p_rules is null or length(btrim(p_rules))=0 or octet_length(p_rules)>262144 then
    raise exception 'Rules text absent or too large'; end if;
  insert into public.market_rule_receipts(market_id,event_slug,received_at,rules_text,source_url)
    values(p_market,p_slug,stamp,p_rules,'https://gamma-api.polymarket.com/events?slug='||p_slug);
  update public.markets set rules_text=p_rules,rules_fetched_at=stamp where market_id=p_market;
  return jsonb_build_object('stored',true,'rules_text',p_rules,'received_at',stamp);
end $$;
revoke all on function public.record_missing_market_rules(uuid,text,text) from public,anon,authenticated;
grant execute on function public.record_missing_market_rules(uuid,text,text) to service_role;

do $migration$
declare old_def text; opts text[]; option_sql text; differences bigint;
begin
  if to_regclass('public.data_freshness_spec') is null then return; end if;
  insert into public.data_freshness_spec(table_name,ts_column,fresh_hours,layer,plain_english)
    values('market_rule_receipts','received_at',null,'market','Received initial event rules retained before prospective forecasts; accumulating record.')
    on conflict(table_name) do nothing;
  if to_regclass('public.v_data_freshness') is null then return; end if;
  old_def:=regexp_replace(pg_get_viewdef('public.v_data_freshness'::regclass,true),';\s*$','');
  if position('market_rule_receipts' in old_def)>0 then return; end if;
  select reloptions into opts from pg_class where oid='public.v_data_freshness'::regclass;
  option_sql:=case when opts is null then '' else ' with ('||array_to_string(opts,',')||')' end;
  execute 'create or replace view public.v_data_freshness'||option_sql||' as select * from ('||old_def||') previous union all
    select ''market_rule_receipts''::text,''market''::text,
      ''Received initial event rules retained before prospective forecasts; accumulating record.''::text,
      count(*)::bigint,false,max(received_at),round(extract(epoch from(now()-max(received_at)))/3600,1),null::numeric,
      case when count(*)=0 then ''empty'' else ''ok'' end from public.market_rule_receipts';
  execute 'select count(*) from ((('||old_def||') except all (select * from public.v_data_freshness where table_name<>''market_rule_receipts''))
    union all ((select * from public.v_data_freshness where table_name<>''market_rule_receipts'') except all ('||old_def||'))) d' into differences;
  if differences<>0 then raise exception 'Existing freshness rows changed'; end if;
end $migration$;
