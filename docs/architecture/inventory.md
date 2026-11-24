# Inventory identity and revocation

Resource IDs belong to a host and can repeat across hosts. Internal map keys,
selection keys and route segments use `scope_key(host, bare)`: both parts are
URL-quoted and joined with one literal colon. Encoded keys contain no slash;
records and wire messages retain bare IDs. Lookups reject missing, malformed
and host-mismatched keys rather than returning another host's resource.

A snapshot replaces one host's inventory coherently, preserving other hosts.
Events at or below the last sequence are classified as duplicates without
advancing the sequence or cursor. Their payloads still pass through
resource-specific handlers and may update inventory; duplicate classification
does not promise blanket ignoring or exactly-once payload application. A gap or
stale epoch requests resynchronization without updating inventory.

The `/v1/hosts` listing is the complete authorized host set. It adds unknown
hosts without assuming capabilities, updates known labels, epochs and
reachability, and preserves capabilities and last-seen values from sync.
Malformed rows are skipped. Hosts missing from a verified listing lose all
inventory slices and any selection that points to them. Later snapshots and
events cannot restore an ungranted host. Before the first verified listing,
the grant set is unknown rather than empty.

Explicit selection through select_session, host-grant removal and identity
revocation increase selection generation to invalidate pending fetches and
input callbacks. Snapshot and resource-removal handlers may clear selection
without increasing its generation. Invalid scoped keys leave selection
unchanged. Command deduplication accepts the same
ID and digest, rejects a changed digest, and refuses revoked or missing-host
contexts.

Identity revocation terminates that state lineage and clears inventory,
selection, commands and grants. Offline or reconnect retention does not apply
after revocation. Late results cannot repopulate the cleared state; fresh
authentication creates a new controller rather than reviving the revoked one.
