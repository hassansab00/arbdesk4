begin;
create function public.expire_paper_commands() returns integer
language plpgsql security invoker set search_path='' as $$
declare a public.paper_accounts; o public.paper_orders; total integer:=0;
begin
  for a in select * from public.paper_accounts x where exists(select 1 from public.paper_orders q
      where q.account_id=x.account_id and q.expires_at<=now() and q.status in ('queued','working'))
      order by x.created_at for update skip locked limit 20 loop
    for o in select * from public.paper_orders where account_id=a.account_id and expires_at<=now()
      and (status='queued' or (status='working' and lease_until<now())) for update loop
      update public.paper_accounts set reserved_cash=reserved_cash-o.cash_ceiling where account_id=a.account_id;
      update public.paper_orders set status='expired',lease_until=null,reason='order_expired',
        result=jsonb_build_object('status','expired','shares','0','notional','0','fee','0','fills','[]'::jsonb)
        where order_id=o.order_id;
      insert into public.paper_activity(account_id,order_id,event_type,payload)
        values(a.account_id,o.order_id,'order_expired',jsonb_build_object('released',o.cash_ceiling));
      total:=total+1;
    end loop;
  end loop;
  update public.paper_trade_plans set status='expired' where status='pending_approval' and expires_at<=now();
  return total;
end $$;
revoke all on function public.expire_paper_commands() from public,anon,authenticated;
grant execute on function public.expire_paper_commands() to service_role;

create function arbdesk_private.set_paper_exit_policy(p_account uuid,p_enabled boolean,p_take_profit numeric,p_stop_loss numeric) returns boolean
language plpgsql security definer set search_path='' as $$
begin
  if auth.uid() is null or not exists(select 1 from public.paper_accounts where account_id=p_account and owner_id=auth.uid()) then
    raise exception 'Account access denied'; end if;
  if p_enabled is null or not coalesce(p_take_profit>0 and p_take_profit<=10 and p_stop_loss>0 and p_stop_loss<1,false) then
    raise exception 'Take-profit must be 0–10 and stop-loss 0–1, both strictly positive'; end if;
  update public.paper_accounts set policy=policy||jsonb_build_object('auto_exit_enabled',p_enabled,
    'take_profit_fraction',p_take_profit,'stop_loss_fraction',p_stop_loss),policy_version=policy_version+1 where account_id=p_account;
  insert into public.paper_activity(account_id,event_type,payload) values(p_account,'exit_policy_changed',
    jsonb_build_object('enabled',p_enabled,'take_profit_fraction',p_take_profit,'stop_loss_fraction',p_stop_loss));
  return true;
end $$;
revoke all on function arbdesk_private.set_paper_exit_policy(uuid,boolean,numeric,numeric) from public,anon;
grant usage on schema arbdesk_private to authenticated;
grant execute on function arbdesk_private.set_paper_exit_policy(uuid,boolean,numeric,numeric) to authenticated;
create function public.set_paper_exit_policy(p_account uuid,p_enabled boolean,p_take_profit numeric,p_stop_loss numeric) returns boolean
language sql security invoker set search_path='' as $$ select arbdesk_private.set_paper_exit_policy(p_account,p_enabled,p_take_profit,p_stop_loss) $$;
revoke all on function public.set_paper_exit_policy(uuid,boolean,numeric,numeric) from public,anon;
grant execute on function public.set_paper_exit_policy(uuid,boolean,numeric,numeric) to authenticated;

create function public.queue_automatic_paper_exit(p_account uuid,p_command uuid,p_band uuid,p_side text,
  p_limit numeric,p_evidence jsonb,p_policy_version bigint) returns uuid
language plpgsql security invoker set search_path='' as $$
declare a public.paper_accounts; pos public.paper_positions; b public.bands; id uuid;
  net numeric; gain numeric; proof public.paper_book_evidence;
begin
  select * into a from public.paper_accounts where account_id=p_account for update;
  if not found then raise exception 'Unknown account'; end if;
  select order_id into id from public.paper_orders where account_id=p_account and command_key=p_command;
  if found then return id; end if;
  if a.mode<>'automatic' or not coalesce((a.policy->>'auto_exit_enabled')::boolean,false)
    or a.policy_version<>p_policy_version then raise exception 'Automatic exit policy unavailable or changed'; end if;
  select * into pos from public.paper_positions where account_id=p_account and band_id=p_band and side=p_side for update;
  if not found or pos.shares<=0 or pos.cost_basis<=0 then raise exception 'No open position'; end if;
  if exists(select 1 from public.paper_orders where account_id=p_account and band_id=p_band and side=p_side
    and status in ('queued','working')) then raise exception 'Position has a pending order'; end if;
  select * into b from public.bands where band_id=p_band;
  select * into proof from public.paper_book_evidence where snapshot_id=p_evidence->>'snapshot_id';
  if not found or proof.token_id<>(case when p_side='YES' then b.token_yes else b.token_no end)
    or proof.observed_at>now() or proof.observed_at<now()-interval '120 seconds'
    then raise exception 'Fresh direct token book required'; end if;
  if p_evidence->>'status'<>'filled' or (p_evidence->>'shares')::numeric<>pos.shares then raise exception 'Full exit quote required'; end if;
  net:=(p_evidence->>'notional')::numeric-(p_evidence->>'fee')::numeric;
  gain:=net/pos.cost_basis-1;
  if not coalesce(gain>=(a.policy->>'take_profit_fraction')::numeric or gain<=-(a.policy->>'stop_loss_fraction')::numeric,false)
    then raise exception 'Exit threshold not reached'; end if;
  insert into public.paper_orders(account_id,command_key,band_id,token_id,side,action,origin,shares,limit_price,
    cash_ceiling,policy_version,expires_at,reason,context)
    values(p_account,p_command,p_band,proof.token_id,p_side,'SELL','automatic',pos.shares,p_limit,0,
      a.policy_version,now()+interval '5 minutes',case when gain>=0 then 'take_profit' else 'stop_loss' end,
      jsonb_build_object('exit_preview',p_evidence,'gain_fraction',gain,'position_basis',pos.cost_basis)) returning order_id into id;
  insert into public.paper_activity(account_id,order_id,event_type,payload) values(p_account,id,'automatic_exit_submitted',
    jsonb_build_object('preview',p_evidence,'gain_fraction',gain,'policy_version',a.policy_version));
  return id;
end $$;
revoke all on function public.queue_automatic_paper_exit(uuid,uuid,uuid,text,numeric,jsonb,bigint) from public,anon,authenticated;
grant execute on function public.queue_automatic_paper_exit(uuid,uuid,uuid,text,numeric,jsonb,bigint) to service_role;
create table public.paper_resolution_evidence (
  proof_id text primary key,
  condition_id text not null,
  token_yes text not null,
  token_no text not null,
  winning_token text not null check(winning_token in (token_yes,token_no)),
  captured_at timestamptz not null default clock_timestamp(),
  gamma jsonb not null,
  clob jsonb not null,
  source_urls jsonb not null
);
alter table public.paper_resolution_evidence enable row level security;
revoke all on public.paper_resolution_evidence from anon,authenticated,service_role;
grant select,insert on public.paper_resolution_evidence to service_role;
create trigger resolution_immutable before update or delete on public.paper_resolution_evidence
  for each row execute function arbdesk_private.immutable_record();
create trigger resolution_no_truncate before truncate on public.paper_resolution_evidence
  for each statement execute function arbdesk_private.immutable_record();

create table public.paper_position_settlements (
  account_id uuid not null references public.paper_accounts(account_id),
  band_id uuid not null references public.bands(band_id),
  side text not null check(side in ('YES','NO')),
  proof_id text not null references public.paper_resolution_evidence(proof_id),
  shares numeric not null,
  payout numeric not null,
  cost_basis numeric not null,
  settled_at timestamptz not null default now(),
  primary key(account_id,band_id,side)
);
alter table public.paper_position_settlements enable row level security;
revoke all on public.paper_position_settlements from anon,authenticated,service_role;
grant select on public.paper_position_settlements to authenticated;
grant select,insert on public.paper_position_settlements to service_role;
create policy owner_read on public.paper_position_settlements for select to authenticated using(exists(
  select 1 from public.paper_accounts a where a.account_id=paper_position_settlements.account_id and a.owner_id=(select auth.uid())));
create trigger settlement_immutable before update or delete on public.paper_position_settlements
  for each row execute function arbdesk_private.immutable_record();
create trigger settlement_no_truncate before truncate on public.paper_position_settlements
  for each statement execute function arbdesk_private.immutable_record();

create function public.settle_paper_inventory(p_band uuid,p_proof text) returns integer
language plpgsql security invoker set search_path='' as $$
declare b public.bands; proof public.paper_resolution_evidence; a public.paper_accounts;
  pos public.paper_positions; o public.paper_orders; payout numeric; total integer:=0;
begin
  select * into b from public.bands where band_id=p_band;
  select * into proof from public.paper_resolution_evidence where proof_id=p_proof;
  if not found or b.band_id is null or proof.condition_id<>b.condition_id or proof.token_yes<>b.token_yes or proof.token_no<>b.token_no
    then raise exception 'Resolution identity mismatch'; end if;
  if proof.gamma->>'conditionId' is distinct from b.condition_id or proof.clob->>'condition_id' is distinct from b.condition_id
    or proof.gamma->>'closed' is distinct from 'true' or proof.gamma->>'umaResolutionStatus' is distinct from 'resolved'
    or proof.clob->>'closed' is distinct from 'true' or proof.clob->>'accepting_orders' is distinct from 'false'
    or (select count(*) from jsonb_array_elements(proof.clob->'tokens') t where t->>'winner'='true')<>1
    or not exists(select 1 from jsonb_array_elements(proof.clob->'tokens') t where t->>'token_id'=proof.winning_token and t->>'winner'='true')
    then raise exception 'Final venue resolution evidence required'; end if;
  for a in select * from public.paper_accounts x where exists(select 1 from public.paper_positions p where
    p.account_id=x.account_id and p.band_id=p_band and p.shares>0) order by x.account_id for update skip locked loop
    if exists(select 1 from public.paper_orders where account_id=a.account_id and band_id=p_band
      and status='working' and lease_until>=now()) then continue; end if;
    for o in select * from public.paper_orders where account_id=a.account_id and band_id=p_band and status in ('queued','working') for update loop
      update public.paper_accounts set reserved_cash=reserved_cash-o.cash_ceiling where account_id=a.account_id;
      update public.paper_orders set status='canceled',reason='market_resolved',lease_until=null where order_id=o.order_id;
      insert into public.paper_activity(account_id,order_id,event_type,payload) values(a.account_id,o.order_id,'order_canceled',jsonb_build_object('proof_id',p_proof,'reason','market_resolved'));
    end loop;
    for pos in select * from public.paper_positions where account_id=a.account_id and band_id=p_band and shares>0 for update loop
      payout:=case when proof.winning_token=(case when pos.side='YES' then b.token_yes else b.token_no end) then pos.shares else 0 end;
      insert into public.paper_position_settlements(account_id,band_id,side,proof_id,shares,payout,cost_basis)
        values(a.account_id,p_band,pos.side,p_proof,pos.shares,payout,pos.cost_basis);
      update public.paper_accounts set cash=cash+payout where account_id=a.account_id;
      update public.paper_positions set shares=0,cost_basis=0,realized_pnl=realized_pnl+payout-pos.cost_basis
        where account_id=a.account_id and band_id=p_band and side=pos.side;
      insert into public.paper_activity(account_id,event_type,cash_delta,payload) values(a.account_id,'position_settled',payout,
        jsonb_build_object('band_id',p_band,'side',pos.side,'shares',pos.shares,'payout',payout,'cost_basis',pos.cost_basis,
          'realized_pnl',payout-pos.cost_basis,'proof_id',p_proof,'source_urls',proof.source_urls));
      total:=total+1;
    end loop;
  end loop;
  return total;
end $$;
revoke all on function public.settle_paper_inventory(uuid,text) from public,anon,authenticated;
grant execute on function public.settle_paper_inventory(uuid,text) to service_role;
commit;
