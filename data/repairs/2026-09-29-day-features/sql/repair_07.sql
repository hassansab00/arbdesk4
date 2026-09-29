-- derived_city_day_features repair, block 7: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$nyc|2026-07-27|23|28.89|22.78|6.11|24.44|17.22|7.22|64.15|~|4.45|~|~|8.88|12|0.110|-0.3192|0.7244
nyc|2026-07-28|24|26.67|22.22|4.45|23.89|21.11|2.78|84.49|~|2.78|~|~|12.78|18|0.250|-0.1235|0.9590
nyc|2026-07-29|24|27.22|21.11|6.11|21.67|19.44|2.23|87.21|~|5.55|~|~|9.44|15|0.490|0.6632|-0.6869
nyc|2026-07-30|24|24.44|20.56|3.88|22.78|18.89|3.89|78.74|~|1.66|~|~|8.11|11|0.180|-0.5517|-0.7967
panama_city|2026-03-18|24|32|24|8|26|24|2|88.78|~|6|~|~|6.78|10|0|0.2056|0.0541
panama_city|2026-06-18|22|31|24|7|25|25|0|100|~|6|~|~|3.78|7|0|0.2162|-0.5534
panama_city|2026-06-21|24|32|25|7|26|25|1|94.24|~|6|~|~|4.67|6|0|-0.1769|0.0198
panama_city|2026-06-22|23|30|26|4|27|26|1|94.29|~|3|~|~|6.88|9|0|0.7485|-0.6177
panama_city|2026-06-23|22|30|25|5|26|24|2|88.78|~|4|~|~|5.71|10|0|0.7193|-0.6544
panama_city|2026-06-24|24|33|26|7|28|25|3|83.82|~|5|~|~|6.89|14|0|0.4341|-0.5952
panama_city|2026-07-25|23|33|25|8|27|25|2|88.86|~|6|~|~|8.22|11|0|0.7329|-0.6369
panama_city|2026-07-27|23|32|25|7|27|25|2|88.86|~|5|~|~|4.78|7|0|0.4602|-0.2847
panama_city|2026-07-28|24|32|26|6|27|25|2|88.86|~|5|~|~|5.22|7|0|0.5636|-0.5505
panama_city|2026-07-29|24|33|26|7|28|25|3|83.82|~|5|~|~|11.44|14|0|0.3431|-0.9327
panama_city|2026-07-30|23|33|26|7|28|25|3|83.82|~|5|~|~|9.67|13|0|0.6397|-0.7120
paris|2026-03-18|24|16|6|10|8|5|3|81.33|~|8|~|~|15.67|19|0|-0.8832|-0.4545
paris|2026-06-18|24|36|17|19|20|15|5|72.95|~|16|~|~|4.67|8|0|-0.2832|0.8107
paris|2026-06-21|24|36|20|16|22|18|4|78.08|~|14|~|~|3.44|4|0|0.1749|-0.9170
paris|2026-06-22|24|37|23|14|26|21|5|74|~|11|~|~|7.22|9|0|-0.8353|-0.5003
paris|2026-06-23|22|35|23|12|25|20|5|73.83|~|10|~|~|8.43|17|0|-0.5689|-0.7400
paris|2026-06-24|24|41|24|17|28|16|12|48.13|~|13|~|~|4.11|6|0|-0.2184|0.8957
paris|2026-07-25|24|30|16|14|17|13|4|77.31|~|13|~|~|4.44|8|0|0.4138|-0.7718
paris|2026-07-27|24|27|16|11|17|14|3|82.51|~|10|~|~|6.67|9|0|0.8308|-0.3673
paris|2026-07-28|24|32|15|17|17|11|6|67.76|~|15|~|~|3.11|4|0|~|~
paris|2026-07-29|24|38|17|21|21|13|8|60.24|~|17|~|~|5.33|9|0|0.8365|0.5109
paris|2026-07-30|24|30|20|10|21|13|8|60.24|~|9|~|~|6.11|14|0|0.5710|0.2842
paris|2026-07-31|24|28|19|9|19|15|4|77.62|~|9|~|~|6|8|0|0.8115|-0.1104
qingdao|2026-03-18|24|10|1|9|7|3|4|75.67|~|3|~|~|11.88|15.55|0|-0.0812|-0.9449
qingdao|2026-06-19|24|25|22|3|23|21|2|88.53|~|2|~|~|7.78|9.72|0|-0.5105|0.8270
qingdao|2026-06-21|24|29|22|7|25|17|8|61.19|~|4|~|~|7.78|9.72|0|-0.1292|-0.9794
qingdao|2026-06-22|24|28|21|7|24|21|3|83.36|~|4|~|~|7.56|11.66|0|-0.3172|0.8738
qingdao|2026-06-23|22|28|18|10|24|19|5|73.66|~|4|~|~|11.23|13.61|0|-0.1345|0.9778
qingdao|2026-06-24|24|30|19|11|23|19|4|78.23|~|7|~|~|7.34|11.66|0|-0.5558|0.7177
qingdao|2026-07-25|24|31|24|7|28|25|3|83.82|~|3|~|~|6.48|11.66|0|-0.3487|0.1033
qingdao|2026-07-27|24|32|25|7|28|28|0|100|~|4|~|~|5.18|7.78|0|-0.2452|0.8735
qingdao|2026-07-28|24|33|25|8|29|27|2|89.02|~|4|~|~|9.29|11.66|0|-0.2485|0.9423
qingdao|2026-07-29|24|33|27|6|30|27|3|84.04|~|3|~|~|9.94|13.61|0|-0.2883|0.9439
qingdao|2026-07-30|24|34|27|7|30|27|3|84.04|~|4|~|~|11.66|17.49|0|-0.4512|0.8814
qingdao|2026-07-31|24|36|28|8|32|26|6|70.72|~|4|~|~|10.37|11.66|0|0.4119|0.8753
san_francisco|2026-03-18|24|26.67|12.78|13.89|16.67|13.33|3.34|80.7|~|10|~|~|7.33|17|0|0.4627|-0.5146
san_francisco|2026-06-18|24|21.67|15|6.67|17.78|12.22|5.56|69.94|~|3.89|~|~|14.67|21|0|0.5662|0.7996
san_francisco|2026-06-22|24|18.89|13.33|5.56|13.89|11.67|2.22|86.46|~|5|~|~|16.56|21|0|0.9095|-0.3757
san_francisco|2026-06-23|22|20.56|12.78|7.78|14.44|11.11|3.33|80.39|~|6.12|~|~|14.78|20|0|0.8807|-0.4314
san_francisco|2026-06-24|24|21.11|12.78|8.33|15|10.56|4.44|74.75|~|6.11|~|~|14|21|0|0.7754|-0.4525
san_francisco|2026-07-25|24|21.11|15.56|5.55|18.33|13.33|5|72.64|~|2.78|~|~|20.89|24|0|0.9884|-0.1330
san_francisco|2026-07-27|24|20.56|14.44|6.12|15.56|12.78|2.78|83.54|~|5|~|~|16.67|19|0|0.9723|-0.2102
san_francisco|2026-07-28|24|21.11|15|6.11|15.56|13.33|2.23|86.62|~|5.55|~|~|17.89|23|0|0.9714|-0.1848
san_francisco|2026-07-29|24|20.56|14.44|6.12|16.11|12.78|3.33|80.62|~|4.45|~|~|14.89|19|0|0.9655|-0.2387
san_francisco|2026-07-30|24|22.78|14.44|8.34|17.78|12.22|5.56|69.94|~|5|~|~|16|20|0|0.9073|-0.3052
sao_paulo|2026-03-18|24|30|18|12|22|19|3|83.12|~|8|~|~|4.33|7|0|-0.5000|-0.0575
sao_paulo|2026-06-18|24|19|9|10|11|11|0|100|~|8|~|~|7.33|10|0|-0.9494|-0.1406
sao_paulo|2026-06-21|24|23|11|12|14|12|2|87.75|~|9|~|~|4.78|8|0|0.7801|-0.0252
sao_paulo|2026-06-22|24|27|10|17|12|12|0|100|~|15|~|~|9.78|14|0|-0.1802|-0.7580
sao_paulo|2026-06-23|22|22|14|8|19|15|4|77.62|~|3|~|~|6.71|9|0|-0.0927|-0.3696
sao_paulo|2026-06-24|24|14|11|3|13|12|1|93.65|~|1|~|~|2.67|5|0|-0.1029|0.7020
sao_paulo|2026-07-25|24|21|15|6|15|14|1|93.74|~|6|~|~|5.56|10|0|0.6726|-0.1501
sao_paulo|2026-07-27|24|28|11|17|13|13|0|100|~|15|~|~|4.33|6|0|0.1834|-0.4141
seattle|2026-03-18|24|13.33|10|3.33|11.11|10|1.11|92.86|~|2.22|~|~|10.11|12|0.200|0.4016|0.8966
seattle|2026-06-18|24|25.56|13.89|11.67|17.78|8.89|8.89|56.01|~|7.78|~|~|9.67|15|0|0.2424|-0.9129
seattle|2026-06-22|24|29.44|13.89|15.55|21.11|10.56|10.55|50.92|~|8.33|~|~|10.22|14|0|0.2790|-0.8857$blob$;
  v_n int;
begin
  if md5(v_blob) <> '6e9660e29d5227adcd047d0f906f62f7' then
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
     is distinct from 'd4a8c63b6f63d9a1871087fec4a89631' then
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
