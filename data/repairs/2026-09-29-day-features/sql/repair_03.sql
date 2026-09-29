-- derived_city_day_features repair, block 3: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$chongqing|2026-07-27|24|40|28|12|33|21|12|49.47|~|7|~|~|8.86|11.66|0|-0.8760|-0.2068
chongqing|2026-07-28|24|36|26|10|29|22|7|66.03|~|7|~|~|5.83|9.72|0|-0.2793|-0.0336
chongqing|2026-07-29|24|39|26|13|29|23|6|70.16|~|10|~|~|7.78|9.72|0|-0.5498|0.7664
chongqing|2026-07-30|24|39|30|9|30|20|10|55.13|~|9|~|~|10.37|11.66|0|-0.6261|0.7516
chongqing|2026-07-31|24|38|30|8|31|20|11|52.08|~|7|~|~|6.70|9.72|0|-0.7635|0.5382
dallas|2026-03-18|24|23.33|8.33|15|9.44|1.67|7.77|58.28|~|13.89|~|~|10.22|15|0|0.3884|0.8489
dallas|2026-06-18|24|34.44|25|9.44|27.78|22.78|5|74.31|~|6.66|~|~|10.56|14|0|-0.5617|0.7919
dallas|2026-06-21|24|33.33|24.44|8.89|26.67|21.67|5|74.12|~|6.66|~|~|10.56|13|0|0.1099|0.9583
dallas|2026-06-22|24|34.44|23.33|11.11|25|22.22|2.78|84.61|~|9.44|~|~|9.33|11|0.270|-0.1739|0.8625
dallas|2026-06-23|21|33.89|26.11|7.78|26.67|23.89|2.78|84.79|~|7.22|~|~|8|12|0|0.2142|0.9341
dallas|2026-06-24|24|34.44|26.11|8.33|27.78|23.33|4.45|76.84|~|6.66|~|~|7.89|10|0|0.3497|0.8900
dallas|2026-07-25|24|36.67|27.78|8.89|28.89|20.56|8.33|60.82|~|7.78|~|~|8.22|11|0|0.6256|0.7079
dallas|2026-07-27|23|37.22|27.78|9.44|28.33|20.56|7.77|62.81|~|8.89|~|~|7.22|10|0|0.3129|0.8632
dallas|2026-07-28|24|37.22|27.22|10|28.33|20.56|7.77|62.81|~|8.89|~|~|7.22|11|0|-0.2191|0.8174
dallas|2026-07-29|24|36.67|28.33|8.34|28.89|22.22|6.67|67.36|~|7.78|~|~|6.44|10|0|-0.3257|0.9120
dallas|2026-07-30|24|37.22|27.78|9.44|28.89|21.67|7.22|65.11|~|8.33|~|~|6.56|11|0|0.0991|0.9158
dc|2026-03-18|24|5|-2.78|7.78|-1.67|-11.67|10|46.39|~|6.67|~|~|6.33|10|0|-0.2517|0.7177
dc|2026-06-18|24|31.67|23.89|7.78|25.56|17.78|7.78|62.18|~|6.11|~|~|14.44|20|0|0.6715|0.7146
dc|2026-06-21|24|31.11|22.78|8.33|26.11|14.44|11.67|48.65|~|5|~|~|8.33|11|0|0.7121|-0.3745
denver|2026-03-18|24|27.22|7.22|20|15.28|-7.61|22.89|19.91|~|11.94|~|~|9.78|14|0|0.8930|-0.2623
denver|2026-06-18|24|30.89|11.67|19.22|20.39|1.78|18.61|29.03|~|10.50|~|~|6.78|13|0|0.0900|0.0394
denver|2026-06-21|24|30.72|14.44|16.28|22|11.39|10.61|50.97|~|8.72|~|~|6.33|10|0.010|-0.0514|-0.3778
denver|2026-06-22|23|31.67|12.39|19.28|20.78|8.72|12.06|45.95|~|10.89|~|~|6.89|10|0|0.3767|-0.1780
denver|2026-06-23|22|27.78|15.22|12.56|20.78|12|8.78|57.19|~|7|~|~|9.33|13|0|-0.1864|-0.9628
denver|2026-06-24|24|30.39|14.44|15.95|21.28|14|7.28|63.21|~|9.11|~|~|8.78|14|0.010|0.1691|-0.6505
denver|2026-07-25|24|38.61|21.11|17.50|28.11|11.67|16.44|36.09|~|10.50|~|~|10.11|18|0|-0.0048|0.9708
denver|2026-07-27|24|33.33|22.11|11.22|26.78|12.78|14|41.97|~|6.55|~|~|9.56|13|0.010|-0.4395|-0.7985
denver|2026-07-28|24|33.5|20|13.5|26.11|14.44|11.67|48.65|~|7.39|~|~|9.67|17|0|0.0919|-0.0598
denver|2026-07-29|24|32.22|20|12.22|26.78|13.22|13.56|43.21|~|5.44|~|~|7.89|19|0.070|-0.1755|-0.1780
denver|2026-07-30|24|34.72|18.72|16|27.5|10.72|16.78|35.13|~|7.22|~|~|9.89|18|0|0.1801|-0.6444
guangzhou|2026-03-18|24|29|19|10|21|18|3|83|~|8|~|~|6.48|7.78|0|0.1229|0.9335
guangzhou|2026-06-19|24|31|26|5|27|26|1|94.29|~|4|~|~|8.42|11.66|0|-0.3064|0.9057
guangzhou|2026-06-21|24|36|28|8|30|26|4|79.24|~|6|~|~|8.43|9.72|0|-0.2349|0.9547
guangzhou|2026-06-22|24|36|29|7|31|26|5|74.84|~|5|~|~|7.78|9.72|0|-0.0339|0.9346
guangzhou|2026-06-23|22|36|29|7|31|26|5|74.84|~|5|~|~|8.64|11.66|0|-0.2477|0.8537
guangzhou|2026-06-24|24|37|29|8|32|27|5|75.01|~|5|~|~|7.13|11.66|0|0.0080|0.9114
guangzhou|2026-07-25|24|37|26|11|31|25|6|70.53|~|6|~|~|8.21|17.49|0|-0.1611|-0.8447
guangzhou|2026-07-27|24|32|25|7|27|26|1|94.29|~|5|~|~|7.56|9.72|0|-0.4396|0.3910
guangzhou|2026-07-28|24|31|26|5|27|25|2|88.86|~|4|~|~|5.62|11.66|0|-0.6138|0.4452
guangzhou|2026-07-29|24|31|26|5|27|25|2|88.86|~|4|~|~|8.86|11.66|0|-0.8200|0.3615
guangzhou|2026-07-30|24|31|26|5|28|26|2|88.94|~|3|~|~|5.62|9.72|0|-0.6701|-0.3426
guangzhou|2026-07-31|24|32|25|7|26|25|1|94.24|~|6|~|~|4.75|9.72|0|-0.3318|0.5383
helsinki|2026-03-18|24|2|-1|3|0|-1|1|92.97|~|2|~|~|13.11|15|0|0.4634|0.8791
helsinki|2026-06-18|24|22|11|11|16|10|6|67.55|~|6|~|~|8.11|10|0|0.9099|0.0088
helsinki|2026-06-21|24|27|18|9|21|17|4|77.93|~|6|~|~|9.89|12|0|0.9557|0.0990
helsinki|2026-06-22|24|23|13|10|18|9|9|55.65|~|5|~|~|9.33|14|0|0.6032|-0.7144
helsinki|2026-06-23|22|19|9|10|15|6|9|54.86|~|4|~|~|11.14|14|0|0.9474|-0.2982
helsinki|2026-06-24|24|19|12|7|14|13|1|93.7|~|5|~|~|7|8|0|-0.1276|-0.4806
helsinki|2026-07-25|24|19|12|7|17|12|5|72.4|~|2|~|~|13.22|16|0|0.1014|0.9870
helsinki|2026-07-27|24|20|15|5|17|16|1|93.84|~|3|~|~|8.44|9|0|-0.4091|0.8582
helsinki|2026-07-28|24|20|14|6|15|13|2|87.84|~|5|~|~|11.22|15|0|0.8330|0.2777
helsinki|2026-07-29|24|24|12|12|17|12|5|72.4|~|7|~|~|8.78|12|0|0.9045|-0.1526
helsinki|2026-07-30|24|24|13|11|17|15|2|88.01|~|7|~|~|7.22|9|0|0.4062|0.8616
helsinki|2026-07-31|24|26|15|11|21|17|4|77.93|~|5|~|~|10.56|14|0|0.0497|0.9374
houston|2026-03-18|24|21.67|7.22|14.45|13.33|7.22|6.11|66.48|~|8.34|~|~|10.11|14|0|-0.1038|0.9740
houston|2026-06-18|24|34.44|25.56|8.88|29.44|26.11|3.33|82.35|~|5|~|~|9.56|12|0|-0.0291|0.9704
houston|2026-06-21|24|31.11|24.44|6.67|24.44|23.33|1.11|93.55|~|6.67|~|~|10.11|16|0.080|-0.2517|0.8758
houston|2026-06-22|24|33.33|27.22|6.11|30|26.11|3.89|79.76|~|3.33|~|~|12.67|16|0|-0.1134|0.9550
houston|2026-06-23|22|33.89|26.11|7.78|27.78|25|2.78|84.91|~|6.11|~|~|9.75|12|0|-0.0214|0.9690
houston|2026-06-24|24|33.89|25.56|8.33|28.89|25|3.89|79.61|~|5|~|~|8.22|11|0|0.4203|0.8954$blob$;
  v_n int;
begin
  if md5(v_blob) <> 'c189d109436afbb38fcc176bce8289d2' then
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
     is distinct from 'b1d21f6334ff5b3b22a0746259fa7cf4' then
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
