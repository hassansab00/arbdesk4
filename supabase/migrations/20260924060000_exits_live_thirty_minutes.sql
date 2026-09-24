-- ===========================================================================
-- AN EXIT ORDER LIVES 30 MINUTES (plan v2 P5.0 item 2)
--
-- Measured live on 24 Sep: every SELL the desk ever queued - 5 of 5, 17-19 Sep -
-- expired unfilled, each with a 5-minute life. Exits were evaluated AFTER the
-- fill step in pipeline_intraday, so an exit order waited for the next
-- worker, four hours away, and was dead long before it.
--
-- Two changes, this file and the workflow:
--   * pipeline_intraday evaluates exits BEFORE the worker fills, so an exit
--     queued in a run is filled in that same run;
--   * an automatic exit order lives 30 minutes instead of 5. The worker still
--     prices it against a fresh book when it fills (max_book_age_seconds), and
--     the limit price still bounds it, so the longer life only stops a slow
--     step from killing it.
--
-- Otherwise identical to 20260912083740_paper_position_lifecycle.sql (the
-- live body's md5 matched that file's on 24 Sep).
-- ===========================================================================

create or replace function public.queue_automatic_paper_exit(p_account uuid,p_command uuid,p_band uuid,p_side text,
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
      a.policy_version,now()+interval '30 minutes',case when gain>=0 then 'take_profit' else 'stop_loss' end,
      jsonb_build_object('exit_preview',p_evidence,'gain_fraction',gain,'position_basis',pos.cost_basis)) returning order_id into id;
  insert into public.paper_activity(account_id,order_id,event_type,payload) values(p_account,id,'automatic_exit_submitted',
    jsonb_build_object('preview',p_evidence,'gain_fraction',gain,'policy_version',a.policy_version));
  return id;
end $$;
revoke all on function public.queue_automatic_paper_exit(uuid,uuid,uuid,text,numeric,jsonb,bigint) from public,anon,authenticated;
grant execute on function public.queue_automatic_paper_exit(uuid,uuid,uuid,text,numeric,jsonb,bigint) to service_role;
