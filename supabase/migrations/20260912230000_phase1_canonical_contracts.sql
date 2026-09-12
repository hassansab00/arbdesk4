-- Phase 1B: publish corrected contract semantics without rewriting source rows.
--
-- The first market collector stored inclusive display bounds directly.  An
-- exact label such as "20 C" therefore became [20,20), while a two-degree
-- Fahrenheit label such as "70-71 F" became [70,71).  Both are wrong for the
-- whole-number lattice used by Polymarket.  The source rows are evidence and
-- stay untouched; corrections are append-only and canonical views apply them.
begin;

create index if not exists proprietary_correction_band_lookup
  on public.proprietary_data_corrections(
    (source_key ->> 'band_id'), recorded_at desc
  ) where source_relation = 'bands';
create index if not exists proprietary_correction_market_lookup
  on public.proprietary_data_corrections(
    (source_key ->> 'market_id'), recorded_at desc
  ) where source_relation = 'markets';

-- Parse only labels with an unambiguous integer-lattice shape.  Labels that
-- do not match this grammar remain unchanged/flagged;
-- guessing at them would be worse than excluding them from corrected history.
with parsed as (
  select
    b.*,
    to_jsonb(b) as original_payload,
    regexp_match(
      b.band_label,
      '^\s*(-?[0-9]+(?:\.[0-9]+)?)\s*(?:-\s*(-?[0-9]+(?:\.[0-9]+)?))?\s*°?([CF])(?:\s+or\s+(below|higher))?\s*$',
      'i'
    ) as part
  from public.bands b
), expected as (
  select
    p.*,
    (part[1])::numeric as expected_lo,
    case
      when lower(coalesce(part[4], '')) = 'higher' then null
      when part[2] is not null then (part[2])::numeric + 1
      else (part[1])::numeric + 1
    end as expected_hi,
    upper(part[3]) as label_unit
  from parsed p
  where part is not null
), changed as (
  select e.*
  from expected e
  where
    (e.open_low and (e.band_lo is not null or e.band_hi is distinct from e.expected_hi))
    or (e.open_high and (e.band_lo is distinct from e.expected_lo or e.band_hi is not null))
    or (not e.open_low and not e.open_high and
        (e.band_lo is distinct from e.expected_lo or e.band_hi is distinct from e.expected_hi))
)
insert into public.proprietary_data_corrections(
  source_relation, source_key, original_payload_hash, corrected_values,
  reason, evidence, code_version
)
select
  'bands',
  jsonb_build_object('band_id', c.band_id),
  md5(c.original_payload::text),
  jsonb_build_object(
    'band_lo', case when c.open_low then null else c.expected_lo end,
    'band_hi', case when c.open_high then null else c.expected_hi end,
    'label_unit', c.label_unit,
    'interval_semantics', 'whole-number lattice [lo,hi)'
  ),
  'Normalize inclusive display labels to half-open whole-number lattice bounds',
  jsonb_build_object(
    'band_label', c.band_label,
    'original_band_lo', c.band_lo,
    'original_band_hi', c.band_hi,
    'open_low', c.open_low,
    'open_high', c.open_high,
    'parser', 'ad4-contract-label-v1'
  ),
  'phase1b-canonical-contracts-v1'
from changed c
where not exists (
  select 1
  from public.proprietary_data_corrections x
  where x.source_relation = 'bands'
    and x.source_key ->> 'band_id' = c.band_id::text
    and x.code_version = 'phase1b-canonical-contracts-v1'
);

-- A market's label unit is the contract evidence.  Correct only markets where
-- every parseable band agrees on one unit; mixed/ambiguous ladders stay
-- unchanged and remain visible to the quality system.
with label_units as (
  select
    b.market_id,
    min(upper(x.part[1])) as label_unit,
    count(distinct upper(x.part[1])) as unit_count
  from public.bands b
  cross join lateral regexp_match(
    b.band_label,
    '°?([CF])(?:\s+or\s+(?:below|higher))?\s*$',
    'i'
  ) as x(part)
  group by b.market_id
), changed as (
  select m.*, u.label_unit, to_jsonb(m) as original_payload
  from public.markets m
  join label_units u on u.market_id = m.market_id and u.unit_count = 1
  where m.unit is distinct from u.label_unit
)
insert into public.proprietary_data_corrections(
  source_relation, source_key, original_payload_hash, corrected_values,
  reason, evidence, code_version
)
select
  'markets',
  jsonb_build_object('market_id', c.market_id),
  md5(c.original_payload::text),
  jsonb_build_object('unit', c.label_unit),
  'Use the single unit printed consistently across the contract ladder',
  jsonb_build_object(
    'original_market_unit', c.unit,
    'label_unit', c.label_unit,
    'city_key', c.city_key,
    'resolution_date', c.resolution_date,
    'parser', 'ad4-contract-label-v1'
  ),
  'phase1b-canonical-contracts-v1'
from changed c
where not exists (
  select 1
  from public.proprietary_data_corrections x
  where x.source_relation = 'markets'
    and x.source_key ->> 'market_id' = c.market_id::text
    and x.code_version = 'phase1b-canonical-contracts-v1'
);

create or replace view public.v_canonical_markets
with (security_invoker = true)
as
with latest as (
  select distinct on (source_key ->> 'market_id')
    source_key ->> 'market_id' as market_id,
    correction_id,
    corrected_values,
    original_payload_hash,
    code_version
  from public.proprietary_data_corrections
  where source_relation = 'markets'
  order by source_key ->> 'market_id', recorded_at desc, correction_id desc
)
select
  m.market_id, m.city_key, m.resolution_date, m.event_slug,
  case when l.corrected_values ? 'unit' then l.corrected_values ->> 'unit' else m.unit end as unit,
  m.closed, m.condition_id,
  m.unit as source_unit,
  l.correction_id, l.original_payload_hash, l.code_version as correction_version
from public.markets m
left join latest l on l.market_id = m.market_id::text;

create or replace view public.v_canonical_bands
with (security_invoker = true)
as
with latest as (
  select distinct on (source_key ->> 'band_id')
    source_key ->> 'band_id' as band_id,
    correction_id,
    corrected_values,
    original_payload_hash,
    code_version
  from public.proprietary_data_corrections
  where source_relation = 'bands'
  order by source_key ->> 'band_id', recorded_at desc, correction_id desc
)
select
  b.band_id, b.market_id, b.band_index, b.band_label,
  case when l.corrected_values ? 'band_lo' then (l.corrected_values ->> 'band_lo')::numeric else b.band_lo end as band_lo,
  case when l.corrected_values ? 'band_hi' then (l.corrected_values ->> 'band_hi')::numeric else b.band_hi end as band_hi,
  b.open_low, b.open_high, b.condition_id, b.token_yes, b.token_no,
  b.band_lo as source_band_lo, b.band_hi as source_band_hi,
  l.corrected_values ->> 'label_unit' as label_unit,
  l.correction_id, l.original_payload_hash, l.code_version as correction_version
from public.bands b
left join latest l on l.band_id = b.band_id::text;

revoke all on public.v_canonical_markets, public.v_canonical_bands
  from public, anon, authenticated;
grant select on public.v_canonical_markets, public.v_canonical_bands to service_role;

-- Make proxy skill explicit in each new probability row.  Existing rows keep
-- their original meaning and are not rewritten.
alter table public.band_probabilities
  add column if not exists skill_lead_days integer,
  add column if not exists skill_proxy boolean not null default false;

commit;
