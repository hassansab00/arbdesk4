# Bounded weather recovery

The existing intraday workflow retries recent final authority evidence after
venue confirmation and banking, with an explicit request budget. It skips
dry runs and does not fit parameters or write prediction checkpoints. The
daily collector retains its existing full recovery path.

Authority requests share provider cooldowns across city-days and bound their
requests and retry waits by the recovery deadline. Longer Retry-After values
defer work. Forward weather requests use a shorter optional timeout, preserving
time for missing chunks to retry while retaining successful answers. The
nightly historical request timeout remains unchanged.

Final weather evidence and venue confirmation remain separate requirements.
An open venue market cannot be scored as settled from a weather reading.
Existing prediction receipt, schedule and immutability guards remain intact.

Archive coverage and free database capacity remain separate open work.
