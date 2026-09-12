"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  { href: "/", label: "Overview" },
  { href: "/board", label: "Board" },
  { href: "/opportunities", label: "Opportunities" },
  { href: "/paper-trades", label: "Paper Trades" },
  { href: "/predictive", label: "Predictive" },
  { href: "/strategies", label: "Strategies" },
  { href: "/clusters", label: "City Clusters" },
  { href: "/globe", label: "Globe" },
  { href: "/live", label: "Live Weather" },
  { href: "/monitor", label: "City Monitor" },
  { href: "/analytics", label: "Analytics" },
  { href: "/databank", label: "Data Bank" },
  { href: "/synthesis", label: "Synthesis" },
  { href: "/backtest", label: "Backtest" },
  { href: "/campaigns", label: "Campaigns" },
  { href: "/goals", label: "Goals" },
  { href: "/workflows", label: "Workflows" },
  { href: "/docs", label: "How it works" },
];

export default function NavTabs() {
  const pathname = usePathname();
  return (
    <nav className="flex gap-1 overflow-x-auto border-b border-border bg-panel2 px-2 text-sm">
      {TABS.map((tab) => {
        const active = tab.href === "/" ? pathname === "/" : pathname?.startsWith(tab.href);
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className={`whitespace-nowrap px-3 py-2 border-b-2 ${
              active ? "border-accent text-accent" : "border-transparent text-muted hover:text-text"
            }`}
          >
            {tab.label}
          </Link>
        );
      })}
    </nav>
  );
}
