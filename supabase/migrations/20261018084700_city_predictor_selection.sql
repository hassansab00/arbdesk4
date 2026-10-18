-- Both alternatives remain causal evidence when the baseline is selected.
-- Patch only the raw-packet branch; preserve every receipt, schedule, outcome
-- and six-hour shadow-replay guard in the installed trigger.
do $migration$
declare original text; revised text;
  old_condition text := 'if new.priced_from like ''wxrd3:%'' and (new.input_key is not null or new.raw_probs is not null) then';
  new_condition text := 'if new.input_key is not null or new.raw_probs is not null then';
  old_version text := 'packet->>''version'' is distinct from new.priced_from';
  new_version text := 'packet->>''version'' is distinct from coalesce(new.predictive_context->>''raw_model_version'',new.priced_from)';
  marker text := '    -- Validate a selector receipt without relabelling the chosen baseline.';
begin
  select pg_get_functiondef('arbdesk_private.freeze_prediction_before_outcome()'::regprocedure) into original;
  if position(marker in original)>0 then return; end if;
  if position(old_condition in original)=0 or position(old_version in original)=0 then
    raise exception 'unexpected installed prediction guard; refusing to replace it';
  end if;
  revised:=replace(replace(original,old_condition,new_condition),old_version,new_version);
  if replace(replace(revised,new_condition,old_condition),new_version,old_version)<>original then
    raise exception 'unrelated prediction guard changed';
  end if;
  revised:=replace(revised,'    new.decided_at := receipt;', $selector$
    -- Validate a selector receipt without relabelling the chosen baseline.
    if new.predictive_context ? 'raw_model_version' then
      if new.predictive_context->'model_selection'->>'policy' is distinct from 'city-horizon-selector-v1'
         or new.predictive_context->'model_selection'->>'checkpoint' is distinct from new.checkpoint
         or coalesce(new.predictive_context->'model_selection'->>'selected','') not in ('wxrd3','baseline')
         or (coalesce((new.predictive_context->'model_selection'->>'active')::boolean,false) and (
             new.predictive_context->'model_selection'->>'fit_at' is null
             or new.predictive_context->'model_selection'->>'validated_through' is null
             or (new.predictive_context->'model_selection'->>'fit_at')::timestamptz > new.input_as_of
             or (new.predictive_context->'model_selection'->>'validated_through')::date >= new.target_date)) then
        raise exception 'Freeze refused: invalid city-horizon model selection receipt';
      end if;
      if new.predictive_context->'model_selection'->>'selected'='baseline' then
        if new.probs is distinct from new.baseline_probs
           or new.priced_from is distinct from regexp_replace(new.baseline_model,'^priced_from:','')
           or (new.checkpoint in ('noon','prepeak_2h','prepeak_1h','postpeak_1h')
               and coalesce((new.predictive_context->'model_selection'->>'active')::boolean,false) is not true) then
          raise exception 'Freeze refused: baseline selection does not match its frozen predictor';
        end if;
      elsif new.priced_from is distinct from new.predictive_context->>'raw_model_version' then
        raise exception 'Freeze refused: raw selection does not match its frozen predictor';
      end if;
    end if;
    new.decided_at := receipt;$selector$);
  if position(marker in revised)=0 then raise exception 'prediction guard marker not found'; end if;
  execute revised;
end $migration$;

create or replace view public.v_city_predictor_pairs with(security_invoker=true) as
select p.city_key,p.target_date,p.checkpoint,p.decided_at,p.frozen_at,p.input_as_of,p.input_sha256,
       coalesce(p.predictive_context->>'raw_model_version',p.priced_from) as model_version,
       p.raw_probs,p.probs,p.baseline_probs,p.baseline_model,p.calibration,p.predictive_context,
       c.timezone,c.unit,f.winner_band_id as winning_band_id,f.settled_at,f.banked_at as label_received_at
from public.prediction_checkpoints p join public.fact_checkpoint_outcome f using(checkpoint_id)
join public.cities c on c.city_key=p.city_key
where coalesce(p.predictive_context->>'raw_model_version',p.priced_from) like 'wxrd3:%'
  and p.frozen_at is not null and p.raw_probs is not null and p.baseline_probs is not null
  and p.input_as_of<=p.frozen_at and p.frozen_at<((p.target_date+1)::timestamp at time zone c.timezone)
  and f.settled_at>p.frozen_at and clock_timestamp()>=((p.target_date+1)::timestamp at time zone c.timezone)
  and not exists(select 1 from public.prediction_checkpoints q where q.city_key=p.city_key
    and q.target_date=p.target_date and q.checkpoint=p.checkpoint
    and (q.decided_at,q.checkpoint_id)<(p.decided_at,p.checkpoint_id));
revoke all on public.v_city_predictor_pairs from public,anon,authenticated;
grant select on public.v_city_predictor_pairs to service_role;
