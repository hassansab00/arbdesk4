-- ===========================================================================
-- ad4_current_ladder.sql - THE CURRENT PREDICTION BETWEEN PRICING RUNS
-- (plan v2.3 P4.9, 29 Sep).
--
-- The engine prices every open market six times a day (band_probabilities,
-- 00:36, 04:36 ... 20:36Z); the station is read every hour. Between two
-- pricing runs a new station maximum can pass the bucket the card calls the
-- most likely. P4.8 marks such a pick "Out of date" and leaves it until the
-- next run. Measured 29 Sep over the 72 tick hours to 21:36Z, with the card's
-- rule on each city's primary station series: 48 same-day city-days an hour;
-- a pick passed since its pricing 2.31 times an hour on average, 4 at the
-- 90th percentile, 8 in the worst hour; 56 of 186 city-days had one.
--
-- The hourly tick (scripts/tick.py) prices the city-days whose checkpoint is
-- due. It now also prices the same-day city-days whose pick the station has
-- passed, and publishes every ladder it priced here:
--
--   * one row per city-day, and the newest wins: a ladder no newer than the
--     one held is not written, so sending it again writes nothing;
--   * a whole ladder or nothing: every canonical band of the market exactly
--     once, each probability a number in [0, 1], the sum within 1e-4 of 1 -
--     prediction_checkpoints' own tolerance: the engine rounds each band to six
--     places, and 672 of the tick's 907 ladders of 26-29 Sep miss 1 by more
--     than 1e-6 (at most 9e-6);
--   * labelled with its time, why it was priced (a due checkpoint, or a pick
--     the station passed) and the path that priced it (the model_versions
--     label, as band_probabilities carries it).
--
-- NOT band_probabilities: that table takes six appends a day per band and the
-- database is over its tier (P1.6). This holds one row per city-day,
-- rewritten in place. v_current_prediction serves, per open city-day, the
-- newer of the newest pricing and the row held here.
--
-- RUN ORDER: after the tables it reads (markets, bands, v_canonical_bands,
-- band_probabilities, model_versions). Re-runnable.
-- ===========================================================================

create table if not exists public.current_ladders (
  city_key          text        not null,
  target_date       date        not null,
  market_id         uuid        not null,
  priced_at         timestamptz not null,
  reason            text        not null check (reason in ('checkpoint', 'station_max')),
  checkpoint        text,
  engine_version    text        not null,
  priced_from       text,
  centre_c          numeric,
  sigma_c           numeric,
  observed_floor_c  numeric,
  top_band_id       uuid        not null,
  -- {band_id: probability}, the shape prediction_checkpoints.probs has
  ladder            jsonb       not null check (jsonb_typeof(ladder) = 'object'),
  published_at      timestamptz not null default clock_timestamp(),
  primary key (city_key, target_date),
  check (reason <> 'checkpoint' or checkpoint is not null)
);

comment on table public.current_ladders is
  'The newest ladder the hourly tick priced for each city-day, rewritten in place (plan v2.3 P4.9): at a due checkpoint, or because the station passed the pick. Written only through publish_current_ladders: a whole ladder or nothing, newest wins. Read through v_current_prediction.';

alter table public.current_ladders enable row level security;
revoke all on table public.current_ladders from public;

do $grants$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on table public.current_ladders from %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant select, insert, update on table public.current_ladders to service_role';
  end if;
end
$grants$;


-- --------------------------------------------------------------------------
-- THE ONE WRITER. A row is checked here, not only in the tick, because the
-- page reads whatever this table holds.
-- --------------------------------------------------------------------------
create or replace function public.publish_current_ladders(p_rows jsonb)
returns jsonb
language plpgsql
set search_path to ''
as $fn$
declare
  r          jsonb;
  v_city     text;
  v_date     date;
  v_market   uuid;
  v_at       timestamptz;
  v_bands    text[];
  v_given    text[];
  v_bad      integer;
  v_sum      numeric;
  v_top      uuid;
  v_rows     integer;
  v_n        integer := 0;
  v_written  integer := 0;
  v_older    integer := 0;
  v_refused  jsonb := '[]'::jsonb;
  v_why      text;
begin
  if jsonb_typeof(coalesce(p_rows, '[]'::jsonb)) <> 'array' then
    raise exception 'publish_current_ladders: p_rows must be a JSON array';
  end if;

  for r in select value from jsonb_array_elements(coalesce(p_rows, '[]'::jsonb)) loop
    v_n := v_n + 1;
    v_why := null;
    begin
      v_market := (r->>'market_id')::uuid;
      v_at := (r->>'priced_at')::timestamptz;
      select m.city_key, m.resolution_date into v_city, v_date
        from public.markets m where m.market_id = v_market;

      if v_city is null then
        v_why := 'no such market';
      elsif v_city is distinct from r->>'city_key' or v_date is distinct from (r->>'target_date')::date then
        v_why := 'the market is another city-day';
      elsif v_at is null or v_at > clock_timestamp() + interval '5 minutes' then
        v_why := 'priced_at missing or in the future';
      elsif coalesce(r->>'reason', '') not in ('checkpoint', 'station_max') then
        v_why := 'reason must be checkpoint or station_max';
      elsif r->>'reason' = 'checkpoint' and nullif(r->>'checkpoint', '') is null then
        v_why := 'a checkpoint ladder must name its checkpoint';
      elsif nullif(r->>'engine_version', '') is null then
        v_why := 'engine_version required';
      elsif jsonb_typeof(r->'ladder') is distinct from 'object' then
        v_why := 'ladder must be an object of band_id: probability';
      end if;

      if v_why is null then
        select array_agg(b.band_id::text order by b.band_id::text) into v_bands
          from public.v_canonical_bands b where b.market_id = v_market;
        select array_agg(e.key order by e.key),
               count(*) filter (where jsonb_typeof(e.value) <> 'number'
                                   or (e.value::text)::numeric < 0 or (e.value::text)::numeric > 1)
          into v_given, v_bad
          from jsonb_each(r->'ladder') e;
        if v_given is distinct from v_bands then
          v_why := 'not the whole ladder: every canonical band of the market, once';
        elsif v_bad > 0 then
          v_why := 'a probability is not a number in [0, 1]';
        else
          select sum((e.value::text)::numeric) into v_sum from jsonb_each(r->'ladder') e;
          if abs(v_sum - 1) > 0.0001 then
            v_why := format('the ladder sums to %s', v_sum);
          end if;
        end if;
      end if;

      if v_why is null then
        -- The pick: the most probability, tails included, ties to the lower
        -- band_id (P4.8: the card's and tick.py's rule).
        select e.key::uuid into v_top from jsonb_each(r->'ladder') e
         order by (e.value::text)::numeric desc, e.key::uuid limit 1;

        insert into public.current_ladders as c
          (city_key, target_date, market_id, priced_at, reason, checkpoint, engine_version,
           priced_from, centre_c, sigma_c, observed_floor_c, top_band_id, ladder, published_at)
        values
          (v_city, v_date, v_market, v_at, r->>'reason', nullif(r->>'checkpoint', ''), r->>'engine_version',
           nullif(r->>'priced_from', ''), (r->>'centre_c')::numeric, (r->>'sigma_c')::numeric,
           (r->>'observed_floor_c')::numeric, v_top, r->'ladder', clock_timestamp())
        on conflict (city_key, target_date) do update
          set market_id = excluded.market_id, priced_at = excluded.priced_at, reason = excluded.reason,
              checkpoint = excluded.checkpoint, engine_version = excluded.engine_version,
              priced_from = excluded.priced_from, centre_c = excluded.centre_c, sigma_c = excluded.sigma_c,
              observed_floor_c = excluded.observed_floor_c, top_band_id = excluded.top_band_id,
              ladder = excluded.ladder, published_at = excluded.published_at
          where c.priced_at < excluded.priced_at;
        get diagnostics v_rows = row_count;
        if v_rows = 1 then v_written := v_written + 1; else v_older := v_older + 1; end if;
      end if;
    exception when others then
      v_why := format('refused by the database: %s', sqlerrm);
    end;

    if v_why is not null then
      v_refused := v_refused || jsonb_build_array(jsonb_build_object(
        'city_key', r->>'city_key', 'target_date', r->>'target_date', 'why', v_why));
    end if;
  end loop;

  return jsonb_build_object('rows', v_n, 'written', v_written, 'not_newer', v_older,
                            'refused', v_refused);
end
$fn$;

comment on function public.publish_current_ladders(jsonb) is
  'Writes the tick''s newest ladder per city-day into current_ladders (plan v2.3 P4.9): every canonical band of the market once, probabilities in [0, 1] summing to 1 within 1e-4, a reason and a time; a ladder no newer than the one held is not written. Returns rows / written / not_newer / refused (with why). Service role only.';

revoke all on function public.publish_current_ladders(jsonb) from public;
do $fgrants$
declare r text;
begin
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke all on function public.publish_current_ladders(jsonb) from %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant execute on function public.publish_current_ladders(jsonb) to service_role';
  end if;
end
$fgrants$;


-- --------------------------------------------------------------------------
-- WHAT THE PLATFORM SAYS NOW, per open city-day and band: the newer of the
-- newest pricing (every band_probabilities row at the market's newest
-- published instant, as v_prediction_ladder takes it) and the tick's ladder.
-- `source` says which: 'pricing', 'checkpoint' or 'station_max'.
-- --------------------------------------------------------------------------
create or replace view public.v_current_prediction as
with om as (
  select m.market_id, m.city_key, m.resolution_date as target_date
    from public.markets m
   where m.resolution_date >= current_date - 1
),
latest as (
  select distinct on (b.band_id)
         om.market_id, b.band_id, p.computed_at, p.prob_id,
         coalesce(p.calibrated_prob, p.raw_prob) as prob,
         p.centre_c, p.sigma_c, p.observed_floor_c, p.forecast_version
    from om
    join public.v_canonical_bands b on b.market_id = om.market_id
    join public.band_probabilities p on p.band_id = b.band_id
   order by b.band_id, p.computed_at desc, p.prob_id desc
),
instant as (
  select market_id, max(computed_at) as priced_at from latest group by market_id
),
pricing as (
  select l.market_id, l.band_id, l.prob, i.priced_at, l.centre_c, l.sigma_c, l.observed_floor_c,
         (select mv.label from public.model_versions mv where mv.version_id = l.forecast_version) as priced_from
    from latest l join instant i on i.market_id = l.market_id and l.computed_at = i.priced_at
),
day as (
  select om.market_id, om.city_key, om.target_date, i.priced_at as pricing_at,
         c.priced_at as held_at,
         (c.priced_at is not null and (i.priced_at is null or c.priced_at > i.priced_at)) as held_is_newer
    from om
    left join instant i on i.market_id = om.market_id
    left join public.current_ladders c on c.market_id = om.market_id
),
current_rows as (
  select d.city_key, d.target_date, d.market_id, e.key::uuid as band_id, (e.value::text)::numeric as prob,
         c.priced_at, c.reason as source, c.checkpoint, c.centre_c, c.sigma_c, c.observed_floor_c,
         c.priced_from, d.pricing_at
    from day d
    join public.current_ladders c on c.market_id = d.market_id
    cross join lateral jsonb_each(c.ladder) e
   where d.held_is_newer
  union all
  select d.city_key, d.target_date, d.market_id, p.band_id, p.prob,
         p.priced_at, 'pricing', null, p.centre_c, p.sigma_c, p.observed_floor_c,
         p.priced_from, d.pricing_at
    from day d
    join pricing p on p.market_id = d.market_id
   where not d.held_is_newer
)
select city_key, target_date, market_id, band_id, prob, priced_at, source, checkpoint,
       centre_c, sigma_c, observed_floor_c, priced_from, pricing_at,
       band_id = first_value(band_id) over (partition by market_id order by prob desc, band_id) as is_top
  from current_rows;

comment on view public.v_current_prediction is
  'The current prediction per open city-day and band (plan v2.3 P4.9): the newer of the newest pricing (band_probabilities at the market''s newest instant) and the tick''s ladder in current_ladders. source: pricing, checkpoint or station_max; is_top: the most probability, ties to the lower band_id; pricing_at: the newest pricing''s time, for a reader that shows both.';

do $vgrants$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on public.v_current_prediction to %I', r);
    end if;
  end loop;
end
$vgrants$;

-- Its freshness row (the same one sql/ad4_39_freshness.sql seeds): a table a
-- job fills is a table the board can say has gone quiet.
do $fresh$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec (table_name, ts_column, fresh_hours, layer, plain_english)
    values ('current_ladders', 'published_at', 2, 'model',
            'The newest ladder the hourly tick priced for each city-day - at a checkpoint, or after the station passed the pick - shown on the city cards until the next pricing run.')
    on conflict (table_name) do update set
      ts_column     = excluded.ts_column,
      fresh_hours   = excluded.fresh_hours,
      layer         = excluded.layer,
      plain_english = excluded.plain_english;
  end if;
end
$fresh$;
