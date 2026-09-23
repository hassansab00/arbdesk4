-- WRITES NEED AN OPERATOR (plan v2, step P1.2).
--
-- The site's write RPCs were granted to anon by name (sql/ad4_38 section 5b,
-- ad4_14, ad4_32, ad4_33), so anyone with the public anon key - which ships in
-- the browser bundle - could change settings, flip strategies, queue
-- backtests and edit campaigns. And settings.n8n_webhooks, readable by anon,
-- handed out the URL of every n8n job.
--
-- The browser now writes only through web/app/api/operator (and the paper,
-- run, cycle and export routes), which check a Supabase session against
-- settings.operators and call the RPC with the service key. So:
--
--   1. settings.operators: the list of operator emails, a JSON array. Seeded
--      empty; an empty list means nobody can write from the site, which is
--      the safe default. Add an email with SQL:
--        update public.settings set value = value || '["you@example.com"]'::jsonb
--         where key = 'operators';
--   2. settings' anon policy no longer returns operators or n8n_webhooks.
--   3. anon AND authenticated lose EXECUTE on every write RPC. authenticated
--      too, because anyone can create a Supabase Auth account for themselves;
--      a session proves who you are, not that you may write. service_role
--      keeps it.
--
-- APPLY ORDER: after the web deploy that sends writes through the routes,
-- and after an operator has been added and can sign in - otherwise the
-- board's write buttons stop working in between.
--
-- Idempotent.

-- 1. The operator list -------------------------------------------------------
insert into public.settings (key, value)
values ('operators', '[]'::jsonb)
on conflict (key) do nothing;

-- 2. What the browser may read from settings -----------------------------------
alter table public.settings enable row level security;
drop policy if exists anon_read on public.settings;
create policy anon_read on public.settings for select to anon
  using (key not in ('operators', 'n8n_webhooks'));

-- 3. Write RPCs: service_role only ----------------------------------------------
do $$
declare sig text;
begin
  foreach sig in array array[
    -- the writes the site makes, now through /api/operator
    'public.update_setting(text, jsonb)',
    'public.set_strategy_enabled(text, boolean)',
    'public.set_run_scope(text, text[])',
    'public.queue_backtest(jsonb)',
    'public.upsert_deployment(jsonb)',
    'public.set_deployment_status(uuid, text)',
    -- writes nothing on the site calls (grep of web/, scripts/, n8n/, 23 Sep)
    'public.approve_signal(bigint)',
    'public.dismiss_signal(bigint)',
    'public.close_position(uuid, numeric, text)',
    'public.log_paper_trade(jsonb)',
    'public.calc_recommendation(jsonb)',
    -- desk management, reached through /api/paper-desk with the service key
    'public.paper_desk_create(text, numeric, text, uuid, jsonb)',
    'public.paper_desk_update(uuid, text, numeric, text, boolean, jsonb)']
  loop
    if to_regprocedure(sig) is not null then
      execute format('revoke execute on function %s from public, anon, authenticated', sig);
      execute format('grant execute on function %s to service_role', sig);
    end if;
  end loop;
end $$;
