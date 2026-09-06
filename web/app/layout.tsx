import type { Metadata } from "next";
import "./globals.css";
import ConfigBanner from "@/components/ConfigBanner";
import Header from "@/components/Header";
import GlobalBar from "@/components/GlobalBar";
import NavTabs from "@/components/NavTabs";
import RightRail from "@/components/RightRail";
import { DataHealthStrip } from "@/components/Provenance";

export const metadata: Metadata = {
  title: "ArbDesk — temperature markets",
  description: "Weather-based quantitative trading desk for Polymarket daily temperature markets",
};

/**
 * An APP SHELL, not a document.
 *
 * This was `<div className="flex"><main/><RightRail/></div>` with the page
 * scrolling normally, which had one consequence that made the whole right-hand
 * column useless: the rail sat in document flow, so scrolling the Board or
 * Opportunities scrolled the Signals panel off the top of the screen. A panel
 * you can only see at scroll position zero is not a panel, and no amount of
 * improving what it SAYS could fix that - which is why five rounds of edits to
 * its contents changed nothing.
 *
 * The shell is now viewport-height: the header stack is fixed at the top, and
 * the row below it is the only thing that scrolls - main and the rail each
 * scrolling independently. `min-h-0` on the row is what lets a flex child
 * actually scroll instead of growing past its parent; without it the whole
 * thing silently reverts to the old behaviour.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="flex h-screen flex-col overflow-hidden bg-ground font-sans text-text">
        <ConfigBanner />
        <Header />
        <GlobalBar />
        <NavTabs />
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-y-auto p-4">
            {/*
              WHETHER THE DESK'S INPUTS ARE CURRENT, on every page, collapsed.
              "The data is outdated" was a feeling with nothing behind it: no
              page said which table had stopped being written or which job was
              meant to write it. This is that list, one line until you open it.
            */}
            <div className="mb-4">
              <DataHealthStrip />
            </div>
            {children}
          </main>
          <RightRail />
        </div>
      </body>
    </html>
  );
}
