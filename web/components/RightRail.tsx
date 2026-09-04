"use client";

import { useState } from "react";
import SignalsPanel from "@/components/SignalsPanel";
import CityWatch from "@/components/CityWatch";

/**
 * The right-hand column: what fired, and what you are keeping an eye on.
 *
 * Signals is reactive - it tells you when something happened. City Watch is
 * standing - it tells you the state of the places you care about whether or
 * not anything has happened. They belong in the same column because they are
 * both "glance", not "work", and one collapse should take both away.
 */
export default function RightRail() {
  const [collapsed, setCollapsed] = useState(false);

  if (collapsed) {
    return (
      <button
        onClick={() => setCollapsed(false)}
        className="fixed right-0 top-32 z-20 rounded-l border border-r-0 border-border bg-panel px-2 py-3 text-xs text-muted hover:text-text"
      >
        Signals &amp; Watch
      </button>
    );
  }

  return (
    <aside className="hidden w-96 shrink-0 flex-col border-l border-border bg-panel lg:flex">
      <SignalsPanel onHide={() => setCollapsed(true)} />
      <CityWatch />
    </aside>
  );
}
