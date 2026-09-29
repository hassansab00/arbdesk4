-- derived_city_day_features repair, block 5: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$london|2026-06-22|24|27|17|10|20|16|4|77.77|~|7|~|~|13.44|18|0|-0.9896|-0.0244
london|2026-06-23|22|31|18|13|22|20|2|88.45|~|9|~|~|8|9|0|-0.9233|0.2045
london|2026-06-24|24|34|21|13|26|20|6|69.58|~|8|~|~|5.89|13|0|-0.8249|-0.3480
london|2026-07-25|24|29|17|12|21|12|9|56.42|~|8|~|~|10|12|0|0.9413|0.2611
london|2026-07-27|24|27|17|10|19|11|8|59.76|~|8|~|~|9.33|10|0|0.9546|0.0311
london|2026-07-28|24|29|19|10|22|16|6|68.79|~|7|~|~|6.33|8|0|0.6860|0.6277
london|2026-07-29|24|34|20|14|24|14|10|53.6|~|10|~|~|8.67|13|0|0.4197|0.6816
london|2026-07-30|24|28|20|8|22|15|7|64.52|~|6|~|~|9|11|0|0.9298|0.3139
london|2026-07-31|24|25|17|8|19|9|10|52.27|~|6|~|~|4.89|7|0|0.5073|-0.5622
los_angeles|2026-03-18|24|27.22|17.22|10|23.33|11.67|11.66|47.89|~|3.89|~|~|8.78|12|0|0.9247|0.1067
los_angeles|2026-06-18|24|21.11|16.67|4.44|18.33|13.89|4.44|75.32|~|2.78|~|~|10.22|15|0|0.9606|0.1984
los_angeles|2026-06-22|24|21.67|16.67|5|18.33|13.89|4.44|75.32|~|3.34|~|~|9.11|12|0|0.9694|0.1988
los_angeles|2026-06-23|22|21.67|17.22|4.45|18.89|15|3.89|78.16|~|2.78|~|~|9.33|12|0|0.9712|0.1603
los_angeles|2026-06-24|24|21.11|16.67|4.44|18.89|15|3.89|78.16|~|2.22|~|~|11.67|14|0|0.9676|0.1699
los_angeles|2026-07-25|24|25.56|21.11|4.45|23.89|18.89|5|73.64|~|1.67|~|~|8.67|11|0|0.9534|0.2759
los_angeles|2026-07-27|23|26.11|20.56|5.55|23.33|19.44|3.89|78.82|~|2.78|~|~|10|13|0|0.9703|0.2010
los_angeles|2026-07-28|24|25.56|20.56|5|23.33|18.33|5|73.54|~|2.23|~|~|10.11|14|0|0.9731|0.1444
los_angeles|2026-07-29|24|26.11|20.56|5.55|22.78|17.22|5.56|70.91|~|3.33|~|~|10.33|14|0|0.8493|0.3742
los_angeles|2026-07-30|24|25|19.44|5.56|23.89|17.22|6.67|66.32|~|1.11|~|~|11.89|15|0|0.9779|0.1807
lucknow|2026-03-18|24|34|19|15|25|14|11|50.49|~|9|~|~|4.56|8|0|-0.1112|-0.4462
lucknow|2026-06-18|24|40|31|9|35|21|14|44.27|~|5|~|~|7.56|12|0|0.8016|-0.2064
lucknow|2026-06-21|24|40|31|9|33|22|11|52.59|~|7|~|~|9.22|13|0|0.9238|-0.1909
lucknow|2026-06-22|24|40|30|10|35|21|14|44.27|~|5|~|~|10.56|15|0|0.8469|-0.4574
lucknow|2026-06-23|22|40|29|11|33|21|12|49.47|~|7|~|~|9.56|12|0|0.8341|-0.4430
lucknow|2026-06-24|24|39|26|13|30|23|7|66.24|~|9|~|~|5.44|9|0|0.8003|-0.3832
lucknow|2026-07-25|24|33|28|5|29|28|1|94.37|~|4|~|~|7.44|12|0|-0.3079|0.6419
lucknow|2026-07-27|24|33|28|5|29|29|0|100|~|4|~|~|1.89|5|0|0.1073|-0.5407
lucknow|2026-07-28|24|34|27|7|29|29|0|100|~|5|~|~|4.44|8|0|-0.5480|-0.2059
lucknow|2026-07-29|24|34|28|6|31|28|3|84.15|~|3|~|~|8.22|11|0|-0.9147|0.0947
lucknow|2026-07-30|24|34|28|6|30|27|3|84.04|~|4|~|~|10.78|14|0|-0.9807|0.0216
lucknow|2026-07-31|24|32|28|4|29|27|2|89.02|~|3|~|~|10.67|15|0|-0.9754|0.1550
madrid|2026-03-18|24|18|3|15|3|2|1|93.13|~|15|~|~|3.11|7|0|-0.1854|0.6272
madrid|2026-06-18|24|35|19|16|20|7|13|42.87|~|15|~|~|5.67|9|0|-0.4083|0.6906
madrid|2026-06-21|24|39|22|17|22|11|11|49.67|~|17|~|~|6.33|14|0|-0.4251|0.4219
madrid|2026-06-22|24|40|23|17|24|9|15|38.5|~|16|~|~|6.22|10|0|-0.5452|0.4295
madrid|2026-06-23|22|40|20|20|21|8|13|43.16|~|19|~|~|4.43|6|0|-0.6866|0.5732
madrid|2026-06-24|24|39|22|17|23|9|14|40.89|~|16|~|~|4.89|9|0|-0.6180|0.5386
madrid|2026-07-25|24|29|18|11|18|8|10|52|~|11|~|~|4.78|6|0|0.6289|0.2225
madrid|2026-07-27|24|35|17|18|18|8|10|52|~|17|~|~|4.11|6|0|-0.0968|-0.4765
madrid|2026-07-28|24|38|20|18|20|6|14|40.02|~|18|~|~|4.44|8|0|-0.1558|0.3786
madrid|2026-07-29|24|39|21|18|22|7|15|37.92|~|17|~|~|5.11|11|0|-0.1050|0.8665
madrid|2026-07-30|24|40|21|19|21|9|12|46.19|~|19|~|~|5.44|10|0|0.3889|0.6950
madrid|2026-07-31|24|38|26|12|26|6|20|27.85|~|12|~|~|10.89|15|0|0.5094|0.8141
manila|2026-03-18|24|32|25|7|27|19|8|61.65|~|5|~|~|8.22|11|0|-0.8416|0.4792
manila|2026-06-19|24|34|28|6|31|24|7|66.44|~|3|~|~|5.56|7|0|0.1251|-0.2284
manila|2026-06-21|24|36|26|10|32|26|6|70.72|~|4|~|~|5.89|9|0|0.9876|-0.0685
manila|2026-06-22|24|33|27|6|30|25|5|74.68|~|3|~|~|9.78|15|0|0.9622|-0.0210
manila|2026-06-23|22|32|29|3|31|25|6|70.53|~|1|~|~|13.44|17|0|0.9768|0.1566
manila|2026-06-24|24|30|25|5|29|25|4|79.1|~|1|~|~|11.33|20|0|0.9762|0.1329
manila|2026-07-25|24|32|27|5|29|25|4|79.1|~|3|~|~|6.89|11|0|0.7432|0.0625
manila|2026-07-27|24|33|26|7|28|25|3|83.82|~|5|~|~|6.44|10|0|0.9760|0.1038
manila|2026-07-28|24|32|24|8|30|26|4|79.24|~|2|~|~|5.33|8|0|0.7450|-0.0649
manila|2026-07-29|24|27|25|2|26|24|2|88.78|~|1|~|~|3|4|0|0.2805|-0.1332
manila|2026-07-30|24|33|25|8|27|26|1|94.29|~|6|~|~|5.44|12|0|0.4741|-0.2432
manila|2026-07-31|24|33|25|8|29|26|3|83.93|~|4|~|~|6.56|9|0|0.7533|-0.1955
mexico_city|2026-03-18|24|24|6|18|11|8|3|81.74|~|13|~|~|4.89|10|0|-0.4591|-0.5174
mexico_city|2026-06-18|24|26|14|12|18|13|5|72.58|~|8|~|~|7.78|15|0|-0.3930|-0.0125
mexico_city|2026-06-21|23|24|15|9|18|9|9|55.65|~|6|~|~|9.44|20|0|-0.7358|0.4542
mexico_city|2026-06-22|23|25|15|10|20|11|9|56.16|~|5|~|~|9|20|0|-0.5117|-0.3259
mexico_city|2026-06-23|22|24|13|11|20|11|9|56.16|~|4|~|~|7.22|13|0|-0.4631|-0.5004$blob$;
  v_n int;
begin
  if md5(v_blob) <> 'acfc72a9923635b4d5a4213c664a5ddf' then
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
     is distinct from '0ea846afaca02330b3666fa26db52556' then
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
