-- ===========================================================================
-- THE SEASONAL FOCUS 10 IS RECORDED BEFORE ITS OUTCOMES (WXPredict build,
-- wave F, step F.1; Hassan, 6 Oct: "Record the shortlist and its start date
-- now, then evaluate subsequent outcomes ... without choosing cities
-- retrospectively because they happened to win").
--
-- One row per focus set, append-only. The cities were chosen on 6 Oct 2026 by
-- tools/focus/season_predictability.py (data before 2026-09-01 only, the
-- sealed test untouched), whose output is committed as
-- data/eval/focus/season_predictability_2026-10-06.json; method_sha256 is that
-- file's sha256, so the row and the repository can be checked against each
-- other. docs/FOCUS_PREREG.md states how the set is evaluated.
--
-- evaluate_from is the first target date every one of whose calls, the
-- day-ahead call included, is made after this record: 8 Oct. (A 7 Oct
-- day-ahead call was frozen on the evening of 6 Oct, local time, which for
-- most cities had already passed.)
--
-- MEMBERSHIP NEVER MOVES A PRICE. Nothing that prices reads this table: it
-- filters what the Predictive page shows and what the comparison scores
-- (tests/test_focus_set.py checks that no pricing script reads it).
-- ===========================================================================
begin;

create table if not exists public.focus_sets (
  set_id        text primary key,
  label         text not null,
  city_keys     text[] not null check (cardinality(city_keys) between 1 and 20),
  window_from   date not null,
  window_to     date not null,
  evaluate_from date not null,
  method_path   text not null,
  method_sha256 text not null check (method_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at   timestamptz not null default clock_timestamp(),
  check (window_from <= evaluate_from and evaluate_from <= window_to)
);

comment on table public.focus_sets is
  'Each focus shortlist of cities, recorded before the outcomes it is judged on (WXPredict build F.1; docs/FOCUS_PREREG.md). Append-only. Filters what is shown and scored; never read by anything that prices.';

drop trigger if exists focus_sets_immutable on public.focus_sets;
create trigger focus_sets_immutable before update or delete on public.focus_sets
  for each row execute function arbdesk_private.immutable_record();
drop trigger if exists focus_sets_no_truncate on public.focus_sets;
create trigger focus_sets_no_truncate before truncate on public.focus_sets
  for each statement execute function arbdesk_private.immutable_record();

alter table public.focus_sets enable row level security;
revoke all on public.focus_sets from public, anon, authenticated, service_role;
grant select on public.focus_sets to anon, authenticated;
grant select, insert on public.focus_sets to service_role;
drop policy if exists focus_sets_read on public.focus_sets;
create policy focus_sets_read on public.focus_sets for select to anon, authenticated, service_role using (true);
drop policy if exists focus_sets_insert on public.focus_sets;
create policy focus_sets_insert on public.focus_sets for insert to service_role with check (true);

insert into public.focus_sets
  (set_id, label, city_keys, window_from, window_to, evaluate_from, method_path, method_sha256)
values
  ('seasonal-2026-10-06', 'Seasonal Focus 10',
   array['lucknow','karachi','helsinki','wellington','tel_aviv','milan','chicago','moscow','miami','amsterdam'],
   date '2026-10-06', date '2026-11-30', date '2026-10-08',
   'data/eval/focus/season_predictability_2026-10-06.json',
   'b02509634235cd25ef75310b8b89f2a046b178cf5c44946d7f95974f0d87655e')
on conflict (set_id) do nothing;

commit;
