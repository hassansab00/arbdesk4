import { createClient } from "@supabase/supabase-js";

// Anon key only, never the service key (Revision A §6.3/§9). Every table
// read through this client is behind an RLS policy from sql/ad4_rls.sql;
// every write goes through an RPC (calc_recommendation, log_paper_trade,
// approve_signal, close_position, queue_backtest), never a direct
// insert/update on a table from here.
export const supabase = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!
);
