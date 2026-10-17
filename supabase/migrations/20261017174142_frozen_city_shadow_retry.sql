-- Preserve causal shadow replays without weakening the primary call guard.
-- Assert the old clauses before changing them; every other clause is kept.
do $migration$
declare original text; changed text;
begin
  original:=pg_get_functiondef('arbdesk_private.freeze_prediction_before_outcome()'::regprocedure);
  changed:=replace(original,
    'if new.decided_at > receipt+interval ''5 seconds'' or new.decided_at < receipt-interval ''15 minutes'' then',
    'if new.decided_at > receipt+interval ''5 seconds'' or (tg_table_name=''prediction_checkpoints'' and new.decided_at < receipt-interval ''15 minutes'') then');
  changed:=replace(changed,
    'if parent_at is null or receipt>parent_at+interval ''30 minutes'' or new.decided_at<parent_at then',
    'if parent_at is null or new.decided_at<parent_at or (receipt>parent_at+interval ''30 minutes'' and not (new.decided_at=parent_at and receipt<=parent_at+interval ''6 hours'')) then');
  if changed=original and position('6 hours' in original)=0 then
    raise exception 'Unexpected freeze function definition; refusing an unverified replacement';
  end if;
  if changed<>original then execute changed; end if;
end $migration$;

-- Explicit service-only working-state policy; no anonymous write grant.
drop policy if exists calibration_service on public.derived_wx_city_calibration;
create policy calibration_service on public.derived_wx_city_calibration for all to service_role using(true) with check(true);
