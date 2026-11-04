/** Pause automatic reads in hidden tabs and never overlap one poller's reads.
 * The caller owns error reporting and state; explicit refresh remains available.
 */
export function startVisiblePolling(
  task: () => void | Promise<void>, intervalMs?: number, immediate = true
): () => void {
  let stopped = false, running = false;
  let startedAt: number | null = immediate ? null : Date.now();
  const visible = () => typeof document === "undefined" || document.visibilityState !== "hidden";
  const start = () => {
    if (stopped || running || !visible()) return;
    running = true;
    // A rejected task must also release the lock. Callers display their errors.
    void Promise.resolve().then(() => {
      if (stopped || !visible()) return;
      startedAt = Date.now();
      return task();
    }).catch(() => undefined).finally(() => { running = false; });
  };
  const resume = () => {
    if (startedAt === null || (intervalMs && Date.now() - startedAt >= intervalMs)) start();
  };
  if (immediate) start();
  const timer = intervalMs && intervalMs > 0 ? setInterval(start, intervalMs) : null;
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", resume);
  return () => {
    stopped = true;
    if (timer !== null) clearInterval(timer);
    if (typeof document !== "undefined") document.removeEventListener("visibilitychange", resume);
  };
}
