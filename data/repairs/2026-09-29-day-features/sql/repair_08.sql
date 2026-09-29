-- derived_city_day_features repair, block 8: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$seattle|2026-06-23|22|31.67|18.89|12.78|23.89|8.89|15|38.47|~|7.78|~|~|7.11|9|0|0.1986|-0.8848
seattle|2026-06-24|24|30|16.67|13.33|20.56|11.11|9.45|54.67|~|9.44|~|~|6|9|0|0.5958|0.7198
seattle|2026-07-25|24|22.22|14.44|7.78|16.11|11.67|4.44|74.94|~|6.11|~|~|10.67|14|0|0.6904|0.6896
seattle|2026-07-27|23|25.56|15|10.56|17.78|12.22|5.56|69.94|~|7.78|~|~|7.56|10|0|0.7369|0.4902
seattle|2026-07-28|24|22.22|15|7.22|17.78|11.67|6.11|67.43|~|4.44|~|~|11.44|13|0|0.6692|0.6593
seattle|2026-07-29|24|23.89|15|8.89|17.78|10|7.78|60.35|~|6.11|~|~|7.56|9|0|0.8084|-0.4188
seattle|2026-07-30|24|25.56|12.22|13.34|15.56|9.44|6.12|66.96|~|10|~|~|4.78|7|0|0.6546|0.5341
seoul|2026-03-18|24|8|4|4|6|2|4|75.49|~|2|~|~|9.89|13|0|0.1855|-0.9021
seoul|2026-06-19|24|31|21|10|24|19|5|73.66|~|7|~|~|6|8|0|0.6066|-0.4176
seoul|2026-06-21|24|27|21|6|22|16|6|68.79|~|5|~|~|8.33|12|0|0.3615|-0.4484
seoul|2026-06-22|24|27|20|7|23|21|2|88.53|~|4|~|~|6.44|9|0|0.3395|0.6785
seoul|2026-06-23|23|30|19|11|22|20|2|88.45|~|8|~|~|7.33|10|0|0.0929|-0.1501
seoul|2026-06-24|23|29|20|9|22|12|10|53.07|~|7|~|~|6.89|10|0|-0.6873|-0.2492
seoul|2026-07-25|24|30|27|3|28|26|2|88.94|~|2|~|~|13.89|17|0|0.3762|0.8862
seoul|2026-07-27|24|31|26|5|28|26|2|88.94|~|3|~|~|10.56|13|0|0.5951|0.7909
seoul|2026-07-28|24|30|26|4|27|26|1|94.29|~|3|~|~|8|11|0|0.9556|0.0909
seoul|2026-07-29|24|30|26|4|27|26|1|94.29|~|3|~|~|9.89|13|0|0.9165|0.3041
seoul|2026-07-30|24|30|26|4|27|25|2|88.86|~|3|~|~|7.78|9|0|0.9637|-0.0832
seoul|2026-07-31|24|31|26|5|28|25|3|83.82|~|3|~|~|9.11|12|0|0.6511|0.7463
shanghai|2026-03-18|24|13|8|5|13|13|0|100|~|0|~|~|12.96|17.49|0|0.4111|-0.8897
shanghai|2026-06-19|24|31|24|7|25|25|0|100|~|6|~|~|10.37|13.61|0|0.1799|0.9635
shanghai|2026-06-21|24|27|24|3|24|24|0|100|~|3|~|~|8.64|11.66|0|-0.9566|0.2163
shanghai|2026-06-22|24|24|23|1|23|23|0|100|~|1|~|~|16.63|21.38|0|-0.9821|0.0479
shanghai|2026-06-23|22|24|22|2|23|23|0|100|~|1|~|~|8.43|11.66|0|0.1302|-0.9569
shanghai|2026-06-24|24|24|22|2|23|23|0|100|~|1|~|~|10.80|11.66|0|-0.3768|-0.9235
shanghai|2026-07-25|24|35|29|6|31|26|5|74.84|~|4|~|~|13.39|15.55|0|-0.2103|0.9261
shanghai|2026-07-27|24|34|28|6|30|26|4|79.24|~|4|~|~|12.31|13.61|0|-0.2623|0.9227
shanghai|2026-07-28|24|33|27|6|30|26|4|79.24|~|3|~|~|11.23|13.61|0|-0.4824|0.8436
shanghai|2026-07-29|24|35|28|7|31|25|6|70.53|~|4|~|~|10.37|11.66|0|-0.1000|0.9767
shanghai|2026-07-30|24|36|28|8|33|27|6|70.91|~|3|~|~|6.70|9.72|0|-0.4607|0.4911
shanghai|2026-07-31|24|36|28|8|32|26|6|70.72|~|4|~|~|7.99|11.66|0|-0.6359|0.1565
shenzhen|2026-03-18|24|26|19|7|22|18|4|78.08|~|4|~|~|7.99|11.66|0|0.2458|0.8484
shenzhen|2026-06-19|24|31|27|4|27|26|1|94.29|~|4|~|~|13.61|17.49|0|-0.0394|0.9793
shenzhen|2026-06-21|24|31|28|3|29|27|2|89.02|~|2|~|~|13.17|15.55|0|-0.2288|0.9700
shenzhen|2026-06-22|24|32|28|4|29|27|2|89.02|~|3|~|~|11.88|15.55|0|-0.0883|0.9808
shenzhen|2026-06-23|22|32|29|3|30|27|3|84.04|~|2|~|~|14.04|17.49|0|-0.1489|0.9832
shenzhen|2026-06-24|24|32|29|3|30|28|2|89.1|~|2|~|~|13.61|17.49|0|-0.1069|0.9873
shenzhen|2026-07-25|24|36|24|12|30|27|3|84.04|~|6|~|~|9.29|21.38|0|0.2450|-0.5178
shenzhen|2026-07-27|24|30|26|4|27|26|1|94.29|~|3|~|~|11.45|13.61|0|-0.2194|0.9613
shenzhen|2026-07-28|24|30|26|4|30|25|5|74.68|~|0|~|~|6.05|11.66|0|-0.4136|0.4472
shenzhen|2026-07-29|24|29|26|3|27|25|2|88.86|~|2|~|~|8.86|9.72|0|-0.8588|0.4417
shenzhen|2026-07-30|24|31|26|5|29|25|4|79.1|~|2|~|~|6.26|9.72|0|-0.4467|0.5469
shenzhen|2026-07-31|24|27|24|3|25|23|2|88.7|~|2|~|~|4.97|7.78|0|-0.4495|0.0438
singapore|2026-03-18|24|32|26|6|27|24|3|83.7|~|5|~|~|10.11|13|0|-0.1623|-0.9478
singapore|2026-06-19|24|31|27|4|28|24|4|78.96|~|3|~|~|6.67|9|0|-0.5765|0.7602
singapore|2026-06-21|24|32|26|6|28|26|2|88.94|~|4|~|~|6.22|8|0|-0.2637|0.9301
singapore|2026-06-22|24|28|25|3|27|23|4|78.81|~|1|~|~|5.33|7|0|0.5647|0.6777
singapore|2026-06-23|22|32|26|6|28|24|4|78.96|~|4|~|~|6.67|10|0|-0.0020|0.9702
singapore|2026-06-24|24|31|24|7|26|24|2|88.78|~|5|~|~|5.11|10|0|-0.0778|0.8896
singapore|2026-07-25|24|30|28|2|28|25|3|83.82|~|2|~|~|8|11|0|-0.2583|0.9157
singapore|2026-07-27|24|32|26|6|28|24|4|78.96|~|4|~|~|9.56|13|0|-0.6862|0.6539
singapore|2026-07-28|24|31|28|3|29|25|4|79.1|~|2|~|~|8.67|13|0|-0.7599|0.6079
singapore|2026-07-29|24|32|28|4|28|25|3|83.82|~|4|~|~|8.22|10|0|-0.7334|0.6306
singapore|2026-07-30|24|30|27|3|28|25|3|83.82|~|2|~|~|6.67|10|0|-0.6298|0.5263
singapore|2026-07-31|24|32|27|5|28|25|3|83.82|~|4|~|~|8|10|0|-0.7553|0.5125
taipei|2026-03-18|24|29|16|13|25|18|7|65.17|~|4|~|~|5.11|7|0|0.6437|-0.7355
taipei|2026-06-19|24|36|24|12|30|26|4|79.24|~|6|~|~|5.33|8|0|0.0149|-0.4920
taipei|2026-06-21|24|37|26|11|32|25|7|66.65|~|5|~|~|5.44|9|0|0.8970|-0.4183
taipei|2026-06-22|24|37|27|10|32|26|6|70.72|~|5|~|~|6.44|11|0|0.9554|-0.2835
tel_aviv|2026-03-18|24|30|10|20|20|9|11|49.12|~|10|~|~|12.44|15|0|-0.5057|0.4945$blob$;
  v_n int;
begin
  if md5(v_blob) <> 'f7fe811a0694255cc6db23810e515579' then
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
     is distinct from '4b3cc2ace67433b52eb1f231d452f2f7' then
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
