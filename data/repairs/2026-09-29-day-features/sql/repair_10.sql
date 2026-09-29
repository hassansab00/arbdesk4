-- derived_city_day_features repair, block 10: 21 city-days cached from what a
-- prune's cut left, rewritten from the repository's raw readings
-- (tools/repair_day_features.py plan; audit: data/repairs/2026-09-29-day-features/audit.jsonl).
do $repair$
declare
  v_blob text := $blob$wuhan|2026-06-22|24|25|21|4|23|23|0|100|~|2|~|~|4.54|5.83|0|-0.1726|-0.9758
wuhan|2026-06-23|22|25|21|4|22|22|0|100|~|3|~|~|4.10|5.83|0|-0.4506|-0.8780
wuhan|2026-06-24|24|27|22|5|23|23|0|100|~|4|~|~|2.37|3.89|0|-0.3420|-0.9397
wuhan|2026-07-25|24|36|27|9|30|26|4|79.24|~|6|~|~|3.67|7.78|0|-0.9725|0.1244
wuhan|2026-07-27|24|32|28|4|30|26|4|79.24|~|2|~|~|10.80|11.66|0|-0.9839|0.1141
wuhan|2026-07-28|24|33|26|7|28|25|3|83.82|~|5|~|~|9.94|11.66|0|-0.8669|0.4486
wuhan|2026-07-29|24|33|26|7|29|25|4|79.1|~|4|~|~|8.21|9.72|0|-0.2235|0.8847
wuhan|2026-07-30|24|34|29|5|30|24|6|70.35|~|4|~|~|10.15|11.66|0|-0.0205|0.9719
wuhan|2026-07-31|24|35|30|5|31|24|7|66.44|~|4|~|~|9.94|11.66|0|0.2629|0.9507
zhengzhou|2026-03-18|24|11|3|8|10|4|6|66.26|~|1|~|~|13.61|17.49|0|-0.4132|-0.8746
zhengzhou|2026-06-19|24|23|20|3|21|21|0|100|~|2|~|~|7.35|13.61|0|-0.5446|-0.6218
zhengzhou|2026-06-21|24|26|18|8|25|24|1|94.2|~|1|~|~|10.15|13.61|0|-0.4820|-0.8311
zhengzhou|2026-06-22|24|25|18|7|19|18|1|93.93|~|6|~|~|4.54|5.83|0|-0.5770|-0.3372
zhengzhou|2026-06-23|22|28|20|8|22|20|2|88.45|~|6|~|~|5.40|9.72|0|-0.9460|-0.0689
zhengzhou|2026-06-24|24|29|22|7|24|21|3|83.36|~|5|~|~|4.32|5.83|0|-0.9217|0.2236
zhengzhou|2026-07-25|24|30|23|7|23|23|0|100|~|7|~|~|3.24|5.83|0|-0.4873|0.1930
zhengzhou|2026-07-27|24|29|24|5|26|25|1|94.24|~|3|~|~|5.62|7.78|0|-0.8369|0.5226
zhengzhou|2026-07-28|24|32|24|8|26|26|0|100|~|6|~|~|7.13|27.21|0|-0.9157|-0.0316
zhengzhou|2026-07-29|24|29|24|5|25|25|0|100|~|4|~|~|4.54|5.83|0|-0.7013|0.4816
zhengzhou|2026-07-30|24|31|24|7|26|26|0|100|~|5|~|~|4.75|5.83|0|0.0535|0.9502
zhengzhou|2026-07-31|24|32|27|5|28|27|1|94.33|~|4|~|~|6.05|7.78|0|0.1753|0.9366$blob$;
  v_n int;
begin
  if md5(v_blob) <> '3c19967d58dda9c8197feefb5db5e5a2' then
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
  if v_n <> 21 then raise exception 'expected 21 repair rows, parsed %', v_n; end if;
  -- The rows still hold exactly what the audit recorded as "before".
  if (select md5(string_agg(concat_ws('|', f.city_key, f.obs_date, f.n_obs, f.max_c, f.min_c, f.diurnal_range_c, coalesce(f.prev_max_c::text, '~'), coalesce(f.delta_max_c::text, '~'), coalesce(f.morning_temp_c::text, '~'), coalesce(f.morning_dewpoint_c::text, '~'), coalesce(f.dewpoint_depression_c::text, '~'), coalesce(f.morning_humidity::text, '~'), coalesce(f.morning_pressure_hpa::text, '~'), coalesce(f.morning_to_max_c::text, '~'), coalesce(f.cloud_mean::text, '~'), coalesce(f.cloud_max::text, '~'), coalesce(f.wind_mean::text, '~'), coalesce(f.wind_max::text, '~'), coalesce(f.precip_total::text, '~'), coalesce(f.pressure_change_24h_hpa::text, '~'), coalesce(f.wind_u_mean::text, '~'), coalesce(f.wind_v_mean::text, '~'), to_char(f.computed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US')), E'\n' order by f.city_key, f.obs_date))
        from public.derived_city_day_features f join _repair r using (city_key, obs_date))
     is distinct from '837405e4f68912d414b72e8f17658e56' then
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
  if v_n <> 21 then raise exception 'expected to repair 21 rows, updated %', v_n; end if;
end $repair$;
