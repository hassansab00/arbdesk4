-- The lineup reads only band_id and band_label. Canonical bands passes both
-- through unchanged; joining its correction overlay defeats the label join's
-- hash plan and compared 32,126,870 rows for 1,000 calls on 16 Oct.
-- Do not touch ranking, local-day withholding, registry blindness or scores.
do $$
declare definition text; options text;
begin
  if to_regclass('public.v_prediction_lineup') is null
     or to_regclass('public.bands') is null then return; end if;
  definition := pg_get_viewdef('public.v_prediction_lineup'::regclass,true);
  definition := replace(replace(definition,'public.v_canonical_bands tb','public.bands tb'),'v_canonical_bands tb','public.bands tb');
  definition := replace(replace(definition,'public.v_canonical_bands wb','public.bands wb'),'v_canonical_bands wb','public.bands wb');
  -- Only the canonical lowercase UUID text can match uuid::text. Guard the
  -- cast so malformed/noncanonical ids keep their previous unmatched result.
  definition := replace(definition,'tb.band_id::text = s.top_band_id',
    'tb.band_id = case when s.top_band_id ~ ''^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$'' then s.top_band_id::uuid end');
  definition := replace(definition,'wb.band_id::text = s.winner_band_id',
    'wb.band_id = case when s.winner_band_id ~ ''^[a-f0-9]{8}(-[a-f0-9]{4}){3}-[a-f0-9]{12}$'' then s.winner_band_id::uuid end');
  select coalesce(' with (' || array_to_string(reloptions,', ') || ')','') into options
    from pg_class where oid='public.v_prediction_lineup'::regclass;
  execute format('create or replace view public.v_prediction_lineup%s as %s',options,definition);
end $$;
