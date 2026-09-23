import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * WHO MAY CHANGE THINGS (plan v2 P1.2). Server-side only.
 *
 * Every write the site makes used to be an RPC the browser called with the
 * public anon key, or an API route that checked only the Origin header, which
 * any script can set. So anyone with the site's URL could change settings,
 * switch strategies, queue backtests and drive the paper desk.
 *
 * Now a write needs a signed-in Supabase session whose email is on the
 * operator list in settings.operators, a row the browser roles cannot read.
 * The session is checked here, on the server, and only then does the route
 * call the RPC with the service key. Reading still needs no sign-in.
 *
 * `authorize` is the whole decision, with its I/O passed in, so it can be
 * tested without a network (web/tests/operator-auth.test.cjs).
 */

export type Verdict =
  | { ok: true; email: string }
  | { ok: false; status: 401 | 403 | 503; message: string };

export interface OperatorDeps {
  /** The email the token was issued to, or null if the token is not valid. */
  emailForToken(token: string): Promise<string | null>;
  /** settings.operators, as a list of emails. */
  operators(): Promise<string[]>;
}

export function bearerToken(request: Request): string | null {
  const header = (request.headers.get("authorization") || "").trim();
  const m = /^Bearer\s+(\S+)$/i.exec(header);
  return m ? m[1] : null;
}

export function normaliseOperators(value: unknown): string[] {
  const list = Array.isArray(value) ? value : [];
  return list
    .filter((x): x is string => typeof x === "string")
    .map((x) => x.trim().toLowerCase())
    .filter(Boolean);
}

export async function authorize(token: string | null, deps: OperatorDeps): Promise<Verdict> {
  if (!token) {
    return { ok: false, status: 401, message: "Sign in to change anything. Reading needs no sign-in." };
  }
  let email: string | null = null;
  try {
    email = await deps.emailForToken(token);
  } catch {
    email = null;
  }
  if (!email) {
    return { ok: false, status: 401, message: "Your sign-in has expired or is not valid. Sign in again." };
  }
  const operators = normaliseOperators(await deps.operators());
  if (!operators.length) {
    return {
      ok: false, status: 403,
      message: "No operators are configured. Add an email to settings.operators (a JSON array).",
    };
  }
  if (!operators.includes(email.trim().toLowerCase())) {
    return { ok: false, status: 403, message: `${email} is not an operator of this desk.` };
  }
  return { ok: true, email };
}

export function serviceClient(): SupabaseClient | null {
  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_KEY;
  if (!url || !key) return null;
  return createClient(url, key, { auth: { persistSession: false, autoRefreshToken: false } });
}

/**
 * The gate every write route calls first. No token is a 401 before anything
 * else is looked at, so a forged Origin with no session learns nothing about
 * how the server is configured.
 */
export async function requireOperator(request: Request): Promise<Verdict> {
  const token = bearerToken(request);
  if (!token) return authorize(null, { emailForToken: async () => null, operators: async () => [] });
  const client = serviceClient();
  if (!client) {
    return {
      ok: false, status: 503,
      message: "Writes are not configured on this server: SUPABASE_SERVICE_KEY is not set.",
    };
  }
  return authorize(token, {
    emailForToken: async (t) => {
      const { data, error } = await client.auth.getUser(t);
      return error ? null : data.user?.email ?? null;
    },
    operators: async () => {
      const { data } = await client.from("settings").select("value").eq("key", "operators").maybeSingle();
      return normaliseOperators(data?.value);
    },
  });
}
