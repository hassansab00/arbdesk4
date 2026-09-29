-- derived_city_day_features repair, block 9: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$tel_aviv|2026-06-18|24|30|23|7|26|19|7|65.39|~|4|~|~|9.33|12|0|0.8486|-0.1848
tel_aviv|2026-06-21|24|29|24|5|26|19|7|65.39|~|3|~|~|9.78|12|0|0.7264|-0.6134
tel_aviv|2026-06-22|24|29|23|6|26|19|7|65.39|~|3|~|~|8.78|12|0|0.9241|-0.3295
tel_aviv|2026-06-23|22|30|22|8|26|19|7|65.39|~|4|~|~|9.71|12|0|0.9640|0.1167
tel_aviv|2026-06-24|24|31|22|9|27|19|8|61.65|~|4|~|~|9|12|0|0.9069|-0.0007
tel_aviv|2026-07-25|24|33|25|8|28|20|8|61.88|~|5|~|~|8.33|10|0|0.8912|0.1318
tel_aviv|2026-07-27|24|32|24|8|29|20|9|58.4|~|3|~|~|9.78|13|0|0.5803|-0.7879
tel_aviv|2026-07-28|24|32|25|7|29|20|9|58.4|~|3|~|~|10.33|13|0|0.9398|-0.2474
tel_aviv|2026-07-29|24|33|24|9|27|21|6|69.77|~|6|~|~|8.78|11|0|0.8692|0.0833
tel_aviv|2026-07-30|24|34|25|9|28|22|6|69.97|~|6|~|~|8|12|0|0.8879|-0.2435
tel_aviv|2026-07-31|24|34|24|10|28|22|6|69.97|~|6|~|~|8.11|11|0|0.7676|-0.3917
tokyo|2026-03-18|24|17|10|7|10|5|5|71.06|~|7|~|~|6.22|11|0|-0.2040|0.5294
tokyo|2026-06-19|24|28|21|7|23|20|3|83.24|~|5|~|~|7.44|12|0|-0.1861|0.7748
tokyo|2026-06-21|24|26|22|4|22|21|1|94.07|~|4|~|~|5.78|8|0|-0.1594|-0.4890
tokyo|2026-06-22|24|28|21|7|24|19|5|73.66|~|4|~|~|8.89|15|0|-0.3948|0.7103
tokyo|2026-06-23|23|23|19|4|22|17|5|73.3|~|1|~|~|11|13|0|-0.8879|-0.4228
tokyo|2026-06-24|23|25|19|6|20|16|4|77.77|~|5|~|~|5.33|11|0|-0.5881|0.2620
tokyo|2026-07-25|24|36|26|10|29|26|3|83.93|~|7|~|~|9.11|18|0|-0.6761|0.3552
tokyo|2026-07-27|24|29|25|4|25|23|2|88.7|~|4|~|~|9.22|12|0|-0.9631|-0.1679
tokyo|2026-07-28|24|30|24|6|25|22|3|83.47|~|5|~|~|11.89|18|0|0.0405|0.8960
tokyo|2026-07-29|24|34|25|9|29|22|7|66.03|~|5|~|~|10.11|14|0|0.1834|0.9674
tokyo|2026-07-30|24|35|27|8|28|25|3|83.82|~|7|~|~|7.11|12|0|-0.1944|0.7843
tokyo|2026-07-31|24|35|27|8|28|25|3|83.82|~|7|~|~|7.22|13|0|-0.5014|0.4069
toronto|2026-03-18|24|-1|-8|7|-6|-10|4|73.3|~|5|~|~|8.56|12|0|-0.1008|0.9384
toronto|2026-06-18|24|22|15|7|16|16|0|100|~|6|~|~|21|27|0|0.9476|0.1702
toronto|2026-06-21|24|24|12|12|17|9|8|59.27|~|7|~|~|5.78|9|0|-0.1261|0.4525
toronto|2026-06-22|24|19|14|5|17|12|5|72.4|~|2|~|~|5.78|8|0|-0.7323|0.3268
toronto|2026-06-23|22|24|14|10|17|12|5|72.4|~|7|~|~|11.14|14|0|0.3635|-0.8897
toronto|2026-06-24|24|24|14|10|18|11|7|63.62|~|6|~|~|6.11|9|0|0.8964|-0.2444
toronto|2026-07-25|24|26|17|9|19|13|6|68.18|~|7|~|~|6.11|10|0|-0.4627|0.8571
toronto|2026-07-27|24|29|18|11|21|18|3|83|~|8|~|~|5.89|10|0|-0.4149|0.7847
toronto|2026-07-28|24|27|20|7|21|19|2|88.36|~|6|~|~|12.11|16|0|0.3511|-0.9235
toronto|2026-07-29|24|27|19|8|20|14|6|68.38|~|7|~|~|17.33|20|0|0.2498|-0.9582
toronto|2026-07-30|24|28|18|10|20|12|8|60|~|8|~|~|9.78|13|0|0.1150|-0.9405
warsaw|2026-03-18|24|14|3|11|5|2|3|80.92|~|9|~|~|4.89|7|0|-0.4222|-0.7945
warsaw|2026-06-18|24|24|10|14|18|10|8|59.52|~|6|~|~|10|13|0|0.9783|-0.1435
warsaw|2026-06-21|24|30|18|12|24|18|6|69.19|~|6|~|~|5.67|12|0|0.4561|-0.6426
warsaw|2026-06-22|24|28|20|8|22|19|3|83.12|~|6|~|~|9.33|13|0|0.7791|-0.5873
warsaw|2026-06-23|22|26|15|11|20|14|6|68.38|~|6|~|~|6.14|9|0|0.5848|-0.5397
warsaw|2026-06-24|24|28|12|16|19|12|7|63.85|~|9|~|~|7.89|11|0|0.8945|-0.3221
warsaw|2026-07-25|24|24|12|12|18|14|4|77.46|~|6|~|~|6.44|8|0|0.6605|0.5232
warsaw|2026-07-27|24|23|15|8|22|15|7|64.52|~|1|~|~|13.56|22|0|0.9656|0.1074
warsaw|2026-07-28|24|22|12|10|15|11|4|76.99|~|7|~|~|15.11|18|0|0.9934|-0.0753
warsaw|2026-07-29|24|25|10|15|17|11|6|67.76|~|8|~|~|6.89|9|0|0.9347|-0.1986
warsaw|2026-07-30|24|31|15|16|21|15|6|68.59|~|10|~|~|7.56|9|0|-0.3084|0.9162
warsaw|2026-07-31|24|33|18|15|24|17|7|64.96|~|9|~|~|8.78|12|0|0.5972|0.3826
wellington|2026-03-18|24|21|16|5|17|14|3|82.51|~|4|~|~|19.44|24|0|-0.0142|-0.9871
wellington|2026-06-19|24|14|7|7|7|6|1|93.35|~|7|~|~|16.56|23|0|-0.2093|-0.9736
wellington|2026-06-21|24|17|10|7|13|13|0|100|~|4|~|~|6.44|12|0|-0.0314|0.9216
wellington|2026-06-22|24|14|7|7|7|6|1|93.35|~|7|~|~|3.11|6|0|-0.2831|-0.1973
wellington|2026-06-24|22|16|11|5|12|10|2|87.56|~|4|~|~|4.33|7|0|-0.2645|-0.9215
wellington|2026-07-25|24|12|9|3|10|9|1|93.5|~|2|~|~|23.67|30|0|0.2602|0.9337
wellington|2026-07-27|24|11|2|9|3|2|1|93.13|~|8|~|~|6.67|10|0|0.2425|0.4077
wellington|2026-07-28|24|13|4|9|4|2|2|86.79|~|9|~|~|4.89|7|0|-0.0695|-0.2983
wellington|2026-07-29|24|12|5|7|7|4|3|81.2|~|5|~|~|20|23|0|-0.2260|-0.9708
wellington|2026-07-30|24|14|10|4|11|8|3|81.74|~|3|~|~|21.33|27|0|-0.0361|-0.9707
wellington|2026-07-31|24|14|11|3|13|7|6|66.91|~|1|~|~|15.56|20|0|0.5203|-0.8466
wuhan|2026-03-18|24|14|9|5|9|9|0|100|~|5|~|~|2.16|3.89|0|~|~
wuhan|2026-06-19|24|28|23|5|25|25|0|100|~|3|~|~|3.67|5.83|0|0.9352|-0.0086
wuhan|2026-06-21|24|29|24|5|27|26|1|94.29|~|2|~|~|7.78|9.72|0|-0.9126|0.0965$blob$;
  v_n int;
begin
  if md5(v_blob) <> '2a240398046d2b776766caf54f376d56' then
    raise exception 'the repair values are not the ones the audit file records';
  end if;
  create temp table _repair on commit drop as
  select split_part(l, '|', 1) as city_key, split_part(l, '|', 2)::date as obs_date,
         nullif(split_part(l, '|', 3), '~')::int as n_obs,
         nullif(split_part(l, '|', 4), '~')::numeric as max_c,
         nullif(split_part(l, '|', 5), '~')::numeric as min_c,
         nullif(split_part(l, '|', 6), '~')::numeric as diurnal_range_c,
         nullif(split_part(l, '|', 7), '~')::numeric as morning_temp_c,
         nullif(split_part(l, '|', 8), '~')::numeric as morning_dewpoint_c,
         nullif(split_part(l, '|', 9), '~')::numeric as dewpoint_depression_c,
         nullif(split_part(l, '|', 10), '~')::numeric as morning_humidity,
         nullif(split_part(l, '|', 11), '~')::numeric as morning_pressure_hpa,
         nullif(split_part(l, '|', 12), '~')::numeric as morning_to_max_c,
         nullif(split_part(l, '|', 13), '~')::numeric as cloud_mean,
         nullif(split_part(l, '|', 14), '~')::numeric as cloud_max,
         nullif(split_part(l, '|', 15), '~')::numeric as wind_mean,
         nullif(split_part(l, '|', 16), '~')::numeric as wind_max,
         nullif(split_part(l, '|', 17), '~')::numeric as precip_total,
         nullif(split_part(l, '|', 18), '~')::numeric as wind_u_mean,
         nullif(split_part(l, '|', 19), '~')::numeric as wind_v_mean
    from unnest(string_to_array(v_blob, E'\n')) l;
  select count(*) into v_n from _repair;
  if v_n <> 60 then raise exception 'expected 60 repair rows, parsed %', v_n; end if;
  -- The rows still hold exactly what the audit recorded as "before".
  if (select md5(string_agg(concat_ws('|', f.city_key, f.obs_date, f.n_obs, f.max_c, f.min_c, f.diurnal_range_c, coalesce(f.prev_max_c::text, '~'), coalesce(f.delta_max_c::text, '~'), coalesce(f.morning_temp_c::text, '~'), coalesce(f.morning_dewpoint_c::text, '~'), coalesce(f.dewpoint_depression_c::text, '~'), coalesce(f.morning_humidity::text, '~'), coalesce(f.morning_pressure_hpa::text, '~'), coalesce(f.morning_to_max_c::text, '~'), coalesce(f.cloud_mean::text, '~'), coalesce(f.cloud_max::text, '~'), coalesce(f.wind_mean::text, '~'), coalesce(f.wind_max::text, '~'), coalesce(f.precip_total::text, '~'), coalesce(f.pressure_change_24h_hpa::text, '~'), coalesce(f.wind_u_mean::text, '~'), coalesce(f.wind_v_mean::text, '~'), to_char(f.computed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')), E'\n' order by f.city_key, f.obs_date))
        from public.derived_city_day_features f join _repair r using (city_key, obs_date))
     is distinct from 'f14a9f97cafc8ff1147db432f8f268f5' then
    raise exception 'these rows do not hold what the audit recorded (already repaired, or changed since)';
  end if;
  update public.derived_city_day_features f
     set n_obs = r.n_obs,
         max_c = r.max_c,
         min_c = r.min_c,
         diurnal_range_c = r.diurnal_range_c,
         morning_temp_c = r.morning_temp_c,
         morning_dewpoint_c = r.morning_dewpoint_c,
         dewpoint_depression_c = r.dewpoint_depression_c,
         morning_humidity = r.morning_humidity,
         morning_pressure_hpa = r.morning_pressure_hpa,
         morning_to_max_c = r.morning_to_max_c,
         cloud_mean = r.cloud_mean,
         cloud_max = r.cloud_max,
         wind_mean = r.wind_mean,
         wind_max = r.wind_max,
         precip_total = r.precip_total,
         wind_u_mean = r.wind_u_mean,
         wind_v_mean = r.wind_v_mean,
         computed_at = now()
    from _repair r
   where f.city_key = r.city_key and f.obs_date = r.obs_date;
  get diagnostics v_n = row_count;
  if v_n <> 60 then raise exception 'expected to repair 60 rows, updated %', v_n; end if;
end $repair$;
