-- ===========================================================================
-- Task 11 - settlement RPC + supporting columns.
-- scripts/settlement.py does the external fetch-and-verify (Postgres can't
-- make outbound HTTP calls); this RPC does the atomic DB-side write once a
-- settlement value has been verified. Genuine SQL, not a stub - see
-- docs/architecture_deviations.md for which Task 11 pieces are real vs why.
-- ===========================================================================

alter table markets add column if not exists settled_value numeric;
alter table markets add column if not exists winning_band_id uuid references bands(band_id);
alter table markets add column if not exists resolution_verified_at timestamptz;
alter table markets add column if not exists resolution_source_used text;
alter table markets add column if not exists dispute_flag boolean default false;

insert into settings (key, value) values
  ('settlement_verified', '{"value": false, "origin":"claude_invented", "note":"Gates auto-settlement. Set true only after docs/settlement_verification.md spot-check passes with real network access - the resolution-source parser in scripts/settlement.py has not been confirmed against a live page from this build session (sandboxed, no weather.gov egress)."}'::jsonb)
on conflict (key) do nothing;

create or replace function settle_markets(
  p_market_id uuid, p_settled_value numeric, p_winning_band_id uuid,
  p_source text, p_verified_at timestamptz
) returns integer language plpgsql security definer as $$
declare
  v_trade record;
  v_exit_price numeric;
  v_gross numeric;
  v_net numeric;
  v_count integer := 0;
begin
  update markets
  set closed = true, settled_value = p_settled_value, winning_band_id = p_winning_band_id,
      resolution_verified_at = p_verified_at, resolution_source_used = p_source
  where market_id = p_market_id;

  for v_trade in
    select pt.* from paper_trades pt
    join bands b on b.band_id = pt.band_id
    where b.market_id = p_market_id and pt.closed_at is null
  loop
    v_exit_price := case
      when v_trade.band_id = p_winning_band_id then (case when v_trade.side = 'YES' then 1.0 else 0.0 end)
      else (case when v_trade.side = 'YES' then 0.0 else 1.0 end)
    end;
    v_gross := v_trade.shares * (v_exit_price - v_trade.avg_fill_price);
    -- Settlement itself is free (fee curve -> 0 at p=0/p=1); net P&L only
    -- carries costs already paid at entry (fee_paid/gas_paid), never a
    -- second charge here.
    v_net := v_gross - coalesce(v_trade.fee_paid, 0) - coalesce(v_trade.gas_paid, 0);

    update paper_trades
    set closed_at = now(), exit_price = v_exit_price, gross_pnl = v_gross, net_pnl = v_net
    where paper_trades.strategy_id = v_trade.strategy_id and paper_trades.band_id = v_trade.band_id
      and paper_trades.opened_at = v_trade.opened_at;

    insert into ledger (stage, strategy_id, band_id, regime_label, forecast_version,
                         calibration_version, cost_version, detail, recorded_at)
    values ('settlement', v_trade.strategy_id, v_trade.band_id, v_trade.regime_label,
            v_trade.forecast_version, v_trade.calibration_version, v_trade.cost_version,
            jsonb_build_object('exit_price', v_exit_price, 'gross_pnl', v_gross, 'net_pnl', v_net,
                                'settled_value', p_settled_value, 'source', p_source),
            now());

    v_count := v_count + 1;
  end loop;

  return v_count;
end;
$$;

-- Thin compatibility stub for Revision A's n8n `evaluate_signals`/
-- `compute_edges` naming (§7.6/§7.7) - the real implementations are
-- scripts/edge_engine.py and scripts/signals.py (Python), run via GitHub
-- Actions. See docs/architecture_deviations.md.
create or replace function compute_edges() returns void language plpgsql as $$
begin
  raise notice 'compute_edges: no-op - edges are computed by scripts/edge_engine.py via GitHub Actions, not this RPC. See docs/architecture_deviations.md.';
end;
$$;

create or replace function evaluate_signals() returns void language plpgsql as $$
begin
  raise notice 'evaluate_signals: no-op - signals are evaluated by scripts/signals.py via GitHub Actions, not this RPC. See docs/architecture_deviations.md.';
end;
$$;
