-- derived_city_day_features repair, block 6: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$mexico_city|2026-06-24|24|21|15|6|17|9|8|59.27|~|4|~|~|2.44|10|0|-0.4988|0.1881
mexico_city|2026-07-25|24|26|16|10|19|11|8|59.76|~|7|~|~|8.78|18|0|0.1396|-0.7619
mexico_city|2026-07-27|24|23|13|10|17|11|6|67.76|~|6|~|~|6.67|15|0|-0.8894|-0.3710
mexico_city|2026-07-28|23|26|14|12|16|11|5|72.21|~|10|~|~|7.11|18|0|-0.6858|-0.4871
mexico_city|2026-07-29|24|26|15|11|19|12|7|63.85|~|7|~|~|9.22|15|0|-0.7751|0.0410
mexico_city|2026-07-30|24|27|15|12|18|13|5|72.58|~|9|~|~|6.67|14|0|-0.4448|-0.6599
miami|2026-03-18|24|20.56|13.33|7.23|15|12.78|2.22|86.57|~|5.56|~|~|6.67|10|0.190|0.2772|-0.8955
miami|2026-06-18|24|33.33|27.78|5.55|31.11|23.89|7.22|65.59|~|2.22|~|~|9.67|11|0|-0.5539|0.7776
miami|2026-06-21|24|34.44|24.44|10|28.89|25|3.89|79.61|~|5.55|~|~|7.44|10|0|-0.5297|0.7923
miami|2026-06-22|24|33.89|28.33|5.56|30|24.44|5.56|72.24|~|3.89|~|~|7.56|10|0|-0.6551|0.7169
miami|2026-06-23|22|32.22|27.22|5|30.56|25|5.56|72.34|~|1.66|~|~|6.14|8|0.030|-0.3242|0.7869
miami|2026-06-24|24|32.78|24.44|8.34|30.56|22.22|8.34|61.21|~|2.22|~|~|7.11|11|0|-0.6403|0.6734
miami|2026-07-25|24|32.22|28.33|3.89|28.89|25.56|3.33|82.28|~|3.33|~|~|5.89|10|0.210|0.1675|0.6758
miami|2026-07-27|24|32.22|23.89|8.33|28.33|24.44|3.89|79.53|~|3.89|~|~|7.22|19|1.830|0.5029|-0.0034
miami|2026-07-28|24|31.67|25.56|6.11|28.89|25.56|3.33|82.28|~|2.78|~|~|7.11|14|0.110|0.2600|0.8551
miami|2026-07-29|24|34.44|26.67|7.77|30|23.89|6.11|69.88|~|4.44|~|~|11.22|14|0.020|0.8724|0.4199
miami|2026-07-30|24|35.56|27.22|8.34|29.44|24.44|5|74.59|~|6.12|~|~|10.11|15|0|0.8875|0.3358
milan|2026-03-18|24|14|3|11|11|2|9|53.8|~|3|~|~|6.11|8|0|-0.0200|0.4325
milan|2026-06-18|24|33|20|13|26|19|7|65.39|~|7|~|~|4|7|0|0.6553|0.5983
milan|2026-06-21|24|34|22|12|27|19|8|61.65|~|7|~|~|5.67|8|0|0.6795|0.0262
milan|2026-06-22|24|34|22|12|28|19|9|58.16|~|6|~|~|4.22|6|0|0.4541|0.1155
milan|2026-06-23|22|34|23|11|28|21|7|65.82|~|6|~|~|5|8|0|0.9141|0.3970
milan|2026-06-24|24|34|24|10|28|21|7|65.82|~|6|~|~|5.11|8|0|0.1781|0.3833
milan|2026-07-25|24|30|17|13|24|10|14|41.18|~|6|~|~|5.22|8|0|-0.4013|0.8426
milan|2026-07-27|24|33|18|15|24|16|8|60.96|~|9|~|~|6|9|0|0.8435|0.3378
milan|2026-07-28|24|33|20|13|26|19|7|65.39|~|7|~|~|5.22|7|0|-0.6489|0.5943
milan|2026-07-29|24|34|24|10|27|19|8|61.65|~|7|~|~|5|8|0|0.4156|0.4255
milan|2026-07-30|24|36|23|13|28|20|8|61.88|~|8|~|~|5.78|8|0|0.7537|0.6025
milan|2026-07-31|24|35|25|10|29|20|9|58.4|~|6|~|~|5.75|9|0|0.6566|0.0034
moscow|2026-03-18|24|9|2|7|3|-3|6|64.67|~|6|~|~|9.50|13.61|0|-0.8519|-0.4644
moscow|2026-06-18|24|16|11|5|14|11|3|82.13|~|2|~|~|11.01|11.66|0|0.6699|0.7154
moscow|2026-06-21|24|25|9|16|18|10|8|59.52|~|7|~|~|12.53|13.61|0|0.2698|-0.9288
moscow|2026-06-22|24|23|17|6|22|15|7|64.52|~|1|~|~|9.29|15.55|0|0.6239|-0.6998
moscow|2026-06-23|22|23|15|8|17|11|6|67.76|~|6|~|~|14.34|17.49|0|0.5631|-0.8131
moscow|2026-06-24|24|21|12|9|17|6|11|48.29|~|4|~|~|9.50|11.66|0|0.7950|-0.3971
moscow|2026-07-25|24|22|15|7|17|15|2|88.01|~|5|~|~|6.48|9.72|0|-0.8616|-0.3362
moscow|2026-07-27|24|26|16|10|18|16|2|88.1|~|8|~|~|10.58|13.61|0|0.9412|0.2792
moscow|2026-07-28|24|27|16|11|20|15|5|72.95|~|7|~|~|10.37|15.55|0|-0.2089|0.9264
moscow|2026-07-29|24|17|14|3|15|14|1|93.74|~|2|~|~|6.70|9.72|0|0.9257|-0.0123
moscow|2026-07-30|24|21|15|6|17|15|2|88.01|~|4|~|~|11.45|15.55|0|0.5372|-0.7973
moscow|2026-07-31|24|24|16|8|19|14|5|72.76|~|5|~|~|14.04|15.55|0|0.4282|-0.8836
munich|2026-03-18|24|11|-3|14|1|-1|2|86.48|~|10|~|~|13.67|18|0|-0.9904|-0.1101
munich|2026-06-18|24|30|13|17|24|17|7|64.96|~|6|~|~|3.56|5|0|-0.9178|0.0247
munich|2026-06-21|24|34|15|19|26|16|10|54.11|~|8|~|~|4.22|6|0|0.2176|-0.4070
munich|2026-06-22|24|33|15|18|25|17|8|61.19|~|8|~|~|4.56|7|0|-0.9599|-0.1034
munich|2026-06-23|22|32|16|16|25|18|7|65.17|~|7|~|~|3.57|6|0|-0.4458|-0.5357
munich|2026-06-24|24|34|17|17|26|19|7|65.39|~|8|~|~|3.56|5|0|-0.1786|-0.3830
munich|2026-07-25|24|31|7|24|18|10|8|59.52|~|13|~|~|4.89|6|0|-0.9090|0.0872
munich|2026-07-27|24|25|16|9|16|14|2|87.93|~|9|~|~|11.22|13|0|0.9409|-0.0616
munich|2026-07-28|24|29|14|15|21|12|9|56.42|~|8|~|~|4|5|0|0.1704|-0.3712
munich|2026-07-29|24|34|11|23|21|14|7|64.3|~|13|~|~|4.22|6|0|-0.5016|0.1579
munich|2026-07-30|24|37|15|22|26|15|11|50.76|~|11|~|~|4.56|8|0|0.7821|-0.3715
munich|2026-07-31|24|36|15|21|25|15|10|53.86|~|11|~|~|5.78|10|0|0.4146|-0.0954
nyc|2026-03-18|24|2.22|-2.78|5|-2.78|-14.44|11.66|40.2|~|5|~|~|7.89|14|0|0.1039|-0.0637
nyc|2026-06-18|24|31.67|20.56|11.11|22.22|20|2.22|87.26|~|9.45|~|~|14.56|25|0|0.5513|0.6318
nyc|2026-06-21|24|28.89|20.56|8.33|22.22|12.22|10|53.13|~|6.67|~|~|7|10|0|0.5483|-0.6564
nyc|2026-06-22|24|22.78|18.89|3.89|20|15.56|4.44|75.59|~|2.78|~|~|8.44|13|1.310|-0.7323|-0.3464
nyc|2026-06-23|22|23.89|20|3.89|20.56|18.89|1.67|90.18|~|3.33|~|~|8.43|10|0.050|0.1699|-0.9805
nyc|2026-06-24|24|27.78|18.89|8.89|21.67|13.33|8.34|59.11|~|6.11|~|~|10.78|14|0|0.6744|-0.6799
nyc|2026-07-25|24|26.11|19.44|6.67|21.67|16.11|5.56|70.7|~|4.44|~|~|11|13|0|-0.7464|-0.5528$blob$;
  v_n int;
begin
  if md5(v_blob) <> '44674f2849c3f428f9871d1c7ddeb25e' then
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
     is distinct from 'b64188f8f9e20832a45c8eb54c64237c' then
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
