/** @type {import('next').NextConfig} */

// Which commit is actually serving this page, stamped at build time.
//
// "Did my change deploy?" has been unanswerable from the browser: a page that
// looks unchanged is identical whether the deploy is still building, went to a
// preview instead of production, or never triggered at all. Vercel knows all
// three, and passes them to the build as environment variables - they just
// were not reaching the bundle. `env` inlines them, so the running page can
// state its own provenance.
//
// Falls back to a git call for local builds, and to "local" if even that
// fails - a build must never break over a version string.
function localSha() {
  try {
    return require("child_process")
      .execSync("git rev-parse --short HEAD", { stdio: ["ignore", "pipe", "ignore"] })
      .toString().trim();
  } catch {
    return "local";
  }
}

const nextConfig = {
  reactStrictMode: true,
  env: {
    NEXT_PUBLIC_BUILD_SHA: (process.env.VERCEL_GIT_COMMIT_SHA || localSha()).slice(0, 7),
    NEXT_PUBLIC_BUILD_TIME: new Date().toISOString(),
    NEXT_PUBLIC_BUILD_ENV: process.env.VERCEL_ENV || "local",
    NEXT_PUBLIC_BUILD_MSG: (process.env.VERCEL_GIT_COMMIT_MESSAGE || "").split("\n")[0].slice(0, 90),
  },
};

module.exports = nextConfig;
