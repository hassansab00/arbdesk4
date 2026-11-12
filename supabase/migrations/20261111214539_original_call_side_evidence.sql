-- Original-bucket YES/NO paper v2. Private evidence and private funded sleeves.
-- V1 tables, guards, settings, balances, predictions and model weights are untouched.
create table if not exists arbdesk_private.original_side_owners(
  owner_id uuid primary key default gen_random_uuid(), created_at timestamptz not null default clock_timestamp()
);
create table if not exists arbdesk_private.original_side_sessions(
  token_sha256 text primary key, owner_id uuid not null references arbdesk_private.original_side_owners,
  issued_at timestamptz not null default clock_timestamp(), expires_at timestamptz not null
);
create table if not exists arbdesk_private.original_side_rule_receipts(
 rules_sha256 text primary key,rules_text text not null,received_at timestamptz not null,source_url text not null
);
create table if not exists arbdesk_private.original_side_ladder_receipts(
 ladder_sha256 text primary key,ladder jsonb not null
);
create table if not exists arbdesk_private.original_side_calls(
  call_id uuid primary key, city_key text not null, target_date date not null,
  checkpoint text not null, source_kind text not null, frozen_at timestamptz,
  source jsonb not null, identity jsonb not null, eligibility text not null,
  cutoff_at timestamptz, registered_at timestamptz not null default clock_timestamp()
);
create index if not exists original_side_calls_scope on arbdesk_private.original_side_calls(target_date,city_key,checkpoint,call_id);
create table if not exists arbdesk_private.original_side_books(
  snapshot_id text primary key, depth_hash text not null, token_id text not null,
  observed_at timestamptz not null, received_at timestamptz not null default clock_timestamp(),
  payload jsonb not null
);
create table if not exists arbdesk_private.original_side_quotes(
  attempt_id uuid primary key, call_id uuid not null references arbdesk_private.original_side_calls,
  side text not null check(side in ('YES','NO')), quote_kind text not null check(quote_kind in ('direct','missing')),
  status text not null check(status in ('captured','blocked','failed')), reason text,
  requested_at timestamptz not null, received_at timestamptz not null default clock_timestamp(),
  receipt jsonb not null default '{}',
  snapshot_id text references arbdesk_private.original_side_books,
  check((status='captured')=(snapshot_id is not null)), check((quote_kind='direct')=(snapshot_id is not null))
);
create index if not exists original_side_quotes_call_side on arbdesk_private.original_side_quotes(call_id,side,received_at,attempt_id);
create table if not exists arbdesk_private.original_side_evaluations(
  evaluation_id uuid primary key default gen_random_uuid(), quote_id uuid not null references arbdesk_private.original_side_quotes,
  calculation_version text not null, stake numeric not null check(stake>0 and stake<=1000000),
  result jsonb not null, created_at timestamptz not null default clock_timestamp(),
  unique(quote_id,calculation_version,stake)
);
create table if not exists arbdesk_private.original_side_policies(
  policy_id uuid primary key default gen_random_uuid(), owner_id uuid not null,
  config jsonb not null, created_at timestamptz not null default clock_timestamp()
);
create table if not exists arbdesk_private.original_side_policy_events(
  event_id bigint generated always as identity primary key,
  policy_id uuid not null references arbdesk_private.original_side_policies,
  action text not null check(action in ('activate','pause','resume')),
  recorded_at timestamptz not null default clock_timestamp()
);
create index if not exists original_side_policy_events_latest on arbdesk_private.original_side_policy_events(policy_id,event_id desc);
create table if not exists arbdesk_private.original_side_accounts(
  account_id uuid primary key default gen_random_uuid(), owner_id uuid not null,
  policy_id uuid not null unique references arbdesk_private.original_side_policies,
  starting_cash numeric not null, cash numeric not null, reserved_cash numeric not null default 0 check(reserved_cash>=0),
  activated_at timestamptz not null default clock_timestamp()
);
create index if not exists original_side_accounts_owner on arbdesk_private.original_side_accounts(owner_id,account_id);
create table if not exists arbdesk_private.original_side_allocation_plans(
  plan_id uuid primary key default gen_random_uuid(), account_id uuid not null references arbdesk_private.original_side_accounts,
  policy_id uuid not null references arbdesk_private.original_side_policies, target_date date not null,
  available_settled_cash numeric not null, allocation_per_slot numeric not null,
  rounding_remainder numeric not null, frozen_at timestamptz not null default clock_timestamp(),
  unique(account_id,target_date)
);
create table if not exists arbdesk_private.original_side_allocations(
  allocation_id uuid primary key default gen_random_uuid(), plan_id uuid not null references arbdesk_private.original_side_allocation_plans,
  city_key text not null, checkpoint text not null, timezone text not null, side text not null check(side in ('YES','NO')),
  reserved_amount numeric not null check(reserved_amount>=0), unique(plan_id,city_key)
);
create table if not exists arbdesk_private.original_side_decisions(
  decision_id uuid primary key default gen_random_uuid(), allocation_id uuid not null unique references arbdesk_private.original_side_allocations,
  account_id uuid not null references arbdesk_private.original_side_accounts,
  policy_id uuid not null references arbdesk_private.original_side_policies,
  call_id uuid not null references arbdesk_private.original_side_calls, side text not null check(side in ('YES','NO')),
  quote_id uuid not null references arbdesk_private.original_side_quotes,
  model_prob_yes numeric not null check(model_prob_yes between 0 and 1),
  selected_side_probability numeric not null check(selected_side_probability between 0 and 1),
  snapshot jsonb not null, decided_at timestamptz not null default clock_timestamp(),
  unique(policy_id,account_id,call_id,side)
);
create table if not exists arbdesk_private.original_side_orders(
  order_id uuid primary key default gen_random_uuid(), command_key uuid not null unique,
  account_id uuid not null references arbdesk_private.original_side_accounts,
  decision_id uuid not null unique references arbdesk_private.original_side_decisions,
  allocation_id uuid not null unique references arbdesk_private.original_side_allocations,
  call_id uuid not null references arbdesk_private.original_side_calls,
  band_id uuid not null, token_id text not null, side text not null check(side in ('YES','NO')),
  action text not null default 'BUY' check(action='BUY'), strategy_id text not null default 's15_original_bucket_sides',
  shares numeric not null check(shares>0), limit_price numeric not null check(limit_price>0 and limit_price<1),
  cash_ceiling numeric not null check(cash_ceiling>0), share_step numeric not null check(share_step>0),
  max_book_age_seconds integer not null default 120 check(max_book_age_seconds=120),
  requested_at timestamptz not null default clock_timestamp(), expires_at timestamptz not null,
  status text not null default 'queued' check(status in ('queued','working','filled','partial','rejected','expired')),
  lease_token uuid, lease_until timestamptz, result jsonb,
  private_side boolean not null default true check(private_side)
);
create index if not exists original_side_orders_queue on arbdesk_private.original_side_orders(status,requested_at);
create index if not exists original_side_orders_account_call on arbdesk_private.original_side_orders(account_id,call_id,side);
create table if not exists arbdesk_private.original_side_execution_events(
  order_id uuid primary key references arbdesk_private.original_side_orders,
  result jsonb not null, recorded_at timestamptz not null default clock_timestamp()
);
create table if not exists arbdesk_private.original_side_resolution_receipts(
  proof_hash text primary key, condition_id text not null, evidence jsonb not null,
  received_at timestamptz not null default clock_timestamp()
);
create table if not exists arbdesk_private.original_side_settlements(
  settlement_id uuid primary key default gen_random_uuid(), call_id uuid not null references arbdesk_private.original_side_calls,
  proof_hash text not null references arbdesk_private.original_side_resolution_receipts, state text not null check(state in ('verified_binary','verified_split','verified_void','pending','disputed','unverified')),
  payout_yes numeric, payout_no numeric, evidence jsonb not null,
  received_at timestamptz not null default clock_timestamp(),
  check((state in ('verified_binary','verified_split','verified_void')) = (payout_yes is not null and payout_no is not null)),
  check(payout_yes between 0 and 1 and payout_no between 0 and 1)
);
create index if not exists original_side_settlements_latest on arbdesk_private.original_side_settlements(call_id,received_at desc,settlement_id);
create table if not exists arbdesk_private.original_side_ledger(
  entry_id bigint generated always as identity primary key, account_id uuid not null references arbdesk_private.original_side_accounts,
  event_key text not null unique, event_type text not null, call_id uuid, order_id uuid,
  settlement_id uuid references arbdesk_private.original_side_settlements,
  cash_delta numeric not null, reserved_delta numeric not null, detail jsonb not null default '{}',
  recorded_at timestamptz not null default clock_timestamp()
);
create index if not exists original_side_ledger_account on arbdesk_private.original_side_ledger(account_id,entry_id);
create table if not exists arbdesk_private.original_side_stage_events(
  event_id bigint generated always as identity primary key, call_id uuid, account_id uuid, allocation_id uuid,
  stage text not null, reason text not null, recorded_at timestamptz not null default clock_timestamp()
);
create index if not exists original_side_stage_scope on arbdesk_private.original_side_stage_events(call_id,recorded_at desc);

-- This feature never feeds a public mirror or a browser raw-packet response.
do $$ declare t text; begin
 for t in select tablename from pg_tables where schemaname='arbdesk_private' and tablename like 'original_side_%' loop
  execute format('alter table arbdesk_private.%I enable row level security',t);
  execute format('revoke all on arbdesk_private.%I from public,anon,authenticated',t);
  execute format('grant all on arbdesk_private.%I to service_role',t);
  if t not in ('original_side_accounts','original_side_orders') then
   execute format('create or replace trigger original_side_immutable before update or delete on arbdesk_private.%I for each row execute function arbdesk_private.immutable_record()',t);
  end if;
  execute format('create or replace trigger original_side_no_truncate before truncate on arbdesk_private.%I for each statement execute function arbdesk_private.immutable_record()',t);
 end loop;
end $$;
grant usage,select on all sequences in schema arbdesk_private to service_role;

-- Numeric finance is shared by observation, order sizing and all readers.
-- Requested allocation is not a loss: only filled notional and per-level fees count.
create or replace function arbdesk_private.original_side_capacity() returns jsonb
language sql stable set search_path='' as $$
 select jsonb_build_object('bytes',coalesce(sum(pg_total_relation_size(c.oid)),0),
  'limit_bytes',16777216,'capture_allowed',coalesce(sum(pg_total_relation_size(c.oid)),0)<16777216)
 from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='arbdesk_private' and c.relkind='r' and c.relname like 'original_side_%'
$$;

-- Same dated station/unit/window semantics as the existing authoritative
-- adapters. Unknown primary sources cannot acquire a verified trade verdict.
create or replace function arbdesk_private.original_side_rule_identity(rules text,station text,unit text,target date,zone text)
returns boolean language plpgsql set search_path='' as $$
declare lower_rules text:=lower(regexp_replace(rules,'\s+',' ','g'));u text;anchor timestamptz;hours integer;
begin
 if rules is null or station is null or unit is null or unit not in ('C','F') then return false;end if;
 if position('https://aviationweather.gov/api/data/metar?' in rules)>0 then
  u:=rtrim(substring(rules from 'https://aviationweather\.gov/api/data/metar\?[^\s<>\\]+'),'.,)');
  if upper(substring(u from '[?&]ids=([A-Za-z0-9]{4})(&|$)')) is distinct from station
   or u !~ '[?&]format=raw(&|$)' or lower_rules not like '%a report whose line begins with speci%'
   or lower_rules not like '%exactly eight digits%' or lower_rules not like '%first metar observation%'
   or lower_rules not like '%station''s local time%'
   or lower_rules not like ('%each market in this event states its range in whole degrees '||case when unit='F' then 'fahrenheit' else 'celsius' end||'%') then return false;end if;
  anchor:=replace(replace(substring(u from '[?&]date=([^&]+)'),'%3A',':'),'%2B','+')::timestamptz;
  hours:=substring(u from '[?&]hours=([0-9]+)')::integer;
  return coalesce(hours between 1 and 48 and anchor>=((target+1)::timestamp at time zone zone)
   and anchor-hours*interval '1 hour'<=(target::timestamp at time zone zone),false);
 elsif lower_rules like '%weather.gov/wrh/timeseries?site=%' then
  return coalesce(upper(substring(rules from '(?i)weather\.gov/wrh/timeseries\?site=([A-Za-z0-9]{4})'))=station
   and lower_rules like case when unit='F' then '%degrees fahrenheit%' else '%degrees celsius%' end,false);
 elsif lower_rules like '%hong kong observatory%' and lower_rules like '%daily extract%' then return station='HKO' and unit='C';
 end if;
 return false;
exception when others then return false;
end $$;

create or replace function arbdesk_private.original_side_calculate(p_book jsonb,p_stake numeric,p_max_shares numeric default null,p_limit numeric default null)
returns jsonb language plpgsql set search_path='' as $$
declare level record; price numeric; available numeric; quantity numeric; take numeric;
 step numeric; tick numeric; rate numeric; minimum numeric; cost numeric:=0; shares numeric:=0; fees numeric:=0;
 n numeric; f numeric; limit_price numeric; fills jsonb:='[]'; left_cash numeric; bid numeric; ask numeric;
begin
 if p_stake is null or p_stake<=0 or p_stake>1000000 or p_stake::text in ('NaN','Infinity','-Infinity') then raise exception 'invalid_stake'; end if;
 p_book:=p_book||jsonb_build_object('asks',coalesce(p_book->'asks',p_book#>'{raw_book,asks}'),
  'bids',coalesce(p_book->'bids',p_book#>'{raw_book,bids}'));
 if p_book->>'quote_kind' is distinct from 'direct' then raise exception 'direct_token_book_required'; end if;
 if (p_book->>'tradeable')::boolean is distinct from true then raise exception 'market_not_tradeable'; end if;
 step:=(p_book->>'share_step')::numeric; tick:=(p_book->>'tick_size')::numeric;
 rate:=(p_book->>'fee_rate')::numeric; minimum:=(p_book->>'min_order_size')::numeric;
 if step is null or step<>.01 or tick is null or tick not in (.1,.01,.001,.0001)
  or rate is null or rate<0 or rate>1 or minimum is null or minimum<=0
  or p_book->>'min_order_size_unit' is distinct from 'shares'
  or p_book->>'fee_rounding' is distinct from 'half_up_5dp' then raise exception 'unverified_fee_minimum_or_precision'; end if;
 if jsonb_typeof(p_book->'asks') is distinct from 'array' or jsonb_array_length(p_book->'asks')=0 then raise exception 'missing_direct_token_depth'; end if;
 if exists(select 1 from jsonb_array_elements(p_book->'asks') x where not coalesce((x->>'price')::numeric>0 and (x->>'price')::numeric<1
  and mod((x->>'price')::numeric,tick)=0 and (x->>'size')::numeric>0,false)) then raise exception 'invalid_book_level'; end if;
 for level in select (x->>'price')::numeric p,sum((x->>'size')::numeric) q from jsonb_array_elements(p_book->'asks') x group by 1 order by 1 loop
  price:=level.p; available:=floor(level.q/step)*step;
  if p_limit is not null and price>p_limit then exit; end if;
  if p_max_shares is not null then available:=least(available,greatest(0,p_max_shares-shares)); end if;
  left_cash:=p_stake-cost-fees;
  quantity:=least(available,floor(left_cash/(price+rate*price*(1-price))/step)*step);
  -- Correct for rounded per-fill fees; at most a small bounded adjustment.
  while quantity>0 and quantity*price+round(quantity*rate*price*(1-price),5)>left_cash loop quantity:=quantity-step; end loop;
  while quantity+step<=available and (quantity+step)*price+round((quantity+step)*rate*price*(1-price),5)<=left_cash loop quantity:=quantity+step; end loop;
  if quantity<=0 then continue; end if;
  n:=quantity*price; f:=round(quantity*rate*price*(1-price),5);
  shares:=shares+quantity;cost:=cost+n;fees:=fees+f;limit_price:=price;
  fills:=fills||jsonb_build_array(jsonb_build_object('price',price::text,'shares',quantity::text,'notional',n::text,'fee',f::text));
 end loop;
 if shares<minimum then raise exception 'allocation_below_venue_minimum'; end if;
 return jsonb_build_object('calculation_version','original-bucket-sides-v2.1','quote_kind','direct',
  'requested_stake',p_stake::text,'shares',shares::text,'notional',cost::text,'fee',fees::text,
  'filled_cost',(cost+fees)::text,'unspent_allocation',(p_stake-cost-fees)::text,
  'profit_if_win',(shares-cost-fees)::text,'loss_if_lose',(-cost-fees)::text,'payout_if_win',shares::text,
  'best_ask',(select min((x->>'price')::numeric)::text from jsonb_array_elements(p_book->'asks') x),
  'best_bid',(select max((x->>'price')::numeric)::text from jsonb_array_elements(coalesce(p_book->'bids','[]')) x),
  'limit_price',limit_price::text,'vwap',(cost/shares)::text,'fills',fills);
end $$;

-- A direct packet without executable depth remains an observation attempt,
-- not successful quote coverage. Later retries retain their own receipts.
create or replace function arbdesk_private.original_side_quote_usable(p_book jsonb,p_stake numeric)
returns boolean language plpgsql set search_path='' as $$
begin
 return coalesce((arbdesk_private.original_side_calculate(p_book,p_stake)->>'shares')::numeric>0,false);
exception when others then return false;
end $$;

create or replace function arbdesk_private.original_side_observation_complete(p_call uuid,p_side text)
returns boolean language sql set search_path='' as $$
 select exists(select 1 from arbdesk_private.original_side_quotes q
  left join arbdesk_private.original_side_evaluations e on e.quote_id=q.attempt_id
   and e.calculation_version='original-bucket-sides-v2.1' and e.stake=10
  where q.call_id=p_call and q.side=p_side and
   (q.status='blocked' or (q.status='captured' and (e.result->>'shares')::numeric>0)))
$$;

create or replace function arbdesk_private.original_side_source(p_call uuid)
returns jsonb language plpgsql set search_path='' as $$
declare p public.prediction_checkpoints; b record; m record; c record; r record;
 ctx jsonb; stamp timestamptz:=clock_timestamp(); scheduled timestamptz; start_at timestamptz;
 cutoff timestamptz; candidate timestamptz; hour numeric; ref jsonb; k text; reason text:='eligible';
 ladder jsonb; rule_text text; rule_received timestamptz; receipt_id bigint; source_kind text:='prediction_checkpoints';
begin
 select * into p from public.prediction_checkpoints where checkpoint_id=p_call;
 if not found then
  select x.* into p from public.day_max_day_ahead_calls d cross join lateral jsonb_populate_record(null::public.prediction_checkpoints,d.record) x where d.call_id=p_call;
  source_kind:='day_max_day_ahead_calls';
 end if;
 if p.checkpoint_id is null then raise exception 'original_call_unavailable'; end if;
 -- Select first BEFORE eligibility. Native day_ahead and legacy midnight receipts are distinct sources.
 if source_kind='prediction_checkpoints' and exists(select 1 from public.prediction_checkpoints x where x.city_key=p.city_key and x.target_date=p.target_date
   and x.checkpoint=p.checkpoint and (x.decided_at,x.checkpoint_id)<(p.decided_at,p.checkpoint_id)) then raise exception 'not_first_original_call'; end if;
 select * into b from public.v_canonical_bands where band_id::text=p.top_band_id;
 if not found then raise exception 'original_band_unavailable'; end if;
 select * into m from public.v_canonical_markets where market_id=b.market_id;
 select * into c from public.cities where city_key=p.city_key;
 if m.city_key is distinct from p.city_key or m.resolution_date is distinct from p.target_date or c.timezone is null then raise exception 'wrong_call_market_date_or_timezone'; end if;
 ctx:=coalesce(p.predictive_context,'{}');
 scheduled:=p.local_decision_time at time zone c.timezone;
 start_at:=p.target_date::timestamp at time zone c.timezone;
 if p.checkpoint in ('prepeak_2h','prepeak_1h') then cutoff:=scheduled+case when p.checkpoint='prepeak_2h' then interval '2 hours' else interval '1 hour' end; end if;
 foreach k in array array['clim_peak_hour','fch_peak_local_hour'] loop
  hour:=(ctx->>k)::numeric;
  if hour>=0 and hour<24 then candidate:=(p.target_date::timestamp+round(hour*60)*interval '1 minute') at time zone c.timezone;cutoff:=least(cutoff,candidate); end if;
 end loop;
 ref:=ctx->'timing_reference';
 if ref->>'source'='derived_weather_peak' and ref->>'city_key'=p.city_key and ref->>'timezone'=c.timezone
  and (ref->>'month')::int=extract(month from p.target_date) and (ref->>'computed_at')::timestamptz<=p.frozen_at then
  hour:=(ref->>'peak_hour_local')::numeric;
  if hour>=0 and hour<24 then candidate:=(p.target_date::timestamp+round(hour*60)*interval '1 minute') at time zone c.timezone;cutoff:=least(cutoff,candidate); end if;
 end if;
 if p.frozen_at<start_at then cutoff:=start_at; end if;
 select jsonb_agg(jsonb_build_object('band_id',bb.band_id,'band_label',bb.band_label,'condition_id',bb.condition_id,
  'token_yes',bb.token_yes,'token_no',bb.token_no,'band_lo',bb.band_lo,'band_hi',bb.band_hi,'open_low',bb.open_low,'open_high',bb.open_high) order by bb.band_id)
 into ladder from public.v_canonical_bands bb where bb.market_id=b.market_id;
 select rr.* into r from public.market_rule_receipts rr where rr.market_id=b.market_id and rr.received_at<=p.frozen_at order by rr.received_at desc,rr.receipt_id desc limit 1;
 if found then rule_text:=r.rules_text;rule_received:=r.received_at;receipt_id:=r.receipt_id;
 else select mm.rules_text,mm.rules_fetched_at into rule_text,rule_received from public.markets mm where mm.market_id=b.market_id and mm.rules_fetched_at<=p.frozen_at;end if;
 if p.frozen_at is null or p.inputs_ok is distinct from true or p.frozen_at>stamp or p.decided_at>p.frozen_at then reason:='unverified_freeze';
 elsif p.checkpoint not in ('day_ahead','d1_eve','morning','noon','prepeak_2h','prepeak_1h') then reason:='ineligible_checkpoint';
 elsif scheduled is null or p.decided_at<scheduled or p.frozen_at>scheduled+interval '75 minutes' then reason:='late_checkpoint_capture';
 elsif coalesce(p.input_as_of,p.forecast_issued_at) is null or coalesce(p.input_as_of,p.forecast_issued_at)>p.frozen_at
  or p.forecast_issued_at>p.frozen_at or (ctx#>>'{forecast_input,run_at}')::timestamptz>p.frozen_at
  or (ctx#>>'{forecast_input,station_combination,inputs_newest_run_at}')::timestamptz>p.frozen_at then reason:='future_or_missing_input';
 elsif rule_received is null or not arbdesk_private.original_side_rule_identity(rule_text,p.station,m.unit,p.target_date,c.timezone) then reason:='unverified_original_rule_station_receipt';
 elsif ctx->>'rules_sha256' is not null and ctx->>'rules_sha256'<>encode(sha256(convert_to(rule_text,'UTF8')),'hex') then reason:='mismatched_original_rule';
 elsif (select count(*) from jsonb_object_keys(p.probs))<>jsonb_array_length(ladder) or exists(select 1 from jsonb_array_elements(ladder) l where not p.probs ? (l->>'band_id')) then reason:='changed_original_ladder';
 elsif ctx ? 'band_ids' and exists(select 1 from unnest(string_to_array(ctx->>'band_ids',';')) with ordinality x(id,n)
  left join public.v_canonical_bands bb on bb.band_id::text=x.id
  where bb.market_id is distinct from b.market_id
   or (ctx ? 'bands_lo' and nullif(split_part(ctx->>'bands_lo',';',x.n::integer),'')::numeric is distinct from bb.band_lo)
   or (ctx ? 'bands_hi' and nullif(split_part(ctx->>'bands_hi',';',x.n::integer),'')::numeric is distinct from bb.band_hi)) then reason:='changed_original_ladder';
 elsif exists(select 1 from jsonb_each_text(p.probs) v where not coalesce(v.value::numeric between 0 and 1,false))
   or (select sum(value::numeric) from jsonb_each_text(p.probs)) not between .999 and 1.001
   or (p.probs->>p.top_band_id)::numeric is distinct from (select max(value::numeric) from jsonb_each_text(p.probs)) then reason:='invalid_original_probability_ladder';
 elsif cutoff is null or p.frozen_at>=cutoff then reason:='at_or_after_saved_peak';
 elsif stamp>=cutoff or p.frozen_at<stamp-interval '90 minutes' then reason:='entry_window_expired';
 elsif m.closed is distinct from false then reason:='market_not_open'; end if;
 return jsonb_build_object('call_id',p.checkpoint_id,'city_key',p.city_key,'target_date',p.target_date,'checkpoint',p.checkpoint,
  'source_kind',source_kind,'frozen_at',p.frozen_at,'cutoff_at',cutoff,'eligibility',reason,
  'source',jsonb_build_object('checkpoint_id',p.checkpoint_id,'city_key',p.city_key,'target_date',p.target_date,'checkpoint',p.checkpoint,
   'decided_at',p.decided_at,'local_decision_time',p.local_decision_time,'engine_version',p.engine_version,'priced_from',p.priced_from,'model_path',p.model_path,
   'probs',p.probs,'frozen_at',p.frozen_at,'input_as_of',coalesce(p.input_as_of,p.forecast_issued_at),'input_sha256',p.input_sha256,
   'full_source_sha256',encode(sha256(convert_to(to_jsonb(p)::text,'UTF8')),'hex'),
   'predictive_context',jsonb_build_object('source_computed_at',ctx->'source_computed_at')),
  'identity',jsonb_build_object('market_id',b.market_id,'predicted_band_id',p.top_band_id,'predicted_band_label',b.band_label,
   'event_slug',m.event_slug,'condition_id',b.condition_id,'token_yes',b.token_yes,'token_no',b.token_no,'station',p.station,'timezone',c.timezone,
   'rule_receipt_id',receipt_id,'rules_received_at',rule_received,
   'rules_sha256',encode(sha256(convert_to(rule_text,'UTF8')),'hex'),
   'ladder_sha256',encode(sha256(convert_to(ladder::text,'UTF8')),'hex'),'unit',m.unit));
end $$;

create or replace function arbdesk_private.original_side_owner_valid(p_owner uuid) returns boolean
language sql set search_path='' as $$
 select p_owner is not null and (exists(select 1 from auth.users u where u.id=p_owner)
  or exists(select 1 from arbdesk_private.original_side_owners o where o.owner_id=p_owner))
$$;

-- A connected service operator may provision a short-lived, opaque owner
-- session. Only its SHA is retained. This avoids publishing ledgers through
-- the legacy anonymous route or requiring a new signup in single-user mode.
create or replace function public.issue_original_side_session(p_owner uuid default null) returns jsonb
language plpgsql security definer set search_path='' as $$
declare owner_key uuid; token text; until_at timestamptz:=clock_timestamp()+interval '7 days';
begin
 perform pg_advisory_xact_lock(hashtextextended('original-side-owner',0));
 if p_owner is null then
  select owner_id into owner_key from arbdesk_private.original_side_owners order by created_at limit 1;
  if not found then insert into arbdesk_private.original_side_owners default values returning owner_id into owner_key;end if;
 else
  if not arbdesk_private.original_side_owner_valid(p_owner) then raise exception 'authorized_owner_required';end if;
  insert into arbdesk_private.original_side_owners(owner_id) values(p_owner) on conflict do nothing;owner_key:=p_owner;
 end if;
 token:=replace(gen_random_uuid()::text,'-','')||replace(gen_random_uuid()::text,'-','');
 insert into arbdesk_private.original_side_sessions(token_sha256,owner_id,expires_at)
  values(encode(sha256(convert_to(token,'UTF8')),'hex'),owner_key,until_at);
 return jsonb_build_object('token',token,'owner_id',owner_key,'expires_at',until_at);
end $$;

create or replace function public.verify_original_side_session(p_token text) returns uuid
language sql security definer set search_path='' as $$
 select owner_id from arbdesk_private.original_side_sessions
 where length(p_token)=64 and token_sha256=encode(sha256(convert_to(p_token,'UTF8')),'hex') and expires_at>clock_timestamp()
$$;

create or replace function public.save_original_side_policy(p_owner uuid,p_config jsonb) returns uuid
language plpgsql security definer set search_path='' as $$
declare id uuid; slot jsonb; n integer;
begin
 if not arbdesk_private.original_side_owner_valid(p_owner) then raise exception 'owner_required'; end if;
 n:=jsonb_array_length(p_config->'slots');
 if n is null or n<1 or n>50 or p_config->>'version' is distinct from 'original-bucket-sides-v2.1'
  or p_config->>'funding' not in ('fixed','settled_equal_compound')
  or (p_config->>'starting_cash')::numeric is distinct from 100::numeric
  or (p_config->>'fixed_stake')::numeric is null or (p_config->>'fixed_stake')::numeric<=0 or (p_config->>'fixed_stake')::numeric>100
  or coalesce((p_config->>'derived_no_execution')::boolean,false) then raise exception 'invalid_paper_policy';end if;
 if (select count(distinct x->>'city_key') from jsonb_array_elements(p_config->'slots') x)<>n then raise exception 'one_side_per_city_per_account';end if;
 for slot in select * from jsonb_array_elements(p_config->'slots') loop
  if slot->>'side' not in ('YES','NO') or slot->>'checkpoint' not in ('day_ahead','d1_eve','morning','noon','prepeak_2h','prepeak_1h')
   or not exists(select 1 from public.cities where city_key=slot->>'city_key' and status='active' and timezone is not null) then raise exception 'invalid_city_side_or_checkpoint';end if;
 end loop;
 insert into arbdesk_private.original_side_policies(owner_id,config) values(p_owner,p_config) returning policy_id into id;
 return id; -- Saving is inactive. No account, contribution, order or schedule is created.
end $$;

create or replace function public.set_original_side_policy_state(p_owner uuid,p_policy uuid,p_action text) returns jsonb
language plpgsql security definer set search_path='' as $$
declare p arbdesk_private.original_side_policies; a arbdesk_private.original_side_accounts; last_action text;
begin
 select * into p from arbdesk_private.original_side_policies where policy_id=p_policy and owner_id=p_owner for update;
 if not found or p_action not in ('activate','pause','resume') then raise exception 'policy_owner_or_action_invalid';end if;
 select * into a from arbdesk_private.original_side_accounts where policy_id=p_policy for update;
 select action into last_action from arbdesk_private.original_side_policy_events where policy_id=p_policy order by event_id desc limit 1;
 if p_action='activate' then
  if a.account_id is not null then return jsonb_build_object('account_id',a.account_id,'reason','already_activated_no_topup');end if;
  insert into arbdesk_private.original_side_accounts(owner_id,policy_id,starting_cash,cash)
   values(p_owner,p_policy,100,100) returning * into a;
  insert into arbdesk_private.original_side_ledger(account_id,event_key,event_type,cash_delta,reserved_delta,detail)
   values(a.account_id,'fund:'||a.account_id,'initial_paper_contribution',100,0,jsonb_build_object('paper_only',true,'policy_id',p_policy));
 elsif a.account_id is null then raise exception 'activate_before_pause_or_resume';
 elsif last_action=p_action then return jsonb_build_object('account_id',a.account_id,'reason','already_'||p_action);end if;
 insert into arbdesk_private.original_side_policy_events(policy_id,action) values(p_policy,p_action);
 return jsonb_build_object('account_id',a.account_id,'action',p_action,'paper_only',true);
end $$;

create or replace function arbdesk_private.original_side_allocate(p_account uuid,p_target date)
returns arbdesk_private.original_side_allocation_plans language plpgsql set search_path='' as $$
declare a arbdesk_private.original_side_accounts;p arbdesk_private.original_side_policies;ap arbdesk_private.original_side_allocation_plans;
 free_cash numeric;per_slot numeric;total numeric;n integer;x jsonb;
begin
 select * into a from arbdesk_private.original_side_accounts where account_id=p_account for update;
 select * into ap from arbdesk_private.original_side_allocation_plans where account_id=p_account and target_date=p_target;
 if found then return ap;end if;
 select * into p from arbdesk_private.original_side_policies where policy_id=a.policy_id;
 free_cash:=greatest(0,a.cash-a.reserved_cash);n:=jsonb_array_length(p.config->'slots');
 per_slot:=case when p.config->>'funding'='fixed' then (p.config->>'fixed_stake')::numeric else floor(free_cash/n*100)/100 end;total:=per_slot*n;
 if per_slot is null or per_slot<=0 or total>free_cash then raise exception 'insufficient_settled_cash_for_dated_allocation';end if;
 insert into arbdesk_private.original_side_allocation_plans(account_id,policy_id,target_date,available_settled_cash,allocation_per_slot,rounding_remainder)
  values(a.account_id,a.policy_id,p_target,free_cash,per_slot,free_cash-total) returning * into ap;
 for x in select * from jsonb_array_elements(p.config->'slots') loop
  insert into arbdesk_private.original_side_allocations(plan_id,city_key,checkpoint,timezone,side,reserved_amount)
   values(ap.plan_id,x->>'city_key',x->>'checkpoint',(select timezone from public.cities where city_key=x->>'city_key'),x->>'side',per_slot);
 end loop;
 insert into arbdesk_private.original_side_ledger(account_id,event_key,event_type,cash_delta,reserved_delta,detail)
  values(a.account_id,'reserve:'||ap.plan_id,'dated_equal_allocation',0,total,jsonb_build_object('plan_id',ap.plan_id,'per_slot',per_slot::text,'remainder',(free_cash-total)::text));
 update arbdesk_private.original_side_accounts set reserved_cash=reserved_cash+total where account_id=a.account_id;
 return ap;
end $$;

create or replace function public.reserve_original_side_date(p_account uuid,p_call uuid) returns uuid
language plpgsql security definer set search_path='' as $$
declare a arbdesk_private.original_side_accounts;c arbdesk_private.original_side_calls;p arbdesk_private.original_side_policies;ap arbdesk_private.original_side_allocation_plans;checked jsonb;
begin
 select * into a from arbdesk_private.original_side_accounts where account_id=p_account for update;
 select * into c from arbdesk_private.original_side_calls where call_id=p_call;
 select * into p from arbdesk_private.original_side_policies where policy_id=a.policy_id;
 if c.source_kind is distinct from 'prediction_checkpoints' then raise exception 'v2_requires_native_named_checkpoint';end if;
 if (select e.action from arbdesk_private.original_side_policy_events e where e.policy_id=p.policy_id order by event_id desc limit 1) not in ('activate','resume')
  or c.frozen_at<a.activated_at or c.eligibility<>'eligible' or not exists(select 1 from jsonb_array_elements(p.config->'slots') s where s->>'city_key'=c.city_key and s->>'checkpoint'=c.checkpoint)
  or coalesce((select (value->>'halted')::boolean from public.settings where key='trading_halt'),false) then raise exception 'prospective_allocation_unavailable';end if;
 checked:=arbdesk_private.original_side_source(p_call);
 if checked->>'eligibility' is distinct from 'eligible' or checked->'identity' is distinct from c.identity then raise exception 'original_call_not_currently_eligible';end if;
 ap:=arbdesk_private.original_side_allocate(p_account,c.target_date);return ap.plan_id;
end $$;

create or replace function public.publish_original_side_order(p_account uuid,p_call uuid,p_quote uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare a arbdesk_private.original_side_accounts; p arbdesk_private.original_side_policies; c arbdesk_private.original_side_calls;
 q arbdesk_private.original_side_quotes; book arbdesk_private.original_side_books;
 ap arbdesk_private.original_side_allocation_plans; slot arbdesk_private.original_side_allocations;
 selected jsonb; x jsonb; checked jsonb; calc jsonb; free_cash numeric; per_slot numeric; total numeric; n int;
 dec_id uuid; ord_id uuid; k uuid; probability numeric; state text; stamp timestamptz:=clock_timestamp();
begin
 select * into a from arbdesk_private.original_side_accounts where account_id=p_account for update;
 if not found then raise exception 'private_paper_account_unavailable';end if;
 select order_id into ord_id from arbdesk_private.original_side_orders where account_id=p_account and call_id=p_call;
 if found then return jsonb_build_object('order_id',ord_id,'duplicate',true);end if;
 select * into p from arbdesk_private.original_side_policies where policy_id=a.policy_id;
 select action into state from arbdesk_private.original_side_policy_events where policy_id=a.policy_id order by event_id desc limit 1;
 if state not in ('activate','resume') then raise exception 'prospective_paper_policy_inactive';end if;
 if coalesce((select (value->>'halted')::boolean from public.settings where key='trading_halt'),false) then raise exception 'trading_halted';end if;
 select * into c from arbdesk_private.original_side_calls where call_id=p_call;
 if c.source_kind is distinct from 'prediction_checkpoints' then raise exception 'v2_requires_native_named_checkpoint';end if;
 select * into q from arbdesk_private.original_side_quotes where attempt_id=p_quote and call_id=p_call;
 select choice into selected from jsonb_array_elements(p.config->'slots') choice where choice->>'city_key'=c.city_key;
 if selected is null or selected->>'checkpoint' is distinct from c.checkpoint or selected->>'side' is distinct from q.side
  or c.frozen_at<a.activated_at or c.eligibility<>'eligible' then raise exception 'call_before_activation_or_outside_frozen_policy';end if;
 checked:=arbdesk_private.original_side_source(p_call);
 if checked->>'eligibility' is distinct from 'eligible' or checked->'identity' is distinct from c.identity or checked->'source' is distinct from c.source then raise exception 'original_call_identity_or_timing_changed';end if;
 if q.status is distinct from 'captured' or q.quote_kind is distinct from 'direct' or q.requested_at<c.frozen_at
  or q.received_at>stamp or q.received_at<stamp-interval '120 seconds' then raise exception 'direct_side_quote_required';end if;
 select * into book from arbdesk_private.original_side_books where snapshot_id=q.snapshot_id;
 if book.observed_at<stamp-interval '120 seconds' or book.observed_at>q.received_at then raise exception 'stale_side_book';end if;
 ap:=arbdesk_private.original_side_allocate(a.account_id,c.target_date);
 select * into slot from arbdesk_private.original_side_allocations where plan_id=ap.plan_id and city_key=c.city_key;
 if slot.side is distinct from q.side or slot.checkpoint is distinct from c.checkpoint then raise exception 'allocation_identity_mismatch';end if;
 select order_id into ord_id from arbdesk_private.original_side_orders where allocation_id=slot.allocation_id;
 if found then return jsonb_build_object('order_id',ord_id,'duplicate',true);end if;
 if exists(select 1 from arbdesk_private.original_side_ledger where event_key='release:'||slot.allocation_id) then raise exception 'allocation_already_released';end if;
 calc:=arbdesk_private.original_side_calculate(book.payload,slot.reserved_amount);
 insert into arbdesk_private.original_side_evaluations(quote_id,calculation_version,stake,result)
  values(p_quote,'original-bucket-sides-v2.1',slot.reserved_amount,calc) on conflict do nothing;
 probability:=(c.source->'probs'->>(c.identity->>'predicted_band_id'))::numeric;
 insert into arbdesk_private.original_side_decisions(allocation_id,account_id,policy_id,call_id,side,quote_id,model_prob_yes,selected_side_probability,snapshot)
  values(slot.allocation_id,a.account_id,a.policy_id,c.call_id,q.side,p_quote,probability,
   case when q.side='YES' then probability else 1-probability end,
   jsonb_build_object('source_call_id',c.call_id,'source_kind',c.source_kind,'original_band_id',c.identity->>'predicted_band_id',
    'source_sha256',c.source->>'input_sha256','model_version',c.source->>'engine_version','quote_id',p_quote,
    'policy_id',a.policy_id,'allocation_id',slot.allocation_id,'hold_to_settlement',true,'calculation',calc)) returning decision_id into dec_id;
 k:=md5('original-side:v2:'||a.policy_id||':'||a.account_id||':'||c.call_id||':'||q.side||':BUY')::uuid;
 insert into arbdesk_private.original_side_orders(command_key,account_id,decision_id,allocation_id,call_id,band_id,token_id,side,shares,limit_price,cash_ceiling,share_step,expires_at)
 values(k,a.account_id,dec_id,slot.allocation_id,c.call_id,(c.identity->>'predicted_band_id')::uuid,
  c.identity->>case when q.side='YES' then 'token_yes' else 'token_no' end,q.side,(calc->>'shares')::numeric,
  (calc->>'limit_price')::numeric,slot.reserved_amount,(book.payload->>'share_step')::numeric,least(stamp+interval '5 minutes',c.cutoff_at)) returning order_id into ord_id;
 return jsonb_build_object('order_id',ord_id,'decision_id',dec_id,'allocation_id',slot.allocation_id,'paper_only',true);
end $$;

create or replace function public.record_original_side_stage(p_call uuid,p_account uuid,p_stage text,p_reason text) returns void
language plpgsql security definer set search_path='' as $$ begin
 if length(p_stage)>80 or length(p_reason)>300 then raise exception 'stage_receipt_too_large';end if;
 insert into arbdesk_private.original_side_stage_events(call_id,account_id,stage,reason) values(p_call,p_account,p_stage,p_reason);
end $$;

create or replace function public.claim_original_side_order() returns jsonb
language plpgsql security definer set search_path='' as $$
declare a arbdesk_private.original_side_accounts; o arbdesk_private.original_side_orders;
begin
 select * into a from arbdesk_private.original_side_accounts x where exists(select 1 from arbdesk_private.original_side_orders q where q.account_id=x.account_id and
  (q.status='queued' or (q.status='working' and q.lease_until<clock_timestamp())))
  and not exists(select 1 from arbdesk_private.original_side_orders w where w.account_id=x.account_id and w.status='working' and w.lease_until>=clock_timestamp())
  order by x.activated_at for update skip locked limit 1;
 if not found then return null;end if;
 select * into o from arbdesk_private.original_side_orders where account_id=a.account_id and
  (status='queued' or (status='working' and lease_until<clock_timestamp())) order by requested_at for update skip locked limit 1;
 if not found then return null;end if;
 update arbdesk_private.original_side_orders set status='working',lease_token=gen_random_uuid(),lease_until=clock_timestamp()+interval '3 minutes' where order_id=o.order_id returning * into o;
 return to_jsonb(o)||jsonb_build_object('original_identity',(select identity from arbdesk_private.original_side_calls where call_id=o.call_id));
end $$;

create or replace function public.record_original_side_execution_book(p_order uuid,p_book jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare o arbdesk_private.original_side_orders; attempt uuid:=gen_random_uuid(); receipt jsonb; b arbdesk_private.original_side_books;
begin
 select * into o from arbdesk_private.original_side_orders where order_id=p_order and status='working';
 if not found then raise exception 'leased_private_order_required';end if;
 receipt:=public.record_original_side_quote(o.call_id,o.side,attempt,(p_book->>'requested_at')::timestamptz,p_book,null);
 select * into b from arbdesk_private.original_side_books where snapshot_id=receipt->>'snapshot_id';
 return jsonb_build_object('snapshot_id',b.snapshot_id,'capture_attempt_id',attempt,
  'received_at',receipt->>'received_at');
end $$;

create or replace function public.original_side_consumed_depth(p_order uuid,p_snapshot text) returns jsonb
language sql security definer set search_path='' as $$
 select coalesce(jsonb_object_agg(t.price,t.shares::text),'{}') from(
  select (f->>'price')::numeric::text price,sum((f->>'shares')::numeric) shares
  from arbdesk_private.original_side_orders current_order
  join arbdesk_private.original_side_books current_book on current_book.snapshot_id=p_snapshot
  join arbdesk_private.original_side_orders previous on previous.account_id=current_order.account_id and previous.token_id=current_order.token_id and previous.order_id<>current_order.order_id
  join arbdesk_private.original_side_books previous_book on previous_book.snapshot_id=previous.result->>'snapshot_id' and previous_book.depth_hash=current_book.depth_hash,
  lateral jsonb_array_elements(previous.result->'fills') f where current_order.order_id=p_order group by 1
 ) t
$$;

create or replace function public.complete_original_side_order(p_order uuid,p_lease uuid,p_result jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare o arbdesk_private.original_side_orders; a arbdesk_private.original_side_accounts; b arbdesk_private.original_side_books;
 c arbdesk_private.original_side_calls; al arbdesk_private.original_side_allocations; q numeric; cost numeric; fee numeric; f jsonb; used jsonb;
 available numeric; filled numeric; check_calc jsonb; execution_quote arbdesk_private.original_side_quotes;
 now_at timestamptz:=clock_timestamp();
begin
 select * into o from arbdesk_private.original_side_orders where order_id=p_order;
 select * into a from arbdesk_private.original_side_accounts where account_id=o.account_id for update;
 select * into o from arbdesk_private.original_side_orders where order_id=p_order for update;
 if not found then raise exception 'private_order_unavailable';end if;
 if o.status in ('filled','partial','rejected','expired') then return o.result;end if;
 if o.status<>'working' or p_lease is null or o.lease_token is distinct from p_lease or o.lease_until<now_at then raise exception 'private_order_lease_lost';end if;
 select * into c from arbdesk_private.original_side_calls where call_id=o.call_id;
 select * into al from arbdesk_private.original_side_allocations where allocation_id=o.allocation_id;
 q:=(p_result->>'shares')::numeric; cost:=(p_result->>'notional')::numeric; fee:=(p_result->>'fee')::numeric;
 if q is null or cost is null or fee is null or q<0 or q>o.shares or cost<0 or fee<0
  or q::text in ('NaN','Infinity','-Infinity') or cost::text in ('NaN','Infinity','-Infinity') or fee::text in ('NaN','Infinity','-Infinity')
  or p_result->>'status' not in ('filled','partial','rejected','expired') or p_result->>'status' is null then raise exception 'invalid_private_fill_amounts';end if;
 if q>0 then
  if now_at>=o.expires_at or now_at>=c.cutoff_at or p_result->>'status' not in ('filled','partial')
   or (p_result->>'status'='filled')<>(q=o.shares) or mod(q,o.share_step)<>0 then raise exception 'invalid_fill_timing_or_quantity';end if;
  select * into b from arbdesk_private.original_side_books where snapshot_id=p_result->>'snapshot_id';
  select * into execution_quote from arbdesk_private.original_side_quotes
   where attempt_id=(p_result->>'capture_attempt_id')::uuid and call_id=o.call_id and side=o.side
    and snapshot_id=b.snapshot_id and status='captured' and quote_kind='direct';
  if b.snapshot_id is null or not found or b.token_id<>o.token_id or b.observed_at>now_at or b.observed_at<now_at-interval '120 seconds'
   or execution_quote.requested_at<o.requested_at or execution_quote.received_at<o.requested_at
   or execution_quote.received_at>now_at or p_result->>'simulated_at' is null
   or nullif(p_result->>'engine_version','') is null
   or (p_result->>'simulated_at')::timestamptz<execution_quote.received_at
   or (p_result->>'simulated_at')::timestamptz>now_at then raise exception 'missing_fresh_direct_execution_evidence';end if;
  if jsonb_typeof(p_result->'fills') is distinct from 'array' then raise exception 'fill_array_required';end if;
  used:=public.original_side_consumed_depth(o.order_id,b.snapshot_id);
  for f in select * from jsonb_array_elements(p_result->'fills') loop
   if not coalesce((f->>'shares')::numeric>0 and mod((f->>'shares')::numeric,o.share_step)=0
    and (f->>'price')::numeric>0 and (f->>'price')::numeric<=o.limit_price
    and mod((f->>'price')::numeric,(b.payload->>'tick_size')::numeric)=0
    and (f->>'notional')::numeric=(f->>'shares')::numeric*(f->>'price')::numeric
    and (f->>'fee')::numeric=round((f->>'shares')::numeric*(b.payload->>'fee_rate')::numeric*(f->>'price')::numeric*(1-(f->>'price')::numeric),5),false) then raise exception 'invalid_private_fill_arithmetic_or_fee';end if;
  end loop;
  for f in select jsonb_build_object('price',(x->>'price')::numeric::text,'shares',sum((x->>'shares')::numeric)::text) from jsonb_array_elements(p_result->'fills') x group by (x->>'price')::numeric loop
   select coalesce(sum((x->>'size')::numeric),0) into available from jsonb_array_elements(coalesce(b.payload->'asks',b.payload#>'{raw_book,asks}')) x where (x->>'price')::numeric=(f->>'price')::numeric;
   if (f->>'shares')::numeric+coalesce((used->>(f->>'price'))::numeric,0)>available then raise exception 'private_book_depth_exceeded';end if;
  end loop;
  if (select sum((x->>'shares')::numeric) from jsonb_array_elements(p_result->'fills') x) is distinct from q
   or (select sum((x->>'notional')::numeric) from jsonb_array_elements(p_result->'fills') x) is distinct from cost
   or (select sum((x->>'fee')::numeric) from jsonb_array_elements(p_result->'fills') x) is distinct from fee
   or cost+fee>o.cash_ceiling or cost+fee>al.reserved_amount or cost+fee>a.cash then raise exception 'private_fill_totals_or_cash_ceiling';end if;
 else
  if cost<>0 or fee<>0 or p_result->>'status' in ('filled','partial') or coalesce(jsonb_array_length(p_result->'fills'),0)<>0 then raise exception 'zero_fill_has_economic_result';end if;
 end if;
 insert into arbdesk_private.original_side_execution_events(order_id,result) values(p_order,p_result);
 insert into arbdesk_private.original_side_ledger(account_id,event_key,event_type,call_id,order_id,cash_delta,reserved_delta,detail)
  values(a.account_id,'fill:'||p_order,'paper_execution',o.call_id,p_order,-cost-fee,-al.reserved_amount,
   jsonb_build_object('notional',cost::text,'fee',fee::text,'unspent_allocation',(al.reserved_amount-cost-fee)::text,'shares',q::text));
 update arbdesk_private.original_side_accounts set cash=cash-cost-fee,reserved_cash=reserved_cash-al.reserved_amount where account_id=a.account_id;
 update arbdesk_private.original_side_orders set status=p_result->>'status',result=p_result,lease_until=null where order_id=p_order;
 return p_result;
end $$;

create or replace function public.expire_original_side_allocations() returns integer
language plpgsql security definer set search_path='' as $$
declare a arbdesk_private.original_side_accounts; slot record; n int:=0;
begin
 for a in select * from arbdesk_private.original_side_accounts order by account_id for update skip locked loop
  for slot in select al.*,ap.target_date from arbdesk_private.original_side_allocations al
   join arbdesk_private.original_side_allocation_plans ap using(plan_id)
   where ap.account_id=a.account_id and clock_timestamp()>=((ap.target_date+1)::timestamp at time zone al.timezone)
    and not exists(select 1 from arbdesk_private.original_side_orders o where o.allocation_id=al.allocation_id)
    and not exists(select 1 from arbdesk_private.original_side_ledger l where l.event_key='release:'||al.allocation_id) loop
   insert into arbdesk_private.original_side_ledger(account_id,event_key,event_type,cash_delta,reserved_delta,detail)
    values(a.account_id,'release:'||slot.allocation_id,'unused_slot_expired',0,-slot.reserved_amount,jsonb_build_object('allocation_id',slot.allocation_id,'reason','city_local_day_ended_without_execution'));
   update arbdesk_private.original_side_accounts set reserved_cash=reserved_cash-slot.reserved_amount where account_id=a.account_id;
   n:=n+1;
  end loop;
 end loop;
 return n;
end $$;

create or replace function public.register_original_side_call(p_call uuid) returns jsonb
language plpgsql security definer set search_path='' as $$
declare packet jsonb; existing arbdesk_private.original_side_calls;
begin
 select * into existing from arbdesk_private.original_side_calls where call_id=p_call;
 if found then return to_jsonb(existing); end if;
 if not (arbdesk_private.original_side_capacity()->>'capture_allowed')::boolean then raise exception 'private_evidence_capacity_hold';end if;
 packet:=arbdesk_private.original_side_source(p_call);
 insert into arbdesk_private.original_side_rule_receipts(rules_sha256,rules_text,received_at,source_url)
  select packet#>>'{identity,rules_sha256}',r.rules_text,r.received_at,
   'https://gamma-api.polymarket.com/events/slug/'||(packet#>>'{identity,event_slug}')
  from (select rules_text,received_at from public.market_rule_receipts where receipt_id=(packet#>>'{identity,rule_receipt_id}')::bigint
   union all select rules_text,rules_fetched_at from public.markets where market_id=(packet#>>'{identity,market_id}')::uuid
    and packet#>>'{identity,rule_receipt_id}' is null) r
  where packet#>>'{identity,rules_sha256}' is not null and r.rules_text is not null and r.received_at is not null on conflict do nothing;
 insert into arbdesk_private.original_side_ladder_receipts(ladder_sha256,ladder)
  select packet#>>'{identity,ladder_sha256}',jsonb_agg(jsonb_build_object('band_id',bb.band_id,'band_label',bb.band_label,'condition_id',bb.condition_id,
   'token_yes',bb.token_yes,'token_no',bb.token_no,'band_lo',bb.band_lo,'band_hi',bb.band_hi,'open_low',bb.open_low,'open_high',bb.open_high) order by bb.band_id)
  from public.v_canonical_bands bb where bb.market_id=(packet#>>'{identity,market_id}')::uuid on conflict do nothing;
 insert into arbdesk_private.original_side_calls(call_id,city_key,target_date,checkpoint,source_kind,frozen_at,source,identity,eligibility,cutoff_at)
 values(p_call,packet->>'city_key',(packet->>'target_date')::date,packet->>'checkpoint',packet->>'source_kind',
  (packet->>'frozen_at')::timestamptz,packet->'source',packet->'identity',packet->>'eligibility',(packet->>'cutoff_at')::timestamptz)
 on conflict do nothing;
 select * into existing from arbdesk_private.original_side_calls where call_id=p_call;
 return to_jsonb(existing);
end $$;

-- Freeze metadata in the same transaction as the actual source insert. A
-- failed observation stage must never abort the original forecast freeze.
create or replace function arbdesk_private.original_side_freeze_receipt() returns trigger language plpgsql set search_path='' as $$
begin
 perform public.register_original_side_call((to_jsonb(new)->>case when tg_table_name='prediction_checkpoints' then 'checkpoint_id' else 'call_id' end)::uuid);
 return new;
exception when others then
 begin insert into arbdesk_private.original_side_stage_events(call_id,stage,reason)
 values((to_jsonb(new)->>case when tg_table_name='prediction_checkpoints' then 'checkpoint_id' else 'call_id' end)::uuid,'freeze_registration',left(sqlerrm,300));
 exception when others then null;end;
 return new;
end $$;
create or replace trigger original_side_freeze_receipt after insert on public.prediction_checkpoints
 for each row execute function arbdesk_private.original_side_freeze_receipt();
create or replace trigger original_side_freeze_receipt after insert on public.day_max_day_ahead_calls
 for each row execute function arbdesk_private.original_side_freeze_receipt();

create or replace function public.record_original_side_quote(p_call uuid,p_side text,p_attempt uuid,p_requested timestamptz,p_book jsonb default null,p_reason text default null)
returns jsonb language plpgsql security definer set search_path='' as $$
declare c arbdesk_private.original_side_calls; stamp timestamptz:=clock_timestamp(); snap text; token text;
 payload jsonb; meta jsonb; raw jsonb; mapped text; status text:='failed'; reason text:=p_reason; e jsonb;
begin
 if pg_column_size(p_book)>524288 or length(p_reason)>500 then raise exception 'bounded_quote_receipt_required';end if;
 select * into c from arbdesk_private.original_side_calls where call_id=p_call;
 if not found or p_side not in ('YES','NO') or p_requested is null or p_requested>stamp or p_requested<c.frozen_at then raise exception 'invalid_original_quote_request'; end if;
 if exists(select 1 from arbdesk_private.original_side_quotes where attempt_id=p_attempt and (call_id<>p_call or side<>p_side)) then raise exception 'quote_attempt_identity_mismatch'; end if;
 if exists(select 1 from arbdesk_private.original_side_quotes where attempt_id=p_attempt) then return jsonb_build_object('attempt_id',p_attempt,'duplicate',true); end if;
 token:=c.identity->>case when p_side='YES' then 'token_yes' else 'token_no' end;
 if p_book is not null then
  if not (arbdesk_private.original_side_capacity()->>'capture_allowed')::boolean
   and not exists(select 1 from arbdesk_private.original_side_orders o where o.call_id=p_call and o.side=p_side and o.status='working') then raise exception 'private_evidence_capacity_hold';end if;
  if c.eligibility<>'eligible' or stamp>=c.cutoff_at or p_requested<c.registered_at-interval '1 second' or stamp>c.frozen_at+interval '90 minutes' then raise exception 'quote_outside_original_entry_window'; end if;
  raw:=p_book->'raw_book';meta:=p_book->'market_metadata';
  select x.token into mapped from jsonb_array_elements_text(case when jsonb_typeof(meta->'clobTokenIds')='string' then (meta->>'clobTokenIds')::jsonb else meta->'clobTokenIds' end) with ordinality x(token,n)
   join jsonb_array_elements_text(case when jsonb_typeof(meta->'outcomes')='string' then (meta->>'outcomes')::jsonb else meta->'outcomes' end) with ordinality y(label,n) using(n) where upper(y.label)=p_side;
  if p_book->>'quote_kind' is distinct from 'direct' or p_book->>'token_id' is distinct from token or raw->>'asset_id' is distinct from token
   or meta->>'conditionId' is distinct from c.identity->>'condition_id' or mapped is distinct from token then raise exception 'wrong_side_token_or_condition'; end if;
  if (p_book->>'observed_at')::timestamptz>stamp or (p_book->>'observed_at')::timestamptz<stamp-interval '120 seconds'
   or to_timestamp((raw->>'timestamp')::numeric/1000) is distinct from (p_book->>'observed_at')::timestamptz then raise exception 'stale_or_future_side_book'; end if;
  if (meta->>'active')::boolean is distinct from true or (meta->>'closed')::boolean is distinct from false
   or (meta->>'acceptingOrders')::boolean is distinct from true then raise exception 'closed_or_untradeable_market'; end if;
  if p_book->'asks' is distinct from raw->'asks' or p_book->'bids' is distinct from raw->'bids'
   or (p_book->>'min_order_size')::numeric is distinct from (raw->>'min_order_size')::numeric
   or (p_book->>'tick_size')::numeric is distinct from (raw->>'tick_size')::numeric then raise exception 'book_terms_not_direct'; end if;
  if encode(sha256(convert_to(p_book->>'current_rules_text','UTF8')),'hex') is distinct from c.identity->>'rules_sha256'
   or (p_book->>'current_rules_received_at')::timestamptz<c.frozen_at or (p_book->>'current_rules_received_at')::timestamptz>stamp
   or p_book->>'rules_source' is distinct from 'https://gamma-api.polymarket.com/events/slug/'||(c.identity->>'event_slug') then raise exception 'current_rule_does_not_match_original_receipt';end if;
  if not coalesce((((meta->>'feesEnabled')::boolean=false and (p_book->>'fee_rate')::numeric=0)
   or ((meta->>'feesEnabled')::boolean=true and (meta#>>'{feeSchedule,exponent}')::numeric=1
    and (p_book->>'fee_rate')::numeric=(meta#>>'{feeSchedule,rate}')::numeric)),false) then raise exception 'unavailable_fee_evidence'; end if;
  -- Raw depth is retained once. Rules are referenced by their immutable hash;
  -- each attempt independently retains its actual rule/HTTP receipt clocks.
  payload:=(p_book-array['current_rules_text','asks','bids'])||jsonb_build_object('received_at',stamp,'rules_sha256',c.identity->>'rules_sha256');
  -- Metadata has its own immutable receipt, while depth_hash prevents refreshes replenishing liquidity.
  snap:=encode(sha256(convert_to(jsonb_build_object('raw',raw,'meta',meta)::text,'UTF8')),'hex');
  payload:=payload||jsonb_build_object('snapshot_id',snap);
  insert into arbdesk_private.original_side_books(snapshot_id,depth_hash,token_id,observed_at,payload)
   values(snap,encode(sha256(convert_to(raw::text,'UTF8')),'hex'),token,(p_book->>'observed_at')::timestamptz,payload) on conflict do nothing;
  status:='captured';reason:=null;
 else status:=case when c.eligibility='eligible' then 'failed' else 'blocked' end;reason:=coalesce(reason,c.eligibility,'not_recorded');end if;
 insert into arbdesk_private.original_side_quotes(attempt_id,call_id,side,quote_kind,status,reason,requested_at,snapshot_id,receipt)
 values(p_attempt,p_call,p_side,case when snap is null then 'missing' else 'direct' end,status,reason,p_requested,snap,
  jsonb_build_object('rules_received_at',p_book->>'current_rules_received_at','rules_sha256',c.identity->>'rules_sha256',
   'book_observed_at',p_book->>'observed_at','book_age_seconds',extract(epoch from stamp-(p_book->>'observed_at')::timestamptz)));
 if snap is not null then
  begin e:=arbdesk_private.original_side_calculate(payload,10);
  exception when others then e:=jsonb_build_object('eligibility','blocked','reason',sqlerrm,'requested_stake','10','calculation_version','original-bucket-sides-v2.1');end;
  insert into arbdesk_private.original_side_evaluations(quote_id,calculation_version,stake,result) values(p_attempt,'original-bucket-sides-v2.1',10,e) on conflict do nothing;
 end if;
 return jsonb_build_object('attempt_id',p_attempt,'snapshot_id',snap,'status',status,'reason',reason,
  'received_at',stamp);
end $$;

create or replace function public.original_side_runtime(p_limit integer default 100) returns jsonb
language sql security definer set search_path='' as $$
 select jsonb_build_object('capacity',arbdesk_private.original_side_capacity(),'calls',coalesce((select jsonb_agg(to_jsonb(t)) from(
  select p.checkpoint_id,p.city_key,p.target_date,p.checkpoint,p.decided_at,p.frozen_at,
   coalesce((select jsonb_object_agg(s.side,s.n) from(select q.side,count(*) n,bool_or(q.status='captured') captured from arbdesk_private.original_side_quotes q where q.call_id=p.checkpoint_id group by q.side) s),'{}') attempts,
   coalesce((select jsonb_agg(side) from (values ('YES'),('NO')) sides(side)
    where arbdesk_private.original_side_observation_complete(p.checkpoint_id,side)),'[]') captured_sides
  from public.v_day_max_original_calls p where (arbdesk_private.original_side_capacity()->>'capture_allowed')::boolean and p.target_date between current_date-1 and current_date+2 and p.frozen_at>=clock_timestamp()-interval '90 minutes'
   and (p.predictive_context->>'source_kind'='day_max_day_ahead_calls' or not exists(select 1 from public.prediction_checkpoints x where x.city_key=p.city_key and x.target_date=p.target_date and x.checkpoint=p.checkpoint and (x.decided_at,x.checkpoint_id)<(p.decided_at,p.checkpoint_id)))
   and ((select count(*) from (values ('YES'),('NO')) sides(side)
     where arbdesk_private.original_side_observation_complete(p.checkpoint_id,side))<2
     or exists(select 1 from arbdesk_private.original_side_accounts a join arbdesk_private.original_side_policies policy using(policy_id)
      where (select e.action from arbdesk_private.original_side_policy_events e where e.policy_id=a.policy_id order by event_id desc limit 1) in ('activate','resume')
       and p.frozen_at>=a.activated_at and exists(select 1 from jsonb_array_elements(policy.config->'slots') choice where choice->>'city_key'=p.city_key and choice->>'checkpoint'=p.checkpoint)
       and not exists(select 1 from arbdesk_private.original_side_orders o where o.account_id=a.account_id and o.call_id=p.checkpoint_id)))
  order by p.frozen_at,p.checkpoint_id limit least(greatest(p_limit,1),200)
 ) t),'[]'),
 'historical_calls',coalesce((select jsonb_agg(to_jsonb(t)) from (
  select distinct e.checkpoint_id from public.day_max_prediction_entries e
  where e.target_date>=current_date-90 and (arbdesk_private.original_side_capacity()->>'capture_allowed')::boolean
   and not exists(select 1 from arbdesk_private.original_side_calls c where c.call_id=e.checkpoint_id)
  order by e.checkpoint_id limit 30) t),'[]'),
 'accounts',coalesce((select jsonb_agg(jsonb_build_object('account_id',a.account_id,'policy_id',a.policy_id,'config',p.config))
  from arbdesk_private.original_side_accounts a join arbdesk_private.original_side_policies p using(policy_id)
  where (select e.action from arbdesk_private.original_side_policy_events e where e.policy_id=p.policy_id order by event_id desc limit 1) in ('activate','resume')),'[]'))
$$;

create or replace function public.original_side_quote_for_order(p_call uuid,p_side text) returns uuid
language sql security definer set search_path='' as $$
 select attempt_id from arbdesk_private.original_side_quotes where call_id=p_call and side=p_side and status='captured'
  and received_at>=clock_timestamp()-interval '120 seconds' order by received_at desc,attempt_id limit 1
$$;

create or replace function public.original_side_settlement_candidates(p_limit integer default 12) returns jsonb
language sql security definer set search_path='' as $$
 select coalesce(jsonb_agg(to_jsonb(t)),'[]') from(
  select c.call_id,c.city_key,c.target_date,c.identity,
   (select max(e.recorded_at) from arbdesk_private.original_side_stage_events e where e.call_id=c.call_id and e.stage='settlement_attempt') last_attempt
  from arbdesk_private.original_side_calls c
  where clock_timestamp()>=((c.target_date+1)::timestamp at time zone (c.identity->>'timezone'))
   and (coalesce((select s.state from arbdesk_private.original_side_settlements s where s.call_id=c.call_id
     order by s.received_at desc,s.settlement_id desc limit 1),'pending') not like 'verified_%'
    or c.target_date>=current_date-7)
  order by last_attempt nulls first,c.target_date,c.call_id limit least(greatest(p_limit,1),50)
 ) t
$$;

create or replace function arbdesk_private.original_side_hex(v text) returns numeric
language plpgsql immutable set search_path='' as $$
declare n numeric:=0;i integer;digit integer;
begin
 if v is null or v !~ '^0x[0-9a-fA-F]{1,64}$' then raise exception 'invalid_ctf_hex';end if;
 for i in 3..length(v) loop digit:=position(lower(substr(v,i,1)) in '0123456789abcdef')-1;n:=n*16+digit;end loop;
 return n;
end $$;

create or replace function arbdesk_private.original_side_fractional_payout(identity jsonb,evidence jsonb) returns jsonb
language plpgsql set search_path='' as $$
declare proof jsonb:=evidence->'ctf_payout';provider jsonb;calls jsonb;block jsonb;previous jsonb;
 den numeric;yn numeric;nn numeric;i integer;side text;collection text;cond text:=lower(identity->>'condition_id');
 contract text:='0x4d97dcd97ec945f40cf65f87097ace5ea0476045';collateral text:='0x2791bca1f2de4661ed88a30c99a7a9449aa84174';
begin
 if pg_column_size(proof)>65536 or proof->>'protocol' is distinct from 'polygon-legacy-ctf-v1'
  or proof->>'ctf' is distinct from contract or proof->>'collateral' is distinct from collateral
  or jsonb_array_length(proof->'providers')<>2 or cond !~ '^0x[0-9a-f]{64}$' or proof->>'received_at' is null
  or (proof->>'received_at')::timestamptz<(evidence->>'requested_at')::timestamptz
  or (proof->>'received_at')::timestamptz>(evidence->>'response_received_at')::timestamptz then return null;end if;
 if (select count(distinct p->>'source') from jsonb_array_elements(proof->'providers') p)<>2 then return null;end if;
 for provider in select * from jsonb_array_elements(proof->'providers') loop
  if provider->>'source' not in ('https://polygon-bor-rpc.publicnode.com','https://polygon.drpc.org')
   or provider->>'chain_id' is distinct from '0x89' or provider->>'latest_block' is null
   or provider->>'latest_block' !~ '^[0-9]{1,20}$' or jsonb_array_length(provider->'calls')<>7 then return null;end if;
  block:=provider->'block';
  if block->>'hash' is null or block->>'hash' !~ '^0x[0-9a-fA-F]{64}$' or (provider->>'latest_block')::numeric-arbdesk_private.original_side_hex(block->>'number')<64
   or to_timestamp(arbdesk_private.original_side_hex(block->>'timestamp')::double precision)<((identity->>'target_date')::date+1)::timestamp at time zone (identity->>'timezone')
   or to_timestamp(arbdesk_private.original_side_hex(block->>'timestamp')::double precision)>(evidence->>'response_received_at')::timestamptz then return null;end if;
  if previous is not null and (previous->'block' is distinct from block or previous->'calls' is distinct from provider->'calls') then return null;end if;
  select jsonb_object_agg(x->>'key',x) into calls from jsonb_array_elements(provider->'calls') x;
  if (select count(*) from jsonb_object_keys(calls))<>7 or exists(select 1 from jsonb_array_elements(provider->'calls') x
   where x->>'to' is distinct from contract or x->>'result' !~ '^0x[0-9a-fA-F]{64}$') then return null;end if;
  if calls#>>'{denominator,data}' is distinct from '0xdd34de67'||substr(cond,3)
   or calls#>>'{numerator_0,data}' is distinct from '0x0504c814'||substr(cond,3)||repeat('0',64)
   or calls#>>'{numerator_1,data}' is distinct from '0x0504c814'||substr(cond,3)||repeat('0',63)||'1' then return null;end if;
  den:=arbdesk_private.original_side_hex(calls#>>'{denominator,result}');
  yn:=arbdesk_private.original_side_hex(calls#>>'{numerator_0,result}');nn:=arbdesk_private.original_side_hex(calls#>>'{numerator_1,result}');
  if den<=0 or yn+nn<>den then return null;end if;
  for i in 1..2 loop
   side:=case when i=1 then 'YES' else 'NO' end;
   if calls#>>array['collection_'||side,'data'] is distinct from '0x856296f7'||repeat('0',64)||substr(cond,3)||repeat('0',63)||i::text then return null;end if;
   collection:=calls#>>array['collection_'||side,'result'];
   if calls#>>array['position_'||side,'data'] is distinct from '0x39dd7530'||lpad(substr(collateral,3),64,'0')||substr(collection,3)
    or arbdesk_private.original_side_hex(calls#>>array['position_'||side,'result']) is distinct from (identity->>case when i=1 then 'token_yes' else 'token_no' end)::numeric then return null;end if;
  end loop;
  previous:=provider;
 end loop;
 return jsonb_build_object('payout_yes',yn/den,'payout_no',nn/den);
exception when others then return null;
end $$;

-- Both venues must identify the same original condition and tokens. A price,
-- closed flag, weather maximum or a YES miss alone cannot prove a NO payout.
create or replace function public.record_original_side_settlement(p_call uuid,p_evidence jsonb) returns jsonb
language plpgsql security definer set search_path='' as $$
declare c arbdesk_private.original_side_calls; g jsonb; cl jsonb; labels jsonb; tokens jsonb; prices jsonb; t jsonb;
 yp numeric; np numeric; token_yes text; token_no text; winner text; state text; proof text; id uuid;
 ord record; a arbdesk_private.original_side_accounts; previous_paid numeric; payout numeric; delta numeric;
 rule_hash text; old_state text;fractional jsonb;normalized jsonb;
begin
 select * into c from arbdesk_private.original_side_calls where call_id=p_call;
 if not found then raise exception 'original_call_unavailable';end if;
 perform pg_advisory_xact_lock(hashtextextended('original-side-settle:'||p_call,0));
 if clock_timestamp()<((c.target_date+1)::timestamp at time zone (c.identity->>'timezone')) then raise exception 'local_target_day_incomplete';end if;
 g:=p_evidence->'gamma';cl:=p_evidence->'clob';
 if p_evidence->>'requested_at' is null or p_evidence->>'response_received_at' is null
  or p_evidence->>'gamma_source' is distinct from 'https://gamma-api.polymarket.com/markets?condition_ids='||(c.identity->>'condition_id')||'&closed=true'
  or p_evidence->>'clob_source' is distinct from 'https://clob.polymarket.com/markets/'||(c.identity->>'condition_id')
  or p_evidence->>'rules_source' is distinct from 'https://gamma-api.polymarket.com/events/slug/'||(c.identity->>'event_slug') then raise exception 'settlement_receipt_provenance_required';end if;
 if g->>'conditionId' is distinct from c.identity->>'condition_id' or cl->>'condition_id' is distinct from c.identity->>'condition_id' then raise exception 'settlement_condition_mismatch';end if;
 tokens:=case when jsonb_typeof(g->'clobTokenIds')='string' then (g->>'clobTokenIds')::jsonb else g->'clobTokenIds' end;
 labels:=case when jsonb_typeof(g->'outcomes')='string' then (g->>'outcomes')::jsonb else g->'outcomes' end;
 prices:=case when jsonb_typeof(g->'outcomePrices')='string' then (g->>'outcomePrices')::jsonb else g->'outcomePrices' end;
 if jsonb_array_length(tokens)<>2 or jsonb_array_length(labels)<>2 or jsonb_array_length(prices)<>2 then raise exception 'binary_token_identity_required';end if;
 select x.token,(prices->>(x.n::int-1))::numeric into token_yes,yp from jsonb_array_elements_text(tokens) with ordinality x(token,n)
  join jsonb_array_elements_text(labels) with ordinality y(label,n) using(n) where upper(y.label)='YES';
 select x.token,(prices->>(x.n::int-1))::numeric into token_no,np from jsonb_array_elements_text(tokens) with ordinality x(token,n)
  join jsonb_array_elements_text(labels) with ordinality y(label,n) using(n) where upper(y.label)='NO';
 if token_yes is distinct from c.identity->>'token_yes' or token_no is distinct from c.identity->>'token_no'
  or jsonb_array_length(cl->'tokens')<>2 or not exists(select 1 from jsonb_array_elements(cl->'tokens') x where x->>'token_id'=token_yes)
  or not exists(select 1 from jsonb_array_elements(cl->'tokens') x where x->>'token_id'=token_no) then raise exception 'settlement_token_mapping_mismatch';end if;
 rule_hash:=encode(sha256(convert_to(p_evidence->>'rules_text','UTF8')),'hex');
 if rule_hash is null or rule_hash is distinct from c.identity->>'rules_sha256' then state:='unverified';yp:=null;np:=null;
 elsif g->>'umaResolutionStatus'='disputed' then state:='disputed';yp:=null;np:=null;
 elsif (g->>'closed')::boolean=true and g->>'umaResolutionStatus'='resolved'
   and (cl->>'closed')::boolean=true and (cl->>'accepting_orders')::boolean=false
   and yp in (0,1) and np in (0,1) and yp+np=1 then
  winner:=case when yp=1 then token_yes else token_no end;
  if (select count(*) from jsonb_array_elements(cl->'tokens') x where (x->>'winner')::boolean=true)<>1
   or not exists(select 1 from jsonb_array_elements(cl->'tokens') x where (x->>'winner')::boolean=true and x->>'token_id'=winner) then raise exception 'settlement_venues_disagree';end if;
  state:='verified_binary';
 elsif (g->>'closed')::boolean=true and g->>'umaResolutionStatus'='resolved'
  and (cl->>'closed')::boolean=true and (cl->>'accepting_orders')::boolean=false then
  fractional:=arbdesk_private.original_side_fractional_payout(c.identity||jsonb_build_object('target_date',c.target_date),p_evidence);
  if fractional is null then state:='pending';yp:=null;np:=null;
  else yp:=(fractional->>'payout_yes')::numeric;np:=(fractional->>'payout_no')::numeric;
   if yp not between 0 and 1 or np not between 0 and 1 or yp+np<>1 then raise exception 'invalid_verified_token_payout';end if;
   state:=case when coalesce((g->>'voided')::boolean,false) then 'verified_void' else 'verified_split' end;
  end if;
 else state:='pending';yp:=null;np:=null;end if;
 -- A split or refund is left unresolved until its actual payout adapter has
 -- verified a payout receipt. It is NEVER inferred as the inverse of a hit.
 normalized:=(p_evidence-'rules_text')||jsonb_build_object('rules_sha256',rule_hash);
 proof:=encode(sha256(convert_to((normalized-array['requested_at','response_received_at'])::text,'UTF8')),'hex');
 select settlement_id into id from arbdesk_private.original_side_settlements where call_id=p_call order by received_at desc,settlement_id desc limit 1;
 if found and (select ss.proof_hash from arbdesk_private.original_side_settlements ss where ss.settlement_id=id)=proof then return jsonb_build_object('settlement_id',id,'duplicate',true);end if;
 if exists(select 1 from arbdesk_private.original_side_settlements ss where ss.settlement_id=id
   and (ss.evidence->>'source_response_received_at')::timestamptz>(p_evidence->>'response_received_at')::timestamptz) then raise exception 'stale_settlement_revision';end if;
 if (p_evidence->>'requested_at')::timestamptz>(p_evidence->>'response_received_at')::timestamptz
  or (p_evidence->>'response_received_at')::timestamptz>clock_timestamp() then raise exception 'future_or_unordered_settlement_response';end if;
 insert into arbdesk_private.original_side_resolution_receipts(proof_hash,condition_id,evidence)
  values(proof,c.identity->>'condition_id',normalized) on conflict do nothing;
 insert into arbdesk_private.original_side_settlements(call_id,proof_hash,state,payout_yes,payout_no,evidence)
 values(p_call,proof,state,yp,np,jsonb_build_object('receipt_sha256',proof,'source_response_received_at',p_evidence->>'response_received_at',
  'reason',case when state='pending' then 'actual_token_payout_not_verified_including_split_or_void' when state='unverified' then 'original_rule_not_verified' else null end)) returning settlement_id into id;
 if state in ('verified_binary','verified_split','verified_void','disputed','unverified','pending') then
  for ord in select o.* from arbdesk_private.original_side_orders o where o.call_id=p_call and o.status in ('filled','partial') order by o.account_id,o.order_id loop
   select * into a from arbdesk_private.original_side_accounts where account_id=ord.account_id for update;
   select coalesce(sum(l.cash_delta),0) into previous_paid from arbdesk_private.original_side_ledger l where l.order_id=ord.order_id and l.event_type='settlement_payout';
   payout:=case when state like 'verified_%' then (ord.result->>'shares')::numeric*case when ord.side='YES' then yp else np end else 0 end;
   delta:=payout-previous_paid;
   insert into arbdesk_private.original_side_ledger(account_id,event_key,event_type,call_id,order_id,settlement_id,cash_delta,reserved_delta,detail)
    values(a.account_id,'settle:'||ord.order_id||':'||id,'settlement_payout',p_call,ord.order_id,id,delta,0,
     jsonb_build_object('payout',payout::text,'previous_projection',previous_paid::text,'state',state,'revision',previous_paid<>0));
   update arbdesk_private.original_side_accounts set cash=cash+delta where account_id=a.account_id;
  end loop;
 end if;
 return jsonb_build_object('settlement_id',id,'state',state,'paper_only',true);
end $$;

create or replace function arbdesk_private.original_side_order_guard() returns trigger language plpgsql set search_path='' as $$
begin
 if (to_jsonb(old)-array['status','lease_token','lease_until','result']) is distinct from (to_jsonb(new)-array['status','lease_token','lease_until','result']) then raise exception 'original_side_order_identity_immutable';end if;
 if old.status in ('filled','partial','rejected','expired') then raise exception 'original_side_terminal_execution_immutable';end if;
 if new.status in ('filled','partial','rejected','expired') and
  (not exists(select 1 from arbdesk_private.original_side_execution_events e where e.order_id=new.order_id and e.result=new.result)
   or not exists(select 1 from arbdesk_private.original_side_ledger l where l.order_id=new.order_id and l.event_key='fill:'||new.order_id))
  then raise exception 'terminal_execution_requires_immutable_event_and_ledger';end if;
 return new;
end $$;
create or replace trigger original_side_order_guard before update on arbdesk_private.original_side_orders for each row execute function arbdesk_private.original_side_order_guard();
create or replace trigger original_side_order_no_delete before delete on arbdesk_private.original_side_orders for each row execute function arbdesk_private.immutable_record();
create or replace function arbdesk_private.original_side_account_guard() returns trigger language plpgsql set search_path='' as $$
declare cash_total numeric; reserve_total numeric;
begin
 if (to_jsonb(old)-array['cash','reserved_cash']) is distinct from (to_jsonb(new)-array['cash','reserved_cash']) then raise exception 'original_side_funding_and_ownership_immutable';end if;
 select coalesce(sum(cash_delta),0),coalesce(sum(reserved_delta),0) into cash_total,reserve_total from arbdesk_private.original_side_ledger where account_id=new.account_id;
 if new.cash<>cash_total or new.reserved_cash<>reserve_total then raise exception 'original_side_balances_require_append_only_ledger';end if;
 return new;
end $$;
create or replace trigger original_side_account_guard before update on arbdesk_private.original_side_accounts for each row execute function arbdesk_private.original_side_account_guard();
create or replace trigger original_side_account_no_delete before delete on arbdesk_private.original_side_accounts for each row execute function arbdesk_private.immutable_record();

-- Compact shared projection. No raw depth, forecasts, coefficients or full
-- financial packets leave the private read boundary. A prediction stays one row.
create or replace function arbdesk_private.original_side_projection(p_call uuid,p_owner uuid,p_account uuid,p_stake numeric)
returns jsonb language plpgsql set search_path='' as $$
declare c arbdesk_private.original_side_calls; s arbdesk_private.original_side_settlements; v_side text; q record;
 calc jsonb; paper jsonb; legacy jsonb; packet jsonb;sides jsonb:='{}'; payout numeric; label text; state text; prediction text:='pending';
 actual_band text;actual_label text;
begin
 select * into c from arbdesk_private.original_side_calls where call_id=p_call;
 if not found then
  begin packet:=arbdesk_private.original_side_source(p_call);c:=jsonb_populate_record(null::arbdesk_private.original_side_calls,packet);
  exception when others then return null;end;
 end if;
 select * into s from arbdesk_private.original_side_settlements where call_id=p_call order by received_at desc,settlement_id desc limit 1;
 select v.winning_band_id::text,b.band_label into actual_band,actual_label from public.v_venue_market_resolution v
  join public.bands b on b.band_id=v.winning_band_id
  where v.market_id=(c.identity->>'market_id')::uuid and v.resolution_state='confirmed';
 if s.state='verified_binary' and c.eligibility in ('eligible','entry_window_expired','market_not_open') then prediction:=case when s.payout_yes=1 then 'hit' else 'miss' end;
 elsif s.state='verified_binary' then prediction:='unverified';
 elsif s.state in ('verified_void','verified_split') then prediction:=s.state;
 elsif s.state in ('disputed','unverified') then prediction:='unverified';end if;
 foreach v_side in array array['YES','NO'] loop
  select qq.*,bb.token_id,bb.observed_at,bb.payload,bb.received_at book_received into q
   from arbdesk_private.original_side_quotes qq left join arbdesk_private.original_side_books bb using(snapshot_id)
   where qq.call_id=p_call and qq.side=v_side
   order by (qq.status='captured' and arbdesk_private.original_side_quote_usable(bb.payload,p_stake)) desc,
    (qq.status='captured') desc,qq.received_at,qq.attempt_id limit 1;
  calc:=null;
  if q.snapshot_id is not null then
   begin calc:=arbdesk_private.original_side_calculate(q.payload,p_stake);
   exception when others then calc:=jsonb_build_object('reason',sqlerrm,'requested_stake',p_stake::text);end;
  end if;
  payout:=case when v_side='YES' then s.payout_yes else s.payout_no end;
  state:=case when s.state='verified_binary' then case when payout=1 then 'won' else 'lost' end
   when s.state='verified_void' then 'void' when s.state='verified_split' then 'split_payout'
   when s.state='disputed' then 'disputed' when s.state='unverified' then 'unverified' else 'pending' end;
  select coalesce(jsonb_agg(jsonb_build_object('order_id',o.order_id,'account_id',o.account_id,'policy_id',a.policy_id,
    'decision_id',o.decision_id,'allocation_id',o.allocation_id,'status',o.status,'reason',o.result->>'reason',
    'requested_at',o.requested_at,'requested_stake',al.reserved_amount::text,'shares',o.result->>'shares',
    'notional',o.result->>'notional','fee',o.result->>'fee','recorded_fill_at',o.result->>'simulated_at',
    'fill_snapshot_id',o.result->>'snapshot_id','fill_vwap',case when (o.result->>'shares')::numeric>0 then ((o.result->>'notional')::numeric/(o.result->>'shares')::numeric)::text end,
    'paper_outcome',case when o.status not in ('filled','partial') then 'no_fill' else state end,
    'paper_net_pnl',case when payout is not null and o.status in ('filled','partial') then
      ((o.result->>'shares')::numeric*payout-(o.result->>'notional')::numeric-(o.result->>'fee')::numeric)::text else null end
    ) order by o.requested_at),'[]') into paper
   from arbdesk_private.original_side_orders o join arbdesk_private.original_side_accounts a using(account_id)
   join arbdesk_private.original_side_allocations al using(allocation_id)
   where o.call_id=p_call and o.side=v_side and a.owner_id=p_owner and (p_account is null or a.account_id=p_account);
  -- Existing v1 fills keep their recorded identity and arithmetic. No YES
  -- row is rewritten, no NO trade is inferred, and no city/date-only join.
  select coalesce(jsonb_agg(jsonb_build_object('order_id',o.order_id,'account_id',o.account_id,'policy_id','day-max-frozen-fixed-stake-v1',
   'decision_id',entry.decision_id::text,'allocation_id',entry.checkpoint_id,'status',o.status,'reason',o.reason,
   'requested_at',o.requested_at,'requested_stake',entry.stake_ceiling::text,'shares',o.result->>'shares',
   'notional',o.result->>'notional','fee',o.result->>'fee','recorded_fill_at',o.result->>'simulated_at','fill_snapshot_id',o.result->>'snapshot_id',
   'fill_vwap',case when (o.result->>'shares')::numeric>0 then ((o.result->>'notional')::numeric/(o.result->>'shares')::numeric)::text end,
   'paper_outcome',case when ps.proof_id is not null and o.status in ('filled','partial') then case when ps.payout=ps.shares then 'won' when ps.payout=0 then 'lost' else 'payout_recorded' end
     when o.status in ('filled','partial') then 'pending' else 'no_fill' end,
   'paper_net_pnl',tr.pnl::text,'source_kind','recorded_v1_ledger','settlement_proof_id',ps.proof_id,
   'evidence_note','Recorded v1 fill; new side comparison requires independently verified minimum/fee metadata'
  ) order by o.requested_at),'[]') into legacy
   from public.day_max_prediction_entries entry join public.paper_orders o on o.plan_id=entry.plan_id and o.account_id=entry.account_id
   join public.paper_accounts a on a.account_id=o.account_id and a.access_mode='single_desk' and a.owner_id is null
   left join public.paper_position_settlements ps on ps.account_id=o.account_id and ps.band_id=o.band_id and ps.side=o.side
   left join lateral(select sum(t.net_pnl) pnl from public.paper_trades t where t.order_id=o.order_id and t.closed_at is not null) tr on true
   where entry.checkpoint_id=p_call and o.side=v_side and o.action='BUY' and o.band_id::text=c.identity->>'predicted_band_id'
    and (p_account is null or p_account=o.account_id);
  paper:=paper||legacy;
  sides:=sides||jsonb_build_object(v_side,jsonb_build_object('side',v_side,'token_id',c.identity->>case when v_side='YES' then 'token_yes' else 'token_no' end,
   'quote_kind',coalesce(q.quote_kind,'missing'),'quote_id',q.attempt_id,'quote_status',coalesce(q.status,'not_recorded'),
   'quote_selection','first_executable_direct_for_stake-v2.1',
   'quote_attempts',coalesce((select jsonb_agg(jsonb_build_object('attempt_id',t.attempt_id,'status',t.status,
     'reason',t.reason,'requested_at',t.requested_at,'received_at',t.received_at,'snapshot_id',t.snapshot_id) order by t.received_at,t.attempt_id)
    from (select * from arbdesk_private.original_side_quotes a where a.call_id=p_call and a.side=v_side order by a.received_at,a.attempt_id limit 10) t),'[]'),
   'attempts_truncated',(select count(*)>10 from arbdesk_private.original_side_quotes a where a.call_id=p_call and a.side=v_side),
   'reason',coalesce(calc->>'reason',q.reason),'quote_requested_at',q.requested_at,'quote_received_at',q.received_at,
   'book_observed_at',q.observed_at,'snapshot_id',q.snapshot_id,'side_outcome',state,
   'comparison_stake',p_stake::text,'calculation_version','original-bucket-sides-v2.1',
   'evaluation',calc,'paper_orders',paper,'hypothetical_net_pnl',case when payout is not null and calc->>'shares' is not null then
    ((calc->>'shares')::numeric*payout-(calc->>'filled_cost')::numeric)::text else null end));
 end loop;
 return jsonb_build_object('call_id',c.call_id,'city_key',c.city_key,'target_date',c.target_date,'checkpoint',c.checkpoint,
  'source_kind',c.source_kind,'predicted_band_id',c.identity->>'predicted_band_id','predicted_band_label',c.identity->>'predicted_band_label',
  'frozen_at',c.frozen_at,'called_at',c.source->>'decided_at','scheduled_local',c.source->>'local_decision_time',
  'timezone',c.identity->>'timezone','station',c.identity->>'station','engine_version',c.source->>'engine_version',
  'rule_receipt_id',c.identity->>'rule_receipt_id','rules_sha256',c.identity->>'rules_sha256','ladder_sha256',c.identity->>'ladder_sha256',
  'input_as_of',c.source->>'input_as_of','input_sha256',c.source->>'input_sha256','eligibility',c.eligibility,
  'prediction_outcome',case when s.state='verified_binary' and actual_band is not null
    and ((actual_band=c.identity->>'predicted_band_id') is distinct from (s.payout_yes=1)) then 'unverified' else prediction end,
  'actual_winning_band_id',case when s.state='verified_binary' then coalesce(actual_band,case when s.payout_yes=1 then c.identity->>'predicted_band_id' end) end,
  'actual_winning_band_label',case when s.state='verified_binary' then coalesce(actual_label,case when s.payout_yes=1 then c.identity->>'predicted_band_label' end) end,
  'settlement_state',coalesce(s.state,'pending'),'settlement_receipt_id',s.settlement_id,'settlement_note',s.evidence->>'reason',
  'sides',sides);
end $$;

create or replace function public.read_original_side_history(p_owner uuid,p_from date,p_to date,p_cities text[] default null,p_checkpoints text[] default null,
 p_account uuid default null,p_stake numeric default 10,p_offset integer default 0,p_limit integer default 100)
returns jsonb language plpgsql security definer set search_path='' as $$
declare result jsonb; total integer; lim integer:=least(greatest(p_limit,1),200);coverage jsonb;
begin
 if not arbdesk_private.original_side_owner_valid(p_owner) then raise exception 'owner_required';end if;
 if p_from is null or p_to is null or p_to<p_from or p_to-p_from>31 or p_offset<0 or p_offset>10000
  or p_stake<=0 or p_stake>1000000 or cardinality(p_cities)>60 then raise exception 'bounded_history_scope_required';end if;
 if p_account is not null and not exists(select 1 from arbdesk_private.original_side_accounts where account_id=p_account and owner_id=p_owner) then raise exception 'account_owner_required';end if;
 select count(*) into total from arbdesk_private.original_side_calls c where c.target_date between p_from and p_to
  and (p_cities is null or c.city_key=any(p_cities)) and (p_checkpoints is null or c.checkpoint=any(p_checkpoints));
 select coalesce(jsonb_agg(arbdesk_private.original_side_projection(t.call_id,p_owner,p_account,p_stake)),'[]') into result from(
  select c.call_id from arbdesk_private.original_side_calls c where c.target_date between p_from and p_to
   and (p_cities is null or c.city_key=any(p_cities)) and (p_checkpoints is null or c.checkpoint=any(p_checkpoints))
  order by c.target_date desc,c.frozen_at desc,c.call_id limit lim offset p_offset
 ) t;
 with calls as (select * from jsonb_array_elements(result)),positions as (
  select o.* from calls r cross join lateral jsonb_array_elements((r.value#>'{sides,YES,paper_orders}')||(r.value#>'{sides,NO,paper_orders}')) o
 ) select jsonb_build_object('scope','returned_page_only','calls',jsonb_array_length(result),
  'quote_covered_calls',(select count(*) from calls where value#>>'{sides,YES,evaluation,shares}' is not null or value#>>'{sides,NO,evaluation,shares}' is not null),
  'both_side_quote_calls',(select count(*) from calls where value#>>'{sides,YES,evaluation,shares}' is not null and value#>>'{sides,NO,evaluation,shares}' is not null),
  'attempted_orders',(select count(*) from positions),
  'filled_positions',(select count(*) from positions where value->>'status' in ('filled','partial')),
  'resolved_positions',(select count(*) from positions where value->>'status' in ('filled','partial') and value->>'paper_net_pnl' is not null),
  'pending_positions',(select count(*) from positions where value->>'status' in ('filled','partial') and value->>'paper_net_pnl' is null),
  'recorded_net_pnl',case when p_account is null then null else (select sum((value->>'paper_net_pnl')::numeric)::text from positions where value->>'status' in ('filled','partial')) end)
 into coverage;
 return jsonb_build_object('rows',result,'total_calls',total,'offset',p_offset,'limit',lim,'truncated',p_offset+lim<total,
  'page_summary',coverage,
  'capacity',arbdesk_private.original_side_capacity(),'comparison_stake',p_stake::text,'calculation_version','original-bucket-sides-v2.1','paper_only',true);
end $$;

create or replace function public.resolve_original_side_history_rows(p_owner uuid,p_rows jsonb,p_account uuid default null,p_stake numeric default 10)
returns jsonb language plpgsql security definer set search_path='' as $$
declare row jsonb; id uuid; out_rows jsonb:='[]';
begin
 if not arbdesk_private.original_side_owner_valid(p_owner) then raise exception 'owner_required';end if;
 if jsonb_typeof(p_rows) is distinct from 'array' or jsonb_array_length(p_rows)>300 or p_stake<=0 or p_stake>1000000 then raise exception 'bounded_row_scope_required';end if;
 if p_account is not null and not exists(select 1 from arbdesk_private.original_side_accounts where account_id=p_account and owner_id=p_owner) then raise exception 'account_owner_required';end if;
 for row in select * from jsonb_array_elements(p_rows) loop
  id:=null;
  -- Exact original timestamp, label, checkpoint and version when supplied.
  -- Legacy day_ahead is ONLY its saved pre-midnight receipt, never native D-1 09:00.
  select c.call_id into id from arbdesk_private.original_side_calls c where c.city_key=row->>'city_key'
   and c.target_date=(row->>'target_date')::date and c.checkpoint=row->>'checkpoint'
   and c.identity->>'predicted_band_label'=row->>'predicted_band_label'
   and (row->>'engine_version' is null or c.source->>'engine_version'=row->>'engine_version')
   and case when row->>'checkpoint'='day_ahead' then c.source_kind='day_max_day_ahead_calls'
     and (c.source#>>'{predictive_context,source_computed_at}')::timestamptz=(row->>'called_at')::timestamptz
    else c.source_kind='prediction_checkpoints' and (c.source->>'decided_at')::timestamptz=(row->>'called_at')::timestamptz end
   order by c.frozen_at,c.call_id limit 1;
  if id is null and row->>'checkpoint'<>'day_ahead' then
   select p.checkpoint_id into id from public.prediction_checkpoints p join public.v_canonical_bands b on b.band_id::text=p.top_band_id
    where p.city_key=row->>'city_key' and p.target_date=(row->>'target_date')::date and p.checkpoint=row->>'checkpoint'
     and p.decided_at=(row->>'called_at')::timestamptz and b.band_label=row->>'predicted_band_label'
     and (row->>'engine_version' is null or p.engine_version=row->>'engine_version')
     and not exists(select 1 from public.prediction_checkpoints earlier where earlier.city_key=p.city_key and earlier.target_date=p.target_date and earlier.checkpoint=p.checkpoint
      and (earlier.decided_at,earlier.checkpoint_id)<(p.decided_at,p.checkpoint_id)) order by p.decided_at,p.checkpoint_id limit 1;
  elsif id is null and row->>'checkpoint'='day_ahead' then
   select d.call_id into id from public.day_max_day_ahead_calls d join public.v_canonical_bands b on b.band_id::text=d.record->>'top_band_id'
    where d.city_key=row->>'city_key' and d.target_date=(row->>'target_date')::date and d.source_computed_at=(row->>'called_at')::timestamptz
     and b.band_label=row->>'predicted_band_label' and (row->>'engine_version' is null or d.record->>'engine_version'=row->>'engine_version') limit 1;
  end if;
  out_rows:=out_rows||jsonb_build_array(jsonb_build_object('row_key',row->>'row_key','call',
   case when id is null then null else arbdesk_private.original_side_projection(id,p_owner,p_account,p_stake) end,
   'reason',case when id is null then 'no_proven_original_call_link' else null end));
 end loop;
 return jsonb_build_object('rows',out_rows,'truncated',false,'comparison_stake',p_stake::text);
end $$;

create or replace function public.read_original_side_desk(p_owner uuid) returns jsonb
language sql security definer set search_path='' as $$
 select jsonb_build_object('policies',coalesce((select jsonb_agg(jsonb_build_object('policy_id',p.policy_id,'config',p.config,'created_at',p.created_at,
  'state',coalesce((select e.action from arbdesk_private.original_side_policy_events e where e.policy_id=p.policy_id order by e.event_id desc limit 1),'inactive'),
  'account',case when a.account_id is null then null else jsonb_build_object('account_id',a.account_id,'starting_cash',a.starting_cash::text,'cash',a.cash::text,'reserved_cash',a.reserved_cash::text,'activated_at',a.activated_at,
  'pending_positions',(select count(*) from arbdesk_private.original_side_orders o where o.account_id=a.account_id and o.status in ('filled','partial') and coalesce((select s.state from arbdesk_private.original_side_settlements s where s.call_id=o.call_id order by s.received_at desc,s.settlement_id desc limit 1),'pending') not like 'verified_%'),
    'recorded_pnl',(select sum((o.result->>'shares')::numeric*case when o.side='YES' then s.payout_yes else s.payout_no end-(o.result->>'notional')::numeric-(o.result->>'fee')::numeric)::text
     from arbdesk_private.original_side_orders o join lateral(select ss.* from arbdesk_private.original_side_settlements ss where ss.call_id=o.call_id order by ss.received_at desc,ss.settlement_id desc limit 1) s on s.state like 'verified_%'
     where o.account_id=a.account_id and o.status in ('filled','partial'))
  ) end) order by p.created_at desc) from (select * from arbdesk_private.original_side_policies pp where pp.owner_id=p_owner order by pp.created_at desc limit 20) p left join arbdesk_private.original_side_accounts a using(policy_id)),'[]'),
  'capacity',arbdesk_private.original_side_capacity(),'truncated',(select count(*)>20 from arbdesk_private.original_side_policies pp where pp.owner_id=p_owner),'paper_only',true)
$$;

create or replace function public.read_original_side_call(p_owner uuid,p_call uuid,p_account uuid default null,p_stake numeric default 10)
returns jsonb language plpgsql security definer set search_path='' as $$
begin
 if not arbdesk_private.original_side_owner_valid(p_owner) or p_stake is null or p_stake<=0 or p_stake>1000000 then raise exception 'bounded_owner_read_required';end if;
 if p_account is not null and not exists(select 1 from arbdesk_private.original_side_accounts where account_id=p_account and owner_id=p_owner) then raise exception 'account_owner_required';end if;
 return arbdesk_private.original_side_projection(p_call,p_owner,p_account,p_stake);
end $$;

create or replace function public.read_original_side_ledger(p_owner uuid,p_account uuid,p_before bigint default null) returns jsonb
language plpgsql security definer set search_path='' as $$
declare entries jsonb; plans jsonb; stages jsonb; next_id bigint;
begin
 if not exists(select 1 from arbdesk_private.original_side_accounts a where a.account_id=p_account and a.owner_id=p_owner) then raise exception 'private_account_owner_required';end if;
 select coalesce(jsonb_agg(jsonb_build_object('entry_id',l.entry_id::text,'event_type',l.event_type,'call_id',l.call_id,'order_id',l.order_id,
  'settlement_id',l.settlement_id,'cash_delta',l.cash_delta::text,'reserved_delta',l.reserved_delta::text,'detail',l.detail,'recorded_at',l.recorded_at) order by l.entry_id desc),'[]'),min(l.entry_id)
  into entries,next_id from(select * from arbdesk_private.original_side_ledger x where x.account_id=p_account and (p_before is null or x.entry_id<p_before) order by x.entry_id desc limit 100) l;
 select coalesce(jsonb_agg(jsonb_build_object('plan_id',p.plan_id,'target_date',p.target_date,'frozen_at',p.frozen_at,
  'available_settled_cash',p.available_settled_cash::text,'allocation_per_slot',p.allocation_per_slot::text,'rounding_remainder',p.rounding_remainder::text,
  'slots',(select jsonb_agg(jsonb_build_object('allocation_id',al.allocation_id,'city_key',al.city_key,'checkpoint',al.checkpoint,'side',al.side,
    'reserved_amount',al.reserved_amount::text,'order_id',o.order_id,'status',coalesce(o.status,case when exists(select 1 from arbdesk_private.original_side_ledger ll where ll.event_key='release:'||al.allocation_id) then 'expired_unused' else 'reserved_for_local_entry' end),
    'call_id',o.call_id) order by al.city_key) from arbdesk_private.original_side_allocations al left join arbdesk_private.original_side_orders o using(allocation_id) where al.plan_id=p.plan_id)
 ) order by p.target_date desc),'[]') into plans from(select * from arbdesk_private.original_side_allocation_plans ap where ap.account_id=p_account order by ap.target_date desc limit 7) p;
 select coalesce(jsonb_agg(jsonb_build_object('stage',e.stage,'reason',e.reason,'call_id',e.call_id,'recorded_at',e.recorded_at) order by e.event_id desc),'[]') into stages
  from(select * from arbdesk_private.original_side_stage_events se where se.account_id=p_account order by event_id desc limit 50) e;
 return jsonb_build_object('entries',entries,'allocation_plans',plans,'stage_events',stages,'next_before',next_id::text,
  'truncated',exists(select 1 from arbdesk_private.original_side_ledger x where x.account_id=p_account and x.entry_id<next_id));
end $$;

-- All RPCs in this migration are service-only. The web route separately
-- verifies the operator session and supplies its verified user id for reads.
-- A private archive first freezes the exact immutable membership and mutable
-- account/order projections in ONE MVCC snapshot. Later bounded pages read
-- only those immutable IDs. Concurrent new receipts cannot enter the backup.
create or replace function public.begin_original_side_archive() returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare t text;pk text;ids jsonb;rows jsonb;entities jsonb:='[]';body text;n integer;
begin
 foreach t in array array['original_side_owners','original_side_rule_receipts','original_side_ladder_receipts','original_side_calls','original_side_books','original_side_quotes',
  'original_side_evaluations','original_side_policies','original_side_policy_events','original_side_accounts',
  'original_side_allocation_plans','original_side_allocations','original_side_decisions','original_side_orders',
  'original_side_execution_events','original_side_resolution_receipts','original_side_settlements','original_side_ledger','original_side_stage_events'] loop
  select a.attname into pk from pg_index i join pg_attribute a on a.attrelid=i.indrelid and a.attnum=i.indkey[0]
   where i.indrelid=('arbdesk_private.'||t)::regclass and i.indisprimary;
  if t in ('original_side_accounts','original_side_orders') then
   execute format('select coalesce(jsonb_agg(to_jsonb(s)::text),''[]''),count(*) from (select * from arbdesk_private.%I order by %I limit 2001) s',t,pk) into rows,n;
   if n>2000 then raise exception 'private_archive_mutable_snapshot_bound_exceeded';end if;
   entities:=entities||jsonb_build_array(jsonb_build_object('table',t,'pk',pk,'mutable_snapshot',rows,'rows',n));
  else
   execute format('select coalesce(jsonb_agg(s.%I::text),''[]''),count(*) from (select %I from arbdesk_private.%I order by %I limit 50001) s',pk,pk,t,pk) into ids,n;
   if n>50000 then raise exception 'private_archive_membership_bound_exceeded';end if;
   entities:=entities||jsonb_build_array(jsonb_build_object('table',t,'pk',pk,'immutable_ids',ids,'rows',n));
  end if;
 end loop;
 body:=jsonb_build_object('schema_version','20261111214539','snapshot_at',statement_timestamp(),'entities',entities,'sessions','excluded_ephemeral_owner_credentials_reissue_after_restore')::text;
 if octet_length(body)>8388608 then raise exception 'private_archive_manifest_size_bound_exceeded';end if;
 return jsonb_build_object('payload',body,'sha256',encode(sha256(convert_to(body,'UTF8')),'hex'));
end $$;

create or replace function public.read_original_side_archive_rows(p_table text,p_ids text[]) returns jsonb
language plpgsql stable security definer set search_path='' as $$
declare pk text;kind text;rows jsonb;n integer;
begin
 if p_table not in ('original_side_owners','original_side_rule_receipts','original_side_ladder_receipts','original_side_calls','original_side_books','original_side_quotes',
  'original_side_evaluations','original_side_policies','original_side_policy_events','original_side_allocation_plans',
  'original_side_allocations','original_side_decisions','original_side_execution_events','original_side_resolution_receipts','original_side_settlements',
  'original_side_ledger','original_side_stage_events') or cardinality(p_ids)>100 or cardinality(p_ids)<1 then raise exception 'bounded_private_archive_ids_required';end if;
 select a.attname,format_type(a.atttypid,a.atttypmod) into pk,kind from pg_index i join pg_attribute a on a.attrelid=i.indrelid and a.attnum=i.indkey[0]
  where i.indrelid=('arbdesk_private.'||p_table)::regclass and i.indisprimary;
 if kind not in ('uuid','text','bigint') then raise exception 'unsupported_private_archive_key';end if;
 execute format('select coalesce(jsonb_agg(to_jsonb(t)::text order by t.%I),''[]''),count(*) from arbdesk_private.%I t where t.%I=any($1::%s[])',pk,p_table,pk,kind) using p_ids into rows,n;
 if n<>(select count(distinct x) from unnest(p_ids) x) then raise exception 'private_archive_member_missing';end if;
 if pg_column_size(rows)>8388608 then raise exception 'private_archive_page_size_bound_exceeded_reduce_page';end if;
 return rows;
end $$;

do $$ declare f record; begin
 for f in select p.oid::regprocedure signature from pg_proc p join pg_namespace n on n.oid=p.pronamespace
  where n.nspname in ('public','arbdesk_private') and (p.proname like '%original_side_%') loop
  execute format('revoke all on function %s from public,anon,authenticated',f.signature);
  execute format('grant execute on function %s to service_role',f.signature);
 end loop;
end $$;
