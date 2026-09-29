-- derived_city_day_features repair, block 2: 60 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$buenos_aires|2026-06-21|24|15|7|8|9|5|4|76.01|~|6|~|~|4.78|7|0|0.6578|-0.7351
buenos_aires|2026-06-22|24|12|4|8|5|3|2|86.89|~|7|~|~|9.22|12|0|0.9220|0.3198
buenos_aires|2026-06-23|22|12|2|10|2|1|1|93.08|~|10|~|~|8.86|11|0|0.9757|0.1270
buenos_aires|2026-06-24|24|11|1|10|2|1|1|93.08|~|9|~|~|7.22|10|0|0.8666|-0.4516
buenos_aires|2026-07-25|24|18|8|10|10|10|0|100|~|8|~|~|6.67|9|0|-0.4413|-0.7991
buenos_aires|2026-07-27|24|18|11|7|13|13|0|100|~|5|~|~|6.44|9|0|-0.3551|-0.7587
busan|2026-03-18|24|10|5|5|8|6|2|87.18|~|2|~|~|4.44|6|0|0.0035|-0.6922
busan|2026-06-19|24|28|23|5|25|22|3|83.47|~|3|~|~|6.67|9|0|-0.6636|0.5793
busan|2026-06-21|24|27|21|6|24|19|5|73.66|~|3|~|~|7.56|9|0|-0.4013|0.7613
busan|2026-06-22|24|23|20|3|21|19|2|88.36|~|2|~|~|5.44|8|0|-0.8764|0.2066
busan|2026-06-23|23|23|20|3|20|16|4|77.77|~|3|~|~|12.22|14|0|-0.8732|-0.4768
busan|2026-06-24|23|23|20|3|21|17|4|77.93|~|2|~|~|11.89|13|0|-0.7496|-0.6544
busan|2026-07-25|24|35|25|10|29|24|5|74.51|~|6|~|~|6.22|11|0|0.2661|0.7458
busan|2026-07-27|24|35|26|9|30|25|5|74.68|~|5|~|~|9.89|16|0|0.3306|0.8856
busan|2026-07-28|24|35|27|8|30|25|5|74.68|~|5|~|~|9.33|13|0|0.4509|0.5062
busan|2026-07-29|24|39|29|10|30|23|7|66.24|~|9|~|~|7.22|9|0|0.8454|-0.4932
busan|2026-07-30|24|38|27|11|30|22|8|62.34|~|8|~|~|7|8|0|0.8868|-0.4204
busan|2026-07-31|24|37|26|11|30|23|7|66.24|~|7|~|~|9.44|13|0|0.5040|0.2714
cape_town|2026-03-18|24|28|15|13|16|15|1|93.79|~|12|~|~|10.11|18|0|0.3555|0.6830
cape_town|2026-06-18|24|19|14|5|14|12|2|87.75|~|5|~|~|17.56|20|0|0.1210|0.9684
cape_town|2026-06-21|24|22|12|10|12|9|3|81.87|~|10|~|~|4.11|6|0|0.0096|0.7422
cape_town|2026-06-22|24|14|12|2|12|12|0|100|~|2|~|~|13.33|16|0|0.4640|-0.8789
cape_town|2026-06-23|22|17|13|4|14|11|3|82.13|~|3|~|~|16.43|21|0|0.2637|-0.9146
cape_town|2026-06-24|24|17|12|5|13|12|1|93.65|~|4|~|~|10.11|13|0|-0.0565|0.9853
cape_town|2026-07-25|24|21|11|10|12|10|2|87.56|~|9|~|~|8.11|10|0|0.3723|0.9042
cape_town|2026-07-27|24|17|13|4|14|11|3|82.13|~|3|~|~|21|26|0|-0.2571|0.9522
cape_town|2026-07-28|24|20|12|8|12|6|6|66.7|~|8|~|~|10.89|13|0|-0.6339|0.7692
cape_town|2026-07-29|24|21|5|16|5|4|1|93.24|~|16|~|~|5.89|10|0|0.4114|0.8365
cape_town|2026-07-30|24|21|5|16|6|5|1|93.29|~|15|~|~|5.11|9|0|0.1527|0.9702
cape_town|2026-07-31|24|24|8|16|8|6|2|87.18|~|16|~|~|5|8|0|-0.0064|0.8492
chengdu|2026-03-18|24|14|9|5|10|7|3|81.6|~|4|~|~|4.10|5.83|0|0.8533|-0.0263
chengdu|2026-06-19|24|34|22|12|24|21|3|83.36|~|10|~|~|4.11|7.78|0|-0.2588|0.7558
chengdu|2026-06-21|24|24|21|3|21|20|1|94.02|~|3|~|~|6.26|9.72|0|-0.3757|-0.8917
chengdu|2026-06-22|24|33|22|11|24|20|4|78.38|~|9|~|~|4.54|9.72|0|-0.4476|0.1134
chengdu|2026-06-23|22|30|24|6|24|21|3|83.36|~|6|~|~|4.32|5.83|0|-0.3730|-0.5829
chengdu|2026-06-24|24|28|23|5|24|21|3|83.36|~|4|~|~|6.26|9.72|0|-0.4575|-0.7660
chengdu|2026-07-25|24|41|27|14|30|21|9|58.64|~|11|~|~|3.67|5.83|0|0.0389|0.3213
chengdu|2026-07-27|24|30|23|7|24|23|1|94.16|~|6|~|~|5.18|7.78|0|-0.1260|-0.6303
chengdu|2026-07-28|24|32|22|10|24|22|2|88.61|~|8|~|~|3.24|5.83|0|0.3124|0.9130
chengdu|2026-07-29|24|34|25|9|26|24|2|88.78|~|8|~|~|3.46|5.83|0|0.2949|0.6947
chengdu|2026-07-30|24|28|23|5|24|23|1|94.16|~|4|~|~|7.13|7.78|0|-0.3552|-0.9144
chengdu|2026-07-31|24|27|23|4|24|23|1|94.16|~|3|~|~|5.83|9.72|0|-0.3321|-0.8820
chicago|2026-03-18|24|7.78|-5|12.78|-3.33|-7.22|3.89|74.44|~|11.11|~|~|10|13|0.010|0.4314|0.7966
chicago|2026-06-18|24|22.78|16.67|6.11|17.22|12.22|5|72.44|~|5.56|~|~|8.67|12|0|0.7566|-0.5225
chicago|2026-06-21|24|21.67|15|6.67|20|13.89|6.11|67.89|~|1.67|~|~|6.67|13|0.690|-0.6858|0.5987
chicago|2026-06-22|24|22.22|14.44|7.78|18.33|12.22|6.11|67.55|~|3.89|~|~|13.78|16|0.020|-0.3993|-0.8884
chicago|2026-06-23|22|23.33|12.22|11.11|16.67|10|6.67|64.75|~|6.66|~|~|7.88|9|0|-0.6693|-0.6930
chicago|2026-06-24|24|21.67|15.56|6.11|19.44|11.11|8.33|58.56|~|2.23|~|~|6.22|9|0.890|-0.2377|0.2803
chicago|2026-07-25|24|26.67|17.22|9.45|21.67|15|6.67|65.84|~|5|~|~|8.11|10|0|0.4161|0.8696
chicago|2026-07-27|23|29.44|22.22|7.22|26.67|25|1.67|90.61|~|2.77|~|~|14.33|25|1.760|-0.2055|0.3521
chicago|2026-07-28|24|26.11|20|6.11|20.56|17.78|2.78|84.12|~|5.55|~|~|13.22|16|0|-0.5581|-0.8207
chicago|2026-07-29|24|26.67|17.22|9.45|21.67|16.11|5.56|70.7|~|5|~|~|11|12|0|-0.4796|-0.8334
chicago|2026-07-30|24|30|17.22|12.78|24.44|13.33|11.11|49.97|~|5.56|~|~|4.33|6|0|0.7030|0.4748
chongqing|2026-03-18|24|14|11|3|11|11|0|100|~|3|~|~|3.89|5.83|0|0.0578|0.1875
chongqing|2026-06-19|24|30|21|9|22|22|0|100|~|8|~|~|3.89|5.83|0|0.3616|0.3730
chongqing|2026-06-21|24|27|23|4|24|24|0|100|~|3|~|~|6.05|7.78|0|0.6251|0.5765
chongqing|2026-06-22|24|24|22|2|22|22|0|100|~|2|~|~|5.18|7.78|0|0.6980|0.4490
chongqing|2026-06-23|22|25|22|3|23|23|0|100|~|2|~|~|4.75|5.83|0|-0.1215|0.9373
chongqing|2026-06-24|24|28|23|5|23|23|0|100|~|5|~|~|3.46|5.83|0|-0.4646|-0.0203
chongqing|2026-07-25|24|39|29|10|31|21|10|55.39|~|8|~|~|8.64|11.66|0|-0.6119|0.7495$blob$;
  v_n int;
begin
  if md5(v_blob) <> '7d97d1236adbfa3626f782e745828613' then
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
     is distinct from '0bce748523eefb9fbcbe08ec6d4fa09a' then
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
