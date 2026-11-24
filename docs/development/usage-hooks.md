# Usage allowance hooks and refreshes

## Consent and settings ownership

Hook diagnosis reads installation evidence without executing settings commands.
Installation and repair require their own confirmation; Serve's --yes does not
authorize them. Creation of settings and replacement of a custom status line are
separate opt-ins. Consent is bound to the exact diagnosed statusLine value; a
changed value refuses the write rather than reusing permission for a new field.
Uninstall restores only the still-managed field, never an entire backup document.

Transactions pin no-follow ancestors and use owner-only files. Script-only upgrade
shares one absolute deadline across its lock waits; other calls bound each individual wait. File I/O has size bounds rather than that
lock deadline. A prepared private receipt precedes settings rename so interrupted
installation can be inspected and uninstalled. Other settings writers must stop;
locks serialize cooperating writers, not arbitrary editors. Script-only upgrade
requires the allowlisted installed script, matching receipt, valid backup and
exact managed command. Missing or changed witnesses refuse it. A committed rename
followed by failure is uncertain, not a clean success or a claim of no change.

Managed commands retain the absolute tool-environment interpreter entry,
including symlink spelling. A linked parent leading to the same directory entry
can compare equal; a sibling environment, copied interpreter or base interpreter
cannot substitute for it. Checkout scripts are not installation witnesses.
Packaged hook bytes must match both their manifest and the compiled SHA-256 pin.

## Pinned offline hook

The packaged standalone status-line hook is byte-pinned, including its local
comments and docstrings. It accepts
bounded input, rejects excessive JSON nesting before parsing, and persists only
allowlisted numeric allowance fields. Session IDs enter fixed-domain digests
rather than stored raw values. API generation derives from the session identity
and finite numeric total API duration; an identical repaint repeats that digest.
Invalid generation inputs yield no generation evidence.

Its ASCII lock format is receipt_ms:confirm_ms:generation. Every confirmation
advances receipt fencing; confirm time advances only for a fresh generation with
limit rows. Older writers are fenced before publication, and the generation is
consumed only after cache and receipt are published. Empty payloads affect
bookkeeping rather than confirming live limits. Fresh live rows replace data;
repaints preserve existing observations. Bounded per-session generation entries
are evicted oldest first. The reader inspects lock metadata without taking the
lock and falls back to the cache capture time for malformed or unusable evidence,
including confirmation clocks beyond the allowed skew.

## Allowance-worker isolation

A separate worker refreshes configured allowance providers independently of the
token-ledger pass lock and reader budget. Per-provider intervals, a throttle floor,
manual-refresh single flight, persisted cooldowns and jittered error backoff
(capped at 30 minutes) bound attempts. Page openings coalesce across devices;
background TUI reads require tool use since the last reading and their configured
interval. Client disconnect does not cancel a started manual refresh. Cooldown
begins only after the resulting snapshot commits. No identity or auth_required
suppresses last-good data; only an identified transient failure may reuse it.

Bounded subprocess runners require absolute argv, a minimal environment, their
own process group and no shell. They cap stdout, discard stderr and bound the
whole interaction deadline. Timeout kills the group and reaps the child. Stdio
sessions always kill and reap on return, including success; unmatched lines still
count toward the shared output cap. Provider state is supplied through fetch
contexts rather than mutable global singletons. JSON-RPC uses initialize,
initialized and one account/rateLimits/read request; account identity is keyed
before it leaves the provider and unavailable keying disables last-good reuse.

## TUI authority, trust and cleanup

Every TUI attempt creates and journals its own temporary tab, verifies the pane's
foreground program before writes and waits for expected output markers after
writes. A stdout-only CLI success is not trusted as delivery proof. A failed
wait-output match causes a tab health check so unavailable Herdr is distinguished
from a tool timeout. Refresh uses the existing Herdr command and wait-output interfaces.

A saved session ID comes only from a live quit hint in the verified owned pane,
corroborated by the recipe's session-root evidence and birth time after attempt
start. File mtimes do not supply identity. Recipes without tracking hints retain
no session IDs. An unexpected session ID is not adopted. A definitely dead resume
ID allows one fresh-tab retry; at most two tabs belong to an attempt and both are
journaled. Unknown tool state receives no quit text; only proven-owned tabs may
be closed, including bounded recovery of leftover tabs after restart.

Probe recipes start in the companion's own empty 0700 folder, never a project
folder borrowed from a live pane. Without a same-kind pane or linked workspace,
that probe can use another available workspace. Trust remains the user's choice
in the tool's terminal prompt: refresh never answers it or writes a trust file.
Supported trust stores are bounded read-only JSON or hashed-workspace permission
layouts; unsupported or unreadable evidence reports unknown. A tool exit code
alone never proves trust.

The locked 0600 refresh-state document stores local recipe/session-root and workspace scope,
including path strings encoded as JSON keys, alongside opaque IDs, timestamps, phases and codes.
That encoding is not path anonymization; screen text and prompt contents are not stored.
Corrupt or unreadable state starts empty, which may permit one extra refresh;
unverifiable tabs still cannot be closed. Reset-clock interpretations stay in the
host zone: a clock time in the past remains past rather than being moved to
tomorrow. A settling panel gets only the bounded rereads allowed by the recipe;
a final refusal or not-reported answer ends submission rather than triggering
another potentially billable message.

Interpreter binding compares trusted directory-entry identity and never writes through the returned
path. Page-open auto requests return the number of queued attempts; probe-folder preparation returns
False for a link or a failed directory operation and writes no content into the folder.
