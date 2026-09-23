"use client";

import { useEffect, useState } from "react";
import { authClient, signInWithEmail, signOut } from "@/lib/operator";

/**
 * Sign in to change things (plan v2 P1.2). Reading needs no sign-in; every
 * write on the site goes through a server route that checks this session
 * against settings.operators. The link is emailed by Supabase Auth, and only
 * to an address that already has an account - there is no sign-up here.
 */
export default function OperatorSignIn() {
  const [email, setEmail] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let client;
    try {
      client = authClient();
    } catch {
      setReady(true);
      return;
    }
    client.auth.getSession().then(({ data }) => {
      setEmail(data.session?.user?.email ?? null);
      setReady(true);
    });
    const { data: sub } = client.auth.onAuthStateChange((_event, session) => {
      setEmail(session?.user?.email ?? null);
    });
    return () => sub.subscription.unsubscribe();
  }, []);

  if (!ready) return null;

  if (email) {
    return (
      <span className="inline-flex items-center gap-2 text-xs text-muted">
        <span title="Signed in: writes on this site are allowed if this email is an operator">{email}</span>
        <button type="button" className="text-accent hover:underline" onClick={() => signOut()}>
          sign out
        </button>
      </span>
    );
  }

  if (!open) {
    return (
      <button type="button" className="text-xs text-accent hover:underline"
        title="Reading needs no sign-in. Changing anything does."
        onClick={() => setOpen(true)}>
        sign in to change things
      </button>
    );
  }

  return (
    <form
      className="inline-flex items-center gap-1 text-xs"
      onSubmit={async (e) => {
        e.preventDefault();
        if (!draft.trim()) return;
        setBusy(true);
        const error = await signInWithEmail(draft);
        setBusy(false);
        setMsg(error ? `Could not send the link: ${error}` : "Check your email for the sign-in link.");
      }}
    >
      <input
        type="email" required value={draft} onChange={(e) => setDraft(e.target.value)}
        placeholder="operator email" aria-label="Operator email"
        className="w-44 rounded border border-border bg-ground px-2 py-0.5 text-text"
      />
      <button type="submit" disabled={busy} className="rounded border border-accent px-2 py-0.5 text-accent">
        {busy ? "sending…" : "send link"}
      </button>
      <button type="button" className="text-muted" onClick={() => { setOpen(false); setMsg(null); }}>
        cancel
      </button>
      {msg && <span className="ml-1 text-muted">{msg}</span>}
    </form>
  );
}
