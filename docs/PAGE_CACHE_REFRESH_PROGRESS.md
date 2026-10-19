# Acknowledged page-cache refresh

A pipeline refresh failed at the HTTP gateway after prediction and scoring had completed. The cache transaction did not finish, so the stored page could lag newly banked outcomes. Increasing the client timeout does not remove an upstream gateway deadline.

The existing Python RPC entry point now submits an idempotent request, then polls a short status RPC until the database records a completed refresh. The existing refresh implementation, serialization lock, materialized views and half-hour schedule are unchanged. A requested database job removes its own schedule after success or failure; contention has an attempt limit and abandoned requests expire. Receipt history remains in the existing ingest log. No permanent polling schedule or new scheduled GitHub workflow is introduced.

Only the service role can request or read completion receipts. Anonymous callers cannot schedule work or read request logs. A skipped refresh is not success. Database errors, query cancellations and an unacknowledged deadline remain explicit failures. Retrying an ambiguous submission with the same request ID does not create a second job.

Validation covers the unchanged refresh function, request replay, actual completion, contention, expiry, failed and cancelled refreshes, schedule cleanup and reader permissions. Production verification must confirm a real background refresh completes, its temporary schedule disappears, and the stored page agrees with newly banked outcomes.
