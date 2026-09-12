-- Optional catalogue metadata; P2.2 uses authenticated event/manual triggers.
-- Do not put its bearer credential or a credential-bearing URL in settings.
do $$ begin
  if to_regclass('public.settings') is null then return; end if;
  update public.settings set value=value || jsonb_build_object('P2.2_paper_trades',
    jsonb_build_object('mode','manual','every_minutes',0))
    where key='workflow_schedules' and not (value ? 'P2.2_paper_trades');
end $$;
