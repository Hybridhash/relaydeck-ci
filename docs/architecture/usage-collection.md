# Numeric usage collection and attribution

## Privacy boundary

Usage readers are selected by log format and configured root allowlists, rather
than by product-specific code branches. They return numeric aggregates, opaque
identities and timestamps. Transcript text, prompts, tool arguments and raw
filesystem paths do not cross the reader boundary. Exceptions expose fixed
categories instead of paths or content.

Working-folder linking uses the same versioned normalization on both sides:
NFC text, non-strict realpath for symlink spelling without opening the target,
and no trailing slash. Readers digest session folders from log metadata; the
connector digests pane folders in memory. A per-install 32-byte private key
produces those keyed digests and repository fingerprints. Missing keys or
unusable paths produce no digest, never an unkeyed fallback. Raw paths are
neither stored nor logged as linking evidence.

## Worker ownership and scheduling

The collector owns one worker. Its injected clock returns epoch milliseconds
and its sleep accepts seconds; injected sleep must return promptly on shutdown.
A replacement worker cannot start while the previous worker is still alive.
Readers cooperate with the 750 ms pass deadline. Catch-up has at least a 250 ms
sequential-pass gap, and the rolling duty controller targets at most 20% wall
time over ten seconds. SQLite commits and bookkeeping can extend a pass; these
are scheduling controls, not a hard CPU quota, and process priority is unchanged.

Each source has its own active/idle cadence and failure backoff. Changed cursor
body or changed events after committed apply count as progress; revision bumps
and returned-event counts do not. Historical gap flags or PARTIAL coverage alone
do not trigger backoff. Known backlog without progress or an obstruction met in
this pass does. An unacknowledged mapping gap or changed mapping set makes that
source due promptly. Unavailable storage cannot acknowledge observations,
refresh their capture time, or invent a zero count.

## Safe enumeration and atomic checkpoints

Each reader supplies a root-relative layout and a per-unit handler. Enumeration
opens descendants through no-follow parent descriptors and counts examined
entries before sorting. Per-directory/global entry limits and a deadline share
bound the walk. Incomplete enumeration preserves existing checkpoints and
context, records an enumeration gap, reports PARTIAL and does not infer a file's
absence. Only complete enumeration can delete an absent checkpoint.

Candidates are visited newest first. A checkpoint ends at a complete record;
a partial last line is read again. Device/inode, head digest, size and
modification time distinguish rotation, replacement and truncation. Whole-file
JSON is re-parsed when its signature changes. A stat/change-time index can skip
reopening a recently verified settled file, but periodic re-verification remains
necessary: coarse filesystem timestamps can conceal a same-size rewrite within
one tick. Clean checks back off that interval; changed checks reset it.

A file unit stages its events, cursor, discard position, gap evidence and reader
context together. Nothing commits unless the complete encoded cursor and its
state/context fit their bounds. Required accounting context is never evicted to
make room. Refusing a unit drops its pending events and preserves its last
intact checkpoint. Gap flags that cannot fit become one global overflow marker,
retaining PARTIAL uncertainty. An unread new file gets global gap evidence,
never an invented file checkpoint. Complete-line handlers can refuse a line
without advancing its checkpoint; end-of-file evidence is valid only after the
verified end was reached without an incomplete tail.

Oversized lines or lines that consume an entire pass are skipped with persistent,
content-free discard offsets so other files can progress. Permanent gap reasons
survive clean suffixes and unvisited files until rotation, truncation or head
replacement restarts that file. Multiple reasons can coexist; a gap whose cause
was not recorded stays UNKNOWN rather than being reclassified. Historical gaps,
incomplete tails and blocked context are not automatically unread backlog.
`more` means known changed work left by the budget, including an unread changed
file or a partly read one. Only current obstructions set the scheduling hint.

## Numeric events, pricing and commits

An event is deduplicated by host, kind, source, session and event key. Re-reading
the same key replaces its contribution, even if day or model changes. Absence
from a rewritten source file never deletes an existing event. Unverified
accounting semantics remain visible in coverage but outside confirmed totals.
Reasoning is a subset of output rather than an added token bucket. Unknown cache
write TTL stays unknown and cannot receive a short-TTL price.

Credit events carry milli-credits with zero token counters; they are never
converted to tokens or USD. Token rates are finite, nonnegative, dated USD per
million tokens. A missing rate for any used bucket leaves that event unpriced.
Tier thresholds use all prompt buckets, including cache reads/writes, strictly
above the named threshold and excluding output/reasoning. A missing tier rate
does not fall back to the base rate. Each event is priced before aggregation;
empty, priced (including free) and unpriced events stay distinct.

Apply compares the expected source revision and commits event replacement,
latest limit windows, mappings, folder observations and the next cursor in one
transaction. Exact retry is a no-op; a stale batch raises stale_batch and changes
nothing. Mapping and folder observations are acknowledged only after commit,
using their exact observed versions so a later update cannot be lost. Retention
pruning is bounded, and rescanning cannot restore events before its horizon.
Retiring a removed source preserves its events and cursor so re-adding it resumes
without discarding history; other hosts remain untouched.

## Attribution evidence and ambiguity

Session observations form separate episodes for every move, including A-to-B-to-A.
Workspace generations distinguish reused IDs across daemon restarts; episode
labels and keyed repository fingerprints preserve historical display identity.
Reported identity is rank 1, validated process evidence is rank 2, and ranking
is resolved before ambiguity. Multiple candidate spaces within the best rank
remain unlinked and cannot fall through to folder inference.

For a timestamp inside a confirmed episode, attribution uses the containing
space. Before the first episode it uses that first episode; between episodes it
uses the most recently ended one; after disconnect it uses the last. Explicit
mapping-gap intervals override this fallback with unknown attribution. An open
mapping gap closes only after clean snapshot evidence, and its version is
acknowledged only after committed apply. A session with no episode remains
unmapped. Folder inference requires matching kind, digest and time overlap and
exactly one candidate space; multiple spaces remain unlinked. Pane folder
episodes store no separate pane ID; their interval ID correlates acknowledgment.

The process prober uses a validated foreground PID/group from Herdr, then checks
UID/start through ps and held locks through lsof. Individual 3/2/3-second command
bounds are also capped by the caller's wall deadline. Names, arguments, command
lines and cwd are not used as process identity. Several foreground entries with
no unique group match yield no evidence. UID mismatch, malformed output,
timeout and read errors yield no observation and never extend an episode.
Source-relative lock keys use the same identity helper as their format reader.

## Reader-specific identity limits

Whole-session token snapshots pin the first emitted turn key using result ID,
message ID or positional index. Later IDs reuse an existing pin; pins are never
evicted. Index-only keys can move under compaction/reordering. Partial documents
resume from the next turn only while their signature matches and keep offset zero
until complete. A pin that cannot fit stops that document before the turn while
other documents continue. Documents above the document limit are gaps; a large
allowed document may exceed the pass-byte budget only as its first unit. A
complete pass with turns but no token totals is unavailable, not zero usage;
unproven cache inclusion leaves those events unverified.

Credit snapshots and streamed summaries share one source cursor. V3 summaries
are processed before V2 turns, with a cutoff at the first summary for that
session. Already-stored V2 events remain because absence does not delete events.
V2 fork claims compare loop identity and exact metering totals and choose the
earliest holder by mtime, session and path, with standing committed claims
retained. A deleted original cannot participate in a later fork's first scan;
that fork can count anew. An incomplete claim scan uses standing claims only and
keeps PARTIAL coverage. Loop keys include session, loop digest, time and messages,
with positional fallback when needed; V3 uses a line ID or byte offset. A credit
turn rejects nonnumeric, nonfinite, negative or oversized values. Foreign-unit entries are skipped
and flagged while valid credit entries in that turn can still count. No reported credits across observed turns means unavailable.
The V3/claim/V2 phases have separate deadline/byte enforcement (up to three phase
wall budgets), while the claim scan is charged to the V2 byte budget.

Cumulative-rollout readers count per-call token_usage_record entries, ignoring
compacted copies. Cumulative totals supply limits only and expose a coverage gap
when no per-call usage exists. Inclusive input subtracts cached reads and writes;
negative residuals or reasoning above output are gaps. Message-usage readers use
the record's top-level totals, ignore iteration and model summaries, and replace
streamed duplicates by message/request identity when supplied.

Event-usage readers count reported attribution records and completed-review
usage, excluding estimates and overlapping completion/sample/checkpoint kinds.
A counted line must have record_type=event; missing payload_type/version fields
are not required by this reader. Model enrichment comes only from the bounded
same-root session index or the record's own supported model identity. Model-less
events remain stored but unverified. Review offset keys include file generation
so restarting a file changes those positional identities.

SQLite readers emit cumulative deltas per row, recording a gap and rebasing when
totals decrease. First sight attributes the full current totals to last-seen day,
which is approximate for older multi-day accumulations. One sticky database
holder keeps the bare session ID; collisions elsewhere use database-qualified
session keys and all event keys include the database tag. Such rows cannot exact
link but may use folder inference. ID enumeration has row/time/memory bounds;
a missing collision view skips that database for the pass. Bounded keyset scans
report partial work rather than presenting a cut scan as complete.

## SQLite reads and report snapshots

Readers use live read-only SQLite snapshots with query_only, bounded chunks and
progress deadlines rather than copying large databases or using immutable mode
that would ignore WAL data. The session index selects only ID, model and workspace
columns, never title, prompt or search text. Text arrives capped at validation
limit plus one so oversized identity is rejected and counted without truncation
into another identity. Missing, unsafe, corrupt, locked, oversized or over-rowed
index data gives no enrichment rather than failing token ingestion.

A report pins one bounded SQLite read transaction for attribution, grouping,
recency and label facts. Over-cap reports contain no partial rows. Hourly queries
require one host-local day and incompatible daily cursors are refused. A repeated
fall-back hour shares its wall-clock label; a skipped spring-forward hour has no
row. Per-source account switching hides historical accounts without deleting
their data. Last-good limits survive only a transient failure for a positively
identified account; unknown identity and authentication failures suppress them.
Each quota displays only its latest observation set, including explicit empty
sets, while an idle first-window reading can leave other windows stale/expired.

SQLite token readers ignore stored *_cost_usd columns and price from the configured price tables.
The limits-plane successful snapshot writer must not receive a failed fetch; failure records use
the separate failure path so last-good reuse remains identity-bound.
