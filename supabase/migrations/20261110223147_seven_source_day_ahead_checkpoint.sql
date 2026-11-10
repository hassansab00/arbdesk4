-- Prospective all-city day-ahead calls; no original row is changed.
alter table public.prediction_checkpoints drop constraint if exists prediction_checkpoints_label;
alter table public.prediction_checkpoints add constraint prediction_checkpoints_label check (checkpoint in ('day_ahead','d1_eve','morning','noon','prepeak_2h','prepeak_1h','postpeak_1h'));
CREATE OR REPLACE FUNCTION arbdesk_private.freeze_prediction_before_outcome()
 RETURNS trigger
 LANGUAGE plpgsql
 SET search_path TO ''
AS $function$
declare
  receipt timestamptz := clock_timestamp(); tz text; plan timestamp; peak numeric;
  parent_at timestamptz; dist jsonb; best text; packet jsonb; ids jsonb;
begin
  select c.timezone into tz from public.cities c where c.city_key=new.city_key;
  if tz is null or receipt >= ((new.target_date+1)::timestamp at time zone tz) then
    raise exception 'Freeze refused: unknown city clock or completed local day';
  end if;
  if exists (select 1 from public.v_venue_market_resolution r
       where r.city_key=new.city_key and r.resolution_date=new.target_date and r.resolution_state='confirmed') then
    raise exception 'Freeze refused: venue outcome already confirmed';
  end if;
  if new.decided_at > receipt+interval '5 seconds' or (tg_table_name='prediction_checkpoints' and new.decided_at < receipt-interval '15 minutes') then
    raise exception 'Freeze refused: backdated or future decision';
  end if;
  new.frozen_at := receipt;
  if tg_table_name='prediction_checkpoints' then
    perform pg_advisory_xact_lock(hashtextextended(new.city_key||'|'||new.target_date||'|'||new.checkpoint,0));
    if exists(select 1 from public.prediction_checkpoints p where p.city_key=new.city_key
        and p.target_date=new.target_date and p.checkpoint=new.checkpoint) then return null; end if;
    plan := case new.checkpoint when 'day_ahead' then new.target_date-1+time '09:00'
              when 'd1_eve' then new.target_date-1+time '18:00'
              when 'morning' then new.target_date+time '09:00'
              when 'noon' then new.target_date+time '12:00' end;
    if plan is null then
      select p.peak_hour_local into peak from public.derived_weather_peak p
        where p.city_key=new.city_key and p.month=extract(month from new.target_date);
      if peak is null then raise exception 'Freeze refused: no measured monthly peak'; end if;
      plan := new.target_date::timestamp + round(peak*60)*interval '1 minute'
          + case new.checkpoint when 'prepeak_2h' then interval '-2 hours'
              when 'prepeak_1h' then interval '-1 hour' when 'postpeak_1h' then interval '1 hour' end;
    end if;
    if new.local_decision_time is distinct from plan
       or receipt < (plan at time zone tz) or receipt > (plan at time zone tz)+interval '75 minutes' then
      raise exception 'Freeze refused: outside scheduled city-local capture window';
    end if;
    if new.reading_at > receipt or new.forecast_issued_at > receipt then
      raise exception 'Freeze refused: future input timestamp';
    end if;
    foreach dist in array array[new.probs,new.raw_probs,new.baseline_probs] loop
      if dist is null then continue; end if;
      if jsonb_typeof(dist)<>'object' or abs(public.ad4_probs_sum(dist)-1)>1e-4
        or exists(select 1 from jsonb_each_text(dist) e where e.value is null or e.value::numeric<0 or e.value::numeric>1)
        or (select array_agg(key order by key) from jsonb_each(dist)) is distinct from
           (select array_agg(key order by key) from jsonb_each(new.probs)) then
        raise exception 'Freeze refused: invalid or mismatched probability ladder';
      end if;
    end loop;
    select key into best from jsonb_each_text(new.probs) order by value::numeric desc,key limit 1;
    if new.top_band_id is distinct from best or new.top_prob is distinct from (new.probs->>best)::numeric then
      raise exception 'Freeze refused: top pick does not match frozen probabilities';
    end if;
    -- Old writers get receipt/time guards during deployment; they cannot
    -- enter the new learner without the paired causal input evidence.
    if new.input_key is not null or new.raw_probs is not null then
      select c.payload into packet from public.live_capture c where c.kind='wx_features' and c.key=new.input_key
        and c.written_at<=receipt order by c.captured_at desc,c.capture_id desc limit 1;
      select jsonb_agg(key order by key) into ids from jsonb_each(new.probs);
      if packet is null or packet->>'policy' is distinct from 'wxrd3-horizons-v1'
        or packet->>'version' is distinct from coalesce(new.predictive_context->>'raw_model_version',new.priced_from)
        or packet->>'model_fit_through' is distinct from new.predictive_context->>'model_fit_through'
        or packet->>'row_sha256' is distinct from new.input_sha256
        or (packet->>'as_of')::timestamptz is distinct from new.input_as_of
        or packet->'row'->>'city_key' is distinct from new.city_key
        or packet->'row'->>'date' is distinct from new.target_date::text
        or packet->>'model_fit_through' is null or (packet->>'model_fit_through')::date>=new.target_date
        or new.input_as_of is null or new.input_as_of>receipt or new.input_as_of<receipt-interval '15 minutes'
        or new.raw_probs is null or new.baseline_probs is null or new.predictive_context is null
        or packet->'band_ids' is distinct from new.predictive_context->'band_ids'
        or (select jsonb_agg(value order by value) from jsonb_array_elements(packet->'band_ids')) is distinct from ids then
        raise exception 'Freeze refused: exact prior input packet or paired baseline missing';
      end if;
    end if;

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
    new.decided_at := receipt;
  else
    select p.decided_at into parent_at from public.prediction_checkpoints p where p.city_key=new.city_key
      and p.target_date=new.target_date and p.checkpoint=new.checkpoint order by p.decided_at,p.checkpoint_id limit 1;
    if parent_at is null or new.decided_at<parent_at or (receipt>parent_at+interval '30 minutes' and not (new.decided_at=parent_at and receipt<=parent_at+interval '6 hours')) then
      raise exception 'Freeze refused: shadow lacks a recent original frozen call';
    end if;
  end if;
  return new;
end $function$
;

