-- derived_city_day_features repair, second last step: 14 rows whose day-over-day
-- terms are NULL although an earlier cached day exists. Each was computed just
-- after a prune had taken the day before it out of v_city_day_features, so the
-- view's lag() had nothing to read (e.g. buenos_aires 29 Jul, computed 28 Sep
-- 02:38Z; the cut left 22 minutes of 28 Jul local, no reading). Their own
-- values are whole; only prev_max_c, delta_max_c and pressure_change_24h_hpa
-- are written, from the cached day before, as the view defines them. Found by
-- checking every row of the table against lag() after the repair (29 Sep).
do $repair$
declare v_n int;
begin
  with rows(city_key, obs_date) as (values
    ('buenos_aires', date '2026-07-29'), ('buenos_aires', date '2026-07-30'), ('buenos_aires', date '2026-07-31'),
    ('jakarta', date '2026-06-19'), ('jinan', date '2026-03-21'), ('jinan', date '2026-06-19'),
    ('jinan', date '2026-06-28'), ('lagos', date '2026-03-18'), ('los_angeles', date '2026-06-21'),
    ('san_francisco', date '2026-06-21'), ('sao_paulo', date '2026-07-29'), ('sao_paulo', date '2026-07-30'),
    ('sao_paulo', date '2026-07-31'), ('seattle', date '2026-06-21')),
  lagged as (select city_key, obs_date, lag(max_c) over w as p_max, lag(morning_pressure_hpa) over w as p_pressure
               from public.derived_city_day_features window w as (partition by city_key order by obs_date))
  update public.derived_city_day_features f
     set prev_max_c = l.p_max,
         delta_max_c = f.max_c - l.p_max,
         pressure_change_24h_hpa = round(f.morning_pressure_hpa - l.p_pressure, 2),
         computed_at = now()
    from lagged l join rows r using (city_key, obs_date)
   where f.city_key = l.city_key and f.obs_date = l.obs_date
     and f.prev_max_c is null and f.delta_max_c is null and l.p_max is not null;
  get diagnostics v_n = row_count;
  if v_n <> 14 then raise exception 'expected 14 rows with NULL day-over-day terms, found %', v_n; end if;
end $repair$;
