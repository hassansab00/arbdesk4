-- Phase 1D follow-up: cover the immutable evidence supersession foreign key.
-- This is additive metadata only and does not update collected evidence.
begin;

create index if not exists city_metadata_evidence_supersedes_idx
  on public.city_metadata_evidence (supersedes)
  where supersedes is not null;

commit;
