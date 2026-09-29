-- derived_city_day_features repair, last step: the day-over-day terms.
with cuts(t) as (values ('2026-03-18T09:01:44.414739+00:00'::timestamptz),('2026-06-18T16:54:29.777872+00:00'::timestamptz),('2026-06-21T07:44:53.186284+00:00'::timestamptz),('2026-06-22T08:13:08.206239+00:00'::timestamptz),('2026-06-23T13:16:15.128648+00:00'::timestamptz),('2026-06-23T15:14:23.150155+00:00'::timestamptz),('2026-06-23T16:14:31.222548+00:00'::timestamptz),('2026-06-24T08:11:51.629442+00:00'::timestamptz),('2026-07-25T08:14:25.812918+00:00'::timestamptz),('2026-07-27T09:38:47.038240+00:00'::timestamptz),('2026-07-28T02:38:28.157669+00:00'::timestamptz),('2026-07-29T02:37:32.013806+00:00'::timestamptz),('2026-07-30T02:38:02.794714+00:00'::timestamptz),('2026-07-31T02:38:22.599704+00:00'::timestamptz)),
pairs as (select distinct c.city_key, (t at time zone c.timezone)::date + k as d
            from public.cities c, cuts, generate_series(-1, 1) k where c.timezone is not null),
repaired as (select f.city_key, f.obs_date from public.derived_city_day_features f
               join pairs p on f.city_key = p.city_key and f.obs_date = p.d
              where f.computed_at >= timestamptz '2026-09-29T07:40:00Z'),
nxt as (select r.city_key, (select min(q.obs_date) from public.derived_city_day_features q
                             where q.city_key = r.city_key and q.obs_date > r.obs_date) as obs_date
          from repaired r),
affected as (select city_key, obs_date from repaired
             union select city_key, obs_date from nxt where obs_date is not null),
lagged as (select city_key, obs_date, lag(max_c) over w as p_max, lag(morning_pressure_hpa) over w as p_pressure
             from public.derived_city_day_features
           window w as (partition by city_key order by obs_date)),
done as (
  update public.derived_city_day_features f
     set prev_max_c = l.p_max,
         delta_max_c = f.max_c - l.p_max,
         pressure_change_24h_hpa = round(f.morning_pressure_hpa - l.p_pressure, 2),
         computed_at = now()
    from lagged l join affected a using (city_key, obs_date)
   where f.city_key = l.city_key and f.obs_date = l.obs_date
     and (f.prev_max_c is distinct from l.p_max
          or f.delta_max_c is distinct from f.max_c - l.p_max
          or f.pressure_change_24h_hpa is distinct from round(f.morning_pressure_hpa - l.p_pressure, 2))
  returning f.city_key)
select (select count(*) from repaired) as repaired_rows, (select count(*) from affected) as affected_rows,
       (select count(*) from done) as day_over_day_written
