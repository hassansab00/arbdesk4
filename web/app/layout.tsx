import type { Metadata } from "next";
import "./globals.css";
import ConfigBanner from "@/components/ConfigBanner";
import Header from "@/components/Header";
import GlobalBar from "@/components/GlobalBar";
import NavTabs from "@/components/NavTabs";
import SignalsPanel from "@/components/SignalsPanel";

export const metadata: Metadata = {
  title: "ArbDesk — temperature markets",
  description: "Weather-based quantitative trading desk for Polymarket daily temperature markets",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-base text-text font-sans min-h-screen">
        <ConfigBanner />
        <Header />
        <GlobalBar />
        <NavTabs />
        <div className="flex">
          <main className="flex-1 min-w-0 p-4">{children}</main>
          <SignalsPanel />
        </div>
      </body>
    </html>
  );
}
