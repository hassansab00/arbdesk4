-- ===========================================================================
-- STRATEGY LEARNING IS SWITCHED ON (Hassan, 7 Oct: "turn learning on").
--
-- WHY. No engine strategy bought in the 7 days to 7 Oct: every decision other
-- than NONE was S10's own WAIT, and the seven shadow desks placed 0 orders.
-- Every engine view starts from the market (market_anchor, Hassan 27 Sep:
-- p = p_market + w (p_model - p_market)) and w moves off 0 only by learning,
-- which has been off: settings.strategy_learning false since it was created
-- (20260926110000), and the nightly step stopped on 5 Oct (20261005190000).
--
-- WHAT THE TICK READS ONCE IT IS ON:
--   * the market weight per view and checkpoint class (market_anchor): off
--     its prior 0 only after 40 settled days - the first 20 only choose a
--     weight, the next 20 score it forward (market_anchor._forward; the test
--     holds 39 and passes 40; Codex on #332) - with the forward gain's lower
--     90% bound above zero and a gain over the latest days, at most 0.05 a
--     night; per city only after its own days. Its own out-of-sample check.
--     On 5 Oct every scope was still held at 0, with 8 to 11 settled days;
--   * the city clusters: they only tighten the cluster rail.
--   The tick does not load the belief maps (engine_shadow passes the clusters
--   and the anchor table only).
-- NOTHING REACHES CAPITAL: meta_allocator reports a strategy through the
-- P5.10 gate and Hassan promotes (#331).
--
-- 1. clock_expected_jobs: P5.8_strategy_learn is expected again, as seeded
--    (20260930001000); 20261005190000 removed it.
-- 2. data_freshness_spec: strategy_params is fresh within 30 h again, as
--    before the stop (sql/ad4_39_freshness.sql says the same).
--    v_data_freshness writes each limit into its own definition, so it is
--    rebuilt from that file's generator live after this migration, as the
--    stop's was (Codex on #332). Done 7 Oct, after this migration, with
--    CREATE OR REPLACE in
--    one REPEATABLE READ transaction: EXCEPT ALL both ways, exactly the
--    strategy_params row differed, every other table's row identical; anon
--    reads all 68 rows.
-- 3. settings.strategy_learning: enabled, with why.
-- Re-runnable. tests/database/strategy-learning-on.cjs holds it.
-- ===========================================================================
begin;

insert into public.clock_expected_jobs (file, job, within_minutes, note)
values ('pipeline_daily.yml', 'P5.8_strategy_learn', 105, null)
on conflict (file, job) do nothing;

do $$
begin
  if to_regclass('public.data_freshness_spec') is not null then
    update public.data_freshness_spec
       set fresh_hours   = 30,
           plain_english = 'What the nightly learning loop fitted - the market weights, the city clusters and the belief maps - one row per version, with the prior and bounds each was held to. Learning is on (7 Oct): the tick reads the market weights and the clusters.'
     where table_name = 'strategy_params';
  end if;
end $$;

insert into public.settings (key, value)
values ('strategy_learning', jsonb_build_object(
  'enabled', true,
  'why', 'Switched on 7 Oct (Hassan: "turn learning on"). The tick reads the market weights, each off 0 only on its own walk-forward evidence (40 settled days: 20 to choose a weight, 20 more scored forward; a lower 90% bound above zero; at most 0.05 a night), and the city clusters, which only tighten the cluster rail. Nothing reaches capital: meta_allocator reports, Hassan promotes.'))
on conflict (key) do update set value = excluded.value, updated_at = now();

commit;
