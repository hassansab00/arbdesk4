-- derived_city_day_features repair, block 1: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$amsterdam|2026-03-18|24|15|7|8|10|5|5|71.06|~|5|~|~|10.44|13|0|-0.7178|0.6454
amsterdam|2026-06-18|24|28|16|12|21|17|4|77.93|~|7|~|~|6.44|9|0|0.6726|0.5462
amsterdam|2026-06-21|24|26|19|7|23|19|4|78.23|~|3|~|~|6.89|8|0|-0.7883|-0.5464
amsterdam|2026-06-22|24|25|17|8|20|13|7|64.07|~|5|~|~|10.56|12|0|-0.7934|-0.5925
amsterdam|2026-06-23|22|28|17|11|19|14|5|72.76|~|9|~|~|4.71|5|0|-0.8216|-0.4692
amsterdam|2026-06-24|24|34|21|13|26|19|7|65.39|~|8|~|~|4.78|6|0|-0.4305|0.7048
amsterdam|2026-07-25|24|25|15|10|21|14|7|64.3|~|4|~|~|11.78|15|0|0.9806|0.0454
amsterdam|2026-07-27|24|21|15|6|18|15|3|82.63|~|3|~|~|14.89|17|0|0.9596|-0.2389
amsterdam|2026-07-28|24|27|14|13|19|15|4|77.62|~|8|~|~|8.78|11|0|0.7509|0.3745
amsterdam|2026-07-29|24|32|18|14|23|15|8|60.72|~|9|~|~|7|9|0|0.6046|0.6183
amsterdam|2026-07-30|24|26|19|7|23|18|5|73.48|~|3|~|~|7.89|11|0|0.8863|-0.1674
amsterdam|2026-07-31|24|24|16|8|21|18|3|83|~|3|~|~|9.67|13|0|0.0591|-0.9353
ankara|2026-03-18|24|15|3|12|9|2|7|61.5|~|6|~|~|6|9|0|-0.4962|0.0089
ankara|2026-06-18|24|30|15|15|23|12|11|49.95|~|7|~|~|8.56|23|0|-0.6465|-0.6648
ankara|2026-06-21|24|25|13|12|19|10|9|55.91|~|6|~|~|10.33|13|0|-0.8603|-0.4760
ankara|2026-06-22|24|26|11|15|20|9|11|49.12|~|6|~|~|14.22|18|0|-0.8941|-0.4079
ankara|2026-06-23|22|26|10|16|18|7|11|48.57|~|8|~|~|10.29|12|0|-0.6448|-0.7345
ankara|2026-06-24|24|29|13|16|20|10|10|52.54|~|9|~|~|7|10|0|-0.8242|-0.4419
ankara|2026-07-25|24|17|14|3|15|12|3|82.26|~|2|~|~|6.44|8|0|0.6005|0.2767
ankara|2026-07-27|24|28|11|17|18|10|8|59.52|~|10|~|~|4.67|7|0|0.8082|0.0814
ankara|2026-07-28|24|30|13|17|21|12|9|56.42|~|9|~|~|9|14|0|-0.5962|-0.7501
ankara|2026-07-29|24|28|15|13|21|9|12|46.19|~|7|~|~|15.22|18|0|-0.6556|-0.7036
ankara|2026-07-30|24|27|11|16|17|7|10|51.73|~|10|~|~|12.67|15|0|-0.9116|-0.3723
ankara|2026-07-31|24|26|12|14|18|9|9|55.65|~|8|~|~|14.56|18|0|-0.9429|-0.2739
atlanta|2026-03-18|24|13.33|1.11|12.22|3.89|-7.78|11.67|42.25|~|9.44|~|~|2.78|7|0|-0.5483|0.2236
atlanta|2026-06-18|24|30.56|22.22|8.34|25.56|22.22|3.34|81.86|~|5|~|~|11.22|20|1.730|0.7318|0.6226
atlanta|2026-06-21|24|30|21.67|8.33|22.22|20.56|1.66|90.3|~|7.78|~|~|5.22|8|0|0.8102|0.2000
atlanta|2026-06-22|24|31.67|23.33|8.34|25|22.22|2.78|84.61|~|6.67|~|~|11.44|18|0.070|0.9029|0.3904
atlanta|2026-06-23|22|28.33|21.11|7.22|23.33|18.89|4.44|76.14|~|5|~|~|14.57|17|0.700|0.8072|-0.5840
atlanta|2026-06-24|24|30|18.33|11.67|22.78|16.67|6.11|68.46|~|7.22|~|~|5.44|8|0|-0.2833|0.5965
atlanta|2026-07-25|24|31.11|23.33|7.78|24.44|22.78|1.66|90.46|~|6.67|~|~|6.78|10|0.210|0.8909|-0.2958
atlanta|2026-07-27|24|32.78|25|7.78|27.22|25|2.22|87.71|~|5.56|~|~|7|15|0.250|0.8391|-0.4312
atlanta|2026-07-28|24|34.44|25.56|8.88|26.11|23.33|2.78|84.73|~|8.33|~|~|8.89|14|0|0.9833|-0.0257
atlanta|2026-07-29|24|31.67|23.33|8.34|26.67|24.44|2.23|87.66|~|5|~|~|8.33|11|0.930|0.7045|-0.6725
atlanta|2026-07-30|24|31.67|23.33|8.34|26.11|20|6.11|69.12|~|5.56|~|~|6.33|9|0|0.5690|-0.1702
austin|2026-03-18|24|25|2.78|22.22|8.33|2.78|5.55|68.01|~|16.67|~|~|11.33|15|0|0.1326|0.9349
austin|2026-06-18|24|36.11|23.89|12.22|26.11|25|1.11|93.63|~|10|~|~|10.89|14|0|-0.2647|0.9271
austin|2026-06-21|24|33.89|23.33|10.56|25.56|23.89|1.67|90.54|~|8.33|~|~|13.56|18|0|0.0342|0.9759
austin|2026-06-22|24|34.44|26.11|8.33|27.78|24.44|3.34|82.14|~|6.66|~|~|12.67|16|0|-0.1420|0.9629
austin|2026-06-23|22|33.89|25.56|8.33|26.67|25|1.67|90.61|~|7.22|~|~|11.25|16|0|-0.1068|0.9661
austin|2026-06-24|24|33.89|24.44|9.45|26.67|23.33|3.34|82|~|7.22|~|~|10.78|13|0|-0.0221|0.9479
austin|2026-07-25|24|35.56|23.33|12.23|26.67|23.33|3.34|82|~|8.89|~|~|8.11|11|0|0.0597|0.9436
austin|2026-07-27|23|36.67|23.33|13.34|24.44|23.89|0.55|96.73|~|12.23|~|~|9.89|13|0|-0.0536|0.9655
austin|2026-07-28|24|36.11|24.44|11.67|27.22|22.78|4.44|76.76|~|8.89|~|~|9.56|13|0|0.0675|0.9369
austin|2026-07-29|24|36.67|24.44|12.23|27.78|24.44|3.34|82.14|~|8.89|~|~|9|12|0|0.2613|0.9371
austin|2026-07-30|24|37.78|24.44|13.34|27.22|23.89|3.33|82.07|~|10.56|~|~|9.89|13|0|0.1340|0.9577
beijing|2026-03-18|24|13|4|9|5|-21|26|13.2|~|8|~|~|14.47|19.44|0|0.9037|-0.2592
beijing|2026-06-19|24|29|21|8|26|23|3|83.59|~|3|~|~|6.05|9.72|0|-0.1693|0.5207
beijing|2026-06-21|24|30|13|17|23|13|10|53.33|~|7|~|~|8.21|13.61|0|0.1245|0.9571
beijing|2026-06-22|24|31|17|14|23|16|7|64.74|~|8|~|~|9.50|13.61|0|0.1902|0.9486
beijing|2026-06-23|22|27|18|9|21|17|4|77.93|~|6|~|~|3.89|7.78|0|0.1775|0.0173
beijing|2026-06-24|24|30|19|11|23|20|3|83.24|~|7|~|~|5.18|7.78|0|-0.2429|0.9356
beijing|2026-07-25|24|31|23|8|27|25|2|88.86|~|4|~|~|3.67|5.83|0|-0.5120|0.8039
beijing|2026-07-27|24|34|25|9|28|24|4|78.96|~|6|~|~|6.70|9.72|0|-0.1235|0.6566
beijing|2026-07-28|24|35|26|9|29|26|3|83.93|~|6|~|~|6.48|7.78|0|-0.2974|0.9014
beijing|2026-07-29|24|35|26|9|30|26|4|79.24|~|5|~|~|8.21|9.72|0|-0.0303|0.9805
beijing|2026-07-30|24|35|27|8|30|26|4|79.24|~|5|~|~|8.43|11.66|0|-0.0345|0.9605
beijing|2026-07-31|24|36|27|9|29|27|2|89.02|~|7|~|~|6.91|9.72|0|-0.0055|0.9149
buenos_aires|2026-03-18|24|29|14|15|15|13|2|87.84|~|14|~|~|9.56|13|0|-0.7313|0.3258
buenos_aires|2026-06-18|24|12|9|3|10|7|3|81.6|~|2|~|~|7|12|0|-0.8477|-0.4799$blob$;
  v_n int;
begin
  if md5(v_blob) <> '6043310d0367c7b3d7b561e07f31f750' then
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
     is distinct from '40bbeccae394cf0cf09aaa55ea9ee136' then
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
