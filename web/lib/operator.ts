import { createClient, type SupabaseClient, type Session } from "@supabase/supabase-js";
import { classifyKey, SECRET_KEY_MESSAGE } from "./keyGuard";

/**
 * THE OPERATOR'S SESSION, AND THE ONLY WAY THE BROWSER WRITES (plan v2 P1.2).
 *
 * The browser never calls a write RPC itself: every write goes to this
 * site's own API routes, which make it with the service key. Sign-in is OFF
 * for now (Hassan, 23 Sep - single user; see requireOperator). If it is turned
 * back on, the session lives in this client alone, under its own storage key,
 * so the data client in lib/supabase.ts stays anonymous, and its access token
 * is attached only to this site's /api routes.
 */

let cached: SupabaseClient | null = null;

export function authClient(): SupabaseClient {
  if (!cached) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !key) {
      throw new Error("Supabase is not configured: set NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_ANON_KEY.");
    }
    if (classifyKey(key) === "secret") throw new Error(SECRET_KEY_MESSAGE);
    cached = createClient(url, key, {
      auth: { storageKey: "ad4-operator-session", persistSession: true, autoRefreshToken: true, detectSessionInUrl: true },
    });
  }
  return cached;
}

export async function currentSession(): Promise<Session | null> {
  try {
    const { data } = await authClient().auth.getSession();
    return data.session ?? null;
  } catch {
    return null;
  }
}

/** fetch() to one of this site's routes, with the operator's session attached. */
export async function operatorFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const session = await currentSession();
  const headers = new Headers(init.headers);
  if (session?.access_token) headers.set("Authorization", `Bearer ${session.access_token}`);
  return fetch(input, { ...init, headers, cache: "no-store" });
}

export interface OperatorError { message: string; hint?: string }
export interface OperatorResult<T> { data: T | null; error: OperatorError | null }

async function post<T>(body: Record<string, unknown>): Promise<OperatorResult<T>> {
  try {
    const res = await operatorFetch("/api/operator", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const json = (await res.json().catch(() => null)) as OperatorResult<T> | null;
    if (!json) return { data: null, error: { message: `The server answered ${res.status} with no readable body.` } };
    return json;
  } catch (e) {
    return { data: null, error: { message: e instanceof Error ? e.message : String(e) } };
  }
}

/** A write RPC, through /api/operator. Same { data, error } shape as supabase.rpc. */
export function operatorRpc<T = unknown>(rpc: string, params: Record<string, unknown> = {}): Promise<OperatorResult<T>> {
  return post<T>({ op: "rpc", rpc, params });
}

export function labelBacktest(runId: string, label: string): Promise<OperatorResult<{ ok: boolean }>> {
  return post({ op: "label_backtest", run_id: runId, label });
}

export function fireWorkflow(job: string, body: Record<string, unknown> = {}):
    Promise<OperatorResult<{ status: number; ok: boolean; text: string }>> {
  return post({ op: "fire_workflow", job, body });
}

export interface WorkflowSettings {
  configured: Record<string, boolean>;
  /** The URLs - only when the caller is a signed-in operator. */
  webhooks: Record<string, { url?: string } & Record<string, unknown>> | null;
}

export async function readWorkflowSettings(): Promise<OperatorResult<WorkflowSettings>> {
  try {
    const res = await operatorFetch("/api/operator?what=workflows");
    return (await res.json()) as OperatorResult<WorkflowSettings>;
  } catch (e) {
    return { data: null, error: { message: e instanceof Error ? e.message : String(e) } };
  }
}
