"use client";

/**
 * Last-resort boundary: catches anything thrown in the root layout itself
 * (the global bar, the nav, the signals panel), where app/error.tsx cannot
 * reach. It has to render its own <html>/<body>.
 */
export default function GlobalError({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <html lang="en">
      <body style={{ background: "#0b0e14", color: "#e6e9f0", fontFamily: "ui-sans-serif, system-ui", margin: 0, padding: 32 }}>
        <h1 style={{ fontSize: 18, color: "#ff5c5c" }}>AD4 failed to start</h1>
        <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", background: "rgba(255,92,92,0.1)", border: "1px solid rgba(255,92,92,0.5)", padding: 12, fontSize: 12, color: "#ff5c5c" }}>
          {error.message}
          {error.digest ? `\n\ndigest: ${error.digest}` : ""}
        </pre>
        <p style={{ fontSize: 12, color: "#8a93a6" }}>
          If this says Supabase is not configured, set NEXT_PUBLIC_SUPABASE_URL and
          NEXT_PUBLIC_SUPABASE_ANON_KEY in Vercel → Settings → Environment Variables and redeploy.
        </p>
        <button onClick={reset} style={{ background: "transparent", color: "#8a93a6", border: "1px solid #232a38", borderRadius: 4, padding: "6px 12px", fontSize: 13 }}>
          Try again
        </button>
      </body>
    </html>
  );
}
