-- derived_city_day_features repair, block 4: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$houston|2026-07-25|24|33.33|25.56|7.77|28.89|24.44|4.45|77.01|~|4.44|~|~|7|12|0|0.1004|0.9153
houston|2026-07-27|23|34.44|25.56|8.88|27.22|25|2.22|87.71|~|7.22|~|~|8|11|0|0.3740|0.8913
houston|2026-07-28|24|35|25|10|28.89|24.44|4.45|77.01|~|6.11|~|~|9.67|14|0|0.4080|0.8668
houston|2026-07-29|24|35|25.56|9.44|28.89|25|3.89|79.61|~|6.11|~|~|8.67|13|0|0.4316|0.8156
houston|2026-07-30|24|35.56|26.11|9.45|28.89|25|3.89|79.61|~|6.67|~|~|6.89|12|0|0.1341|0.8896
istanbul|2026-03-18|24|9|7|2|8|5|3|81.33|~|1|~|~|19.33|22|0|-0.6371|-0.7670
istanbul|2026-06-18|24|23|19|4|22|16|6|68.79|~|1|~|~|14.44|17|0|-0.5828|-0.7995
istanbul|2026-06-21|24|25|19|6|23|17|6|68.99|~|2|~|~|11.67|14|0|-0.5772|-0.7914
istanbul|2026-06-22|24|26|21|5|23|18|5|73.48|~|3|~|~|12.78|16|0|-0.5151|-0.8315
istanbul|2026-06-23|22|26|21|5|23|17|6|68.99|~|3|~|~|13|15|0|-0.6579|-0.7356
istanbul|2026-06-24|24|26|22|4|24|18|6|69.19|~|2|~|~|14.56|16|0|-0.4343|-0.8832
istanbul|2026-07-25|24|23|18|5|23|16|7|64.74|~|0|~|~|14.33|24|0|-0.2673|-0.8425
istanbul|2026-07-27|24|31|19|12|24|17|7|64.96|~|7|~|~|11.67|13|0|0.4996|0.8415
istanbul|2026-07-28|24|29|24|5|28|16|12|48.13|~|1|~|~|14|18|0|-0.1232|-0.9527
istanbul|2026-07-29|24|27|24|3|25|17|8|61.19|~|2|~|~|17.44|19|0|-0.8613|-0.4864
istanbul|2026-07-30|24|26|23|3|25|14|11|50.49|~|1|~|~|21.44|22|0|-0.8220|-0.5629
istanbul|2026-07-31|24|26|22|4|25|15|10|53.86|~|1|~|~|22.11|24|0|-0.7439|-0.6561
jakarta|2026-03-18|24|34|25|9|27|24|3|83.7|~|7|~|~|4.22|7|0|0.4062|-0.4497
jakarta|2026-06-21|24|34|25|9|28|25|3|83.82|~|6|~|~|7.44|13|0|-0.6697|-0.5706
jeddah|2026-03-18|24|36|26|10|27|18|9|57.91|~|9|~|~|8.44|14|0|0.4361|0.6886
jeddah|2026-06-18|24|39|29|10|32|22|10|55.64|~|7|~|~|15.89|19|0|0.5461|-0.8200
jeddah|2026-06-21|24|35|29|6|31|23|8|62.56|~|4|~|~|7.11|9|0|0.9726|-0.0060
jeddah|2026-06-22|24|34|28|6|30|24|6|70.35|~|4|~|~|6.67|10|0|0.9644|-0.1637
jeddah|2026-06-23|22|34|29|5|31|24|7|66.44|~|3|~|~|8.38|13|0|0.9583|-0.1914
jeddah|2026-06-24|24|36|29|7|31|23|8|62.56|~|5|~|~|11.11|15|0|0.8669|-0.3619
jeddah|2026-07-25|24|40|30|10|33|21|12|49.47|~|7|~|~|8.78|13|0|0.9151|-0.2187
jeddah|2026-07-27|24|42|28|14|31|24|7|66.44|~|11|~|~|9.22|12|0|0.7161|-0.6499
jeddah|2026-07-28|24|37|29|8|31|28|3|84.15|~|6|~|~|10.78|13|0|0.8744|-0.3916
jeddah|2026-07-29|24|40|31|9|32|27|5|75.01|~|8|~|~|8|10|0|0.8125|-0.5290
jeddah|2026-07-30|24|42|30|12|34|16|18|34.21|~|8|~|~|8.11|12|0|0.8003|-0.4035
jeddah|2026-07-31|24|41|30|11|33|25|8|63.01|~|8|~|~|7.11|9|0|0.9201|-0.2555
karachi|2026-03-18|24|30|19|11|24|21|3|83.36|~|6|~|~|9.56|12|0|-0.2266|0.8021
karachi|2026-06-18|24|34|30|4|31|24|7|66.44|~|3|~|~|16.33|20|0|0.8148|0.5290
karachi|2026-06-21|24|34|29|5|30|24|6|70.35|~|4|~|~|15.89|18|0|0.8267|0.5340
karachi|2026-06-22|24|35|29|6|31|24|7|66.44|~|4|~|~|19.33|24|0|0.8586|0.4557
karachi|2026-06-23|22|34|29|5|31|24|7|66.44|~|3|~|~|16.78|22|0|0.9384|0.2972
karachi|2026-06-24|24|34|29|5|30|24|6|70.35|~|4|~|~|20|22|0|0.9554|0.2376
karachi|2026-07-25|24|33|26|7|30|25|5|74.68|~|3|~|~|14.33|17|0|0.9261|0.3657
karachi|2026-07-27|24|34|29|5|30|24|6|70.35|~|4|~|~|15.89|23|0|0.8791|0.4104
karachi|2026-07-28|24|33|28|5|29|24|5|74.51|~|4|~|~|17.11|22|0|0.8440|0.5198
karachi|2026-07-29|24|34|29|5|30|25|5|74.68|~|4|~|~|14.44|19|0|0.9172|0.3016
karachi|2026-07-30|24|35|29|6|29|25|4|79.1|~|6|~|~|10.78|16|0|0.8260|0.4718
karachi|2026-07-31|24|35|29|6|30|24|6|70.35|~|5|~|~|11|15|0|0.7765|0.5135
kuala_lumpur|2026-03-18|24|33|24|9|25|23|2|88.7|~|8|~|~|3.44|6|0|~|~
kuala_lumpur|2026-06-19|24|31|25|6|26|24|2|88.78|~|5|~|~|4.33|6|0|~|~
kuala_lumpur|2026-06-21|24|33|26|7|27|24|3|83.7|~|6|~|~|3.44|6|0|~|~
kuala_lumpur|2026-06-22|24|33|26|7|26|24|2|88.78|~|7|~|~|4.56|7|0|0.7476|-0.6200
kuala_lumpur|2026-06-23|22|33|26|7|27|25|2|88.86|~|6|~|~|2.56|5|0|~|~
kuala_lumpur|2026-06-24|24|32|24|8|26|25|1|94.24|~|6|~|~|4.11|6|0|0.3105|-0.5300
kuala_lumpur|2026-07-25|24|30|26|4|26|25|1|94.24|~|4|~|~|2.44|5|0|-0.5793|0.8105
kuala_lumpur|2026-07-27|24|32|27|5|27|24|3|83.7|~|5|~|~|5.11|6|0|-0.7660|0.6428
kuala_lumpur|2026-07-28|24|31|27|4|28|23|5|74.34|~|3|~|~|5.33|7|0|-0.6803|0.7077
kuala_lumpur|2026-07-29|24|31|25|6|26|24|2|88.78|~|5|~|~|4.11|5|0|-0.6886|0.6547
kuala_lumpur|2026-07-30|24|33|25|8|25|24|1|94.2|~|8|~|~|4.44|6|0|~|~
kuala_lumpur|2026-07-31|24|32|25|7|26|25|1|94.24|~|6|~|~|3.44|6|0|~|~
lagos|2026-06-18|16|31|25|6|26|25|1|94.24|~|5|~|~|5.83|8|0|0.4371|0.7818
lagos|2026-06-21|24|31|23|8|24|24|0|100|~|7|~|~|4.67|8|0|0.1121|0.7386
london|2026-03-18|24|18|7|11|12|8|4|76.51|~|6|~|~|9.44|13|0|-0.9080|0.1789
london|2026-06-18|23|26|17|9|19|16|3|82.76|~|7|~|~|7.22|9|0|0.2437|0.9005
london|2026-06-21|24|28|20|8|24|17|7|64.96|~|4|~|~|8.78|14|0|-0.9641|0.1116$blob$;
  v_n int;
begin
  if md5(v_blob) <> '02146eb8ea7f53197e62dba505c6a0f8' then
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
     is distinct from '8f042ae74455c51990c96176613f9fe3' then
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
