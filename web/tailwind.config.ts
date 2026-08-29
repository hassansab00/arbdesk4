import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}", "./lib/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0b0e14",
        panel: "#12161f",
        panel2: "#171c27",
        border: "#232a38",
        text: "#e6e9f0",
        muted: "#8a93a6",
        accent: "#4f8cff",
        good: "#2ecc71",
        bad: "#ff5c5c",
        warn: "#ffb020",
      },
      fontFamily: {
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
    },
  },
  plugins: [],
};
export default config;
