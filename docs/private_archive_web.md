# Private archive web reader

`/api/private-archive` uses the existing strict private owner session (the
Original sides HttpOnly owner cookie or an authorized bearer session). Legacy
single-user mode does not bypass this gate. Anonymous and revoked sessions
receive 401 before any GitHub data request.

The deployed server needs `AD4_PRIVATE_ARCHIVE_READ_TOKEN`, a credential with
Contents read access only to `hassansab00/Arbdesk4-archive-001`. Enter it in the
deployment environment, never source or a browser. The Actions writer token
is not copied out of its runner environment.

The reader verifies current private visibility, pins the repository's main
commit for the whole request, checks the manifest against the committed
snapshot receipt, and verifies each compressed payload's SHA-256, byte count
and parsed row count. It preserves the legacy decoded-payload response shape.
Dataset dates use the same cutoff column as the Python exporter. Read failure
is explicit; it never falls back to incomplete public history.

After proving private owner access and a representative historical read,
`AD4_PRIVATE_ARCHIVE_WEB=1` switches the existing `/api/archive` route to the
private reader. Until that setting is enabled, existing archive requests keep
their current behavior. A direct private endpoint is available for the proof
before switching the existing route. Private responses are always no-store.

Queries require a bounded date range and page size; complete private file
downloads are available by an exact indexed asset name and `redirect=1`.
Payloads exceeding the server's compressed-size budget must use that download.

Deployment credentials and a live owner-read proof remain required. Neither
this endpoint nor its default-off switch authorizes or enables live retirement.
Mirrored-only datasets and ongoing incremental offload need separate support.
