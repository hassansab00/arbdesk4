"use client";

import { useEffect, useState } from "react";
import { fmtAge } from "@/lib/format";

/**
 * Which build you are looking at.
 *
 * Without this, "nothing changed" is indistinguishable from "the deploy is
 * still running", "it went to a preview, not production", and "the push never
 * triggered a build" - and the only way to tell them apart is a trip to the
 * Vercel dashboard. The commit and the build time are stamped into the bundle
 * at build time (next.config.js), so the page can simply say.
 *
 * Amber when the build is not production: the most confusing case of all is
 * looking at a preview URL and wondering why production has not moved.
 */
export default function BuildStamp() {
  const sha = process.env.NEXT_PUBLIC_BUILD_SHA ?? "unknown";
  const at = process.env.NEXT_PUBLIC_BUILD_TIME ?? null;
  const env = process.env.NEXT_PUBLIC_BUILD_ENV ?? "unknown";
  const msg = process.env.NEXT_PUBLIC_BUILD_MSG ?? "";

  // Rendered client-side only: the age is relative to now, and a
  // server-rendered "2m ago" would hydrate into a mismatch.
  const [ready, setReady] = useState(false);
  useEffect(() => setReady(true), []);

  const preview = env !== "production" && env !== "local";

  return (
    <span
      className={`hidden font-mono text-[10px] sm:inline ${preview ? "text-warn" : "text-muted"}`}
      title={
        `Build ${sha}` +
        (msg ? `\n${msg}` : "") +
        (at ? `\nbuilt ${at}` : "") +
        `\nenvironment: ${env}` +
        (preview
          ? "\n\nThis is a PREVIEW build, not production. Production is whatever was last deployed from the production branch."
          : "")
      }
    >
      {preview && <span className="mr-1 uppercase tracking-wide">preview</span>}
      {sha}
      {ready && at ? <span className="ml-1 opacity-70">· built {fmtAge(at)}</span> : null}
    </span>
  );
}
