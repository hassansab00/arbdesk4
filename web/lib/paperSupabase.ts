import { createClient, type SupabaseClient } from '@supabase/supabase-js';
import { classifyKey, SECRET_KEY_MESSAGE } from './keyGuard';

// A separate session leaves the existing public desk's reads unchanged.
let client: SupabaseClient | null = null;
export function paperClient(): SupabaseClient {
  if (!client) {
    const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
    const key = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
    if (!url || !key) throw new Error('Supabase is not configured.');
    if (classifyKey(key) === 'secret') throw new Error(SECRET_KEY_MESSAGE);
    client = createClient(url, key, { auth: { storageKey: 'arbdesk-paper-session' } });
  }
  return client;
}

/** Wake the server-side paper worker without exposing its bearer token. */
export async function runPaperWorker(): Promise<{ orders_completed: number }> {
  const { data, error } = await paperClient().auth.getSession();
  if (error) throw error;
  const token = data.session?.access_token;
  if (!token) throw new Error('Desk owner sign-in required.');
  const response = await fetch('/api/paper-cycle', {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(typeof payload?.error === 'string'
      ? payload.error
      : 'Paper worker could not be reached. The order remains queued.');
  }
  return payload as { orders_completed: number };
}
