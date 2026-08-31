import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { classifyKey, SECRET_KEY_MESSAGE } from "./keyGuard";

// Anon key only, never the service key (Revision A §6.3/§9). Every table
// read through this client is behind an RLS policy from sql/ad4_rls.sql;
// every write goes through an RPC (calc_recommendation, log_paper_trade,
// approve_signal, close_position, queue_backtest, ...), never a direct
// insert/update on a table from here.
//
// Lazily initialised: every page in web/app/ is a client component that
// only touches `supabase` inside useEffect (i.e. in the browser, after
// hydration) - but Next.js's `next build` still statically prerenders the
// initial HTML shell for each route, which imports this module. If the
// client were constructed at module scope, `createClient(undefined!,
// undefined!)` throws "supabaseUrl is required." during that prerender
// step whenever NEXT_PUBLIC_SUPABASE_URL/ANON_KEY aren't present in the
// build environment - which killed the Vercel build. Deferring
// construction to first actual property access (via this Proxy) means
// prerendering, which never calls into useEffect, never touches it; the
// real client is built on first real use in the browser, where Vercel's
// build-time-inlined env vars are already baked into the bundle.
let cached: SupabaseClient | null = null;

function getClient(): SupabaseClient {
  if (!cached) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !key) {
      throw new Error(
        "Supabase is not configured: set NEXT_PUBLIC_SUPABASE_URL and " +
          "NEXT_PUBLIC_SUPABASE_ANON_KEY (see web/.env.local.example)."
      );
    }
    // Never send a secret key from a browser. Supabase's API rejects it too,
    // but by then it has already been shipped to every visitor - refusing
    // here means no request is attempted and the UI states the real problem.
    if (classifyKey(key) === "secret") {
      throw new Error(SECRET_KEY_MESSAGE);
    }
    cached = createClient(url, key);
  }
  return cached;
}

export const supabase: SupabaseClient = new Proxy({} as SupabaseClient, {
  get(_target, prop, _receiver) {
    const real = getClient();
    const value = Reflect.get(real, prop, real);
    return typeof value === "function" ? value.bind(real) : value;
  },
});
