-- Phase 1B follow-up: normalize unambiguous symbolic tail labels such as
-- "<29°F".  The collected source row remains untouched; this appends the
-- interpretation and updates only the private canonical view.
begin;

with parsed as (
  select
    b.*,
    to_jsonb(b) as original_payload,
    regexp_match(
      b.band_label,
      '^\s*(<=|>=|<|>|≤|≥)\s*(-?[0-9]+(?:\.[0-9]+)?)\s*°?([CF])\s*$',
      'i'
    ) as part
  from public.bands b
), expected as (
  select
    p.*,
    case when part[1] in ('<', '<=', '≤') then null
         when part[1] = '>' then (part[2])::numeric + 1
         else (part[2])::numeric end as expected_lo,
    case when part[1] = '<' then (part[2])::numeric
         when part[1] in ('<=', '≤') then (part[2])::numeric + 1
         else null end as expected_hi,
    part[1] in ('<', '<=', '≤') as expected_open_low,
    part[1] in ('>', '>=', '≥') as expected_open_high,
    upper(part[3]) as label_unit
  from parsed p
  where part is not null
)
insert into public.proprietary_data_corrections(
  source_relation, source_key, original_payload_hash, corrected_values,
  reason, evidence, code_version
)
select
  'bands',
  jsonb_build_object('band_id', e.band_id),
  md5(e.original_payload::text),
  jsonb_build_object(
    'band_lo', e.expected_lo,
    'band_hi', e.expected_hi,
    'open_low', e.expected_open_low,
    'open_high', e.expected_open_high,
    'label_unit', e.label_unit,
    'interval_semantics', 'whole-number lattice [lo,hi)'
  ),
  'Normalize symbolic tail label to half-open whole-number lattice bounds',
  jsonb_build_object(
    'band_label', e.band_label,
    'original_band_lo', e.band_lo,
    'original_band_hi', e.band_hi,
    'original_open_low', e.open_low,
    'original_open_high', e.open_high,
    'operator', e.part[1],
    'parser', 'ad4-symbolic-tail-v1'
  ),
  'phase1b-symbolic-tail-v1'
from expected e
where not exists (
  select 1
  from public.proprietary_data_corrections x
  where x.source_relation = 'bands'
    and x.source_key ->> 'band_id' = e.band_id::text
    and x.code_version = 'phase1b-symbolic-tail-v1'
);

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
  case when l.corrected_values ? 'open_low' then (l.corrected_values ->> 'open_low')::boolean else b.open_low end as open_low,
  case when l.corrected_values ? 'open_high' then (l.corrected_values ->> 'open_high')::boolean else b.open_high end as open_high,
  b.condition_id, b.token_yes, b.token_no,
  b.band_lo as source_band_lo, b.band_hi as source_band_hi,
  l.corrected_values ->> 'label_unit' as label_unit,
  l.correction_id, l.original_payload_hash, l.code_version as correction_version
from public.bands b
left join latest l on l.band_id = b.band_id::text;

revoke all on public.v_canonical_bands from public, anon, authenticated;
grant select on public.v_canonical_bands to service_role;

commit;
