-- The database half of data/replay/inputs_engine_2026-09-27/books.json.gz (plan v2 P5.12 part 2).
-- Run 27 Sep 2026 ~10:08-10:12Z against book_snapshots, in date chunks (each answer ~1.4-3.2 MB):
--   [2026-09-11, 2026-09-20), [09-20, 09-23), [09-23, 09-24), [09-24, 09-25), [09-25, 09-27)
-- for the ask side, and [09-11, 09-21), [09-21, 09-24), [09-24, 09-27) for the bid side, joined on
-- (band_id, epoch). 101,860 rows (= select count(*) ... where observed_at >= '2026-09-11' and < '2026-09-27').
-- The other half is every row of data/archive/books/*.csv.gz in the same window that the database no
-- longer holds (185,151; 5,853 rows present in both were taken from the database).
select json_agg(json_build_array(band_id, extract(epoch from observed_at)::bigint,
         round(best_bid, 3), round(best_ask, 3), round(no_best_ask, 3),
         round(ask_usd_1c), round(ask_usd_2c), round(ask_usd_5c), round(ask_usd_10c), round(ask_usd_25c))
         order by snapshot_id)
  from book_snapshots
 where observed_at >= :from and observed_at < :to;

select json_agg(json_build_array(band_id, extract(epoch from observed_at)::bigint,
         round(bid_usd_1c), round(bid_usd_2c), round(bid_usd_5c), round(bid_usd_10c), round(bid_usd_25c))
         order by snapshot_id)
  from book_snapshots
 where observed_at >= :from and observed_at < :to;
