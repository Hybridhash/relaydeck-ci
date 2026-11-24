# Client reading contexts and refresh

Screens receive typed feature values and render them through controlled widgets. Transport adapters
validate response shapes before turning them into model values. Request tokens and generations
separate contexts: a late response cannot replace the account, host, worktree or query now on screen.

## Virtual rows

Virtual lists build the viewport plus a bounded margin, using padding and a spacer for unbuilt rows.
Each child has an explicit extent, so a sliding child index cannot inherit the previous row's
position. Stable keys and signatures reuse unchanged control instances. Callers describe a row
only when requested, and unchanged owner revisions avoid scanning every row during a render.

A jump initially builds a lean visible window, then adds margins on a later tick. Measured-height
lists estimate unknown rows from the observed average; once the reader leaves the top, the average
freezes so unseen rows above cannot move under a fixed pixel offset. A changed context adopts its
own window and requested place. Until the native list confirms a scroll request, its last reported
position remains authoritative. New measurements above the visible anchor compensate the scroll
position; forgotten rows lose cached heights so memory remains bounded.

## Changes and review

Changes summaries and the open-view revision share one serialized scheduler. Visible summaries are
coalesced to at most one request per second and polled every 30 seconds while the Console is shown.
An open view checks every 10 seconds while its agent works and every 60 seconds otherwise, with
additional status, focus and reconnect triggers. Backgrounding stops scheduling; foreground return
checks immediately. Busy/rate-limited Retry-After closes only its channel until the retry window
ends, and a failed poll does not kill the scheduler.

Summary revisions can raise Updated without replacing a loaded file list's branch, base, timestamp
or partial/shared metadata. Working compares with its Working revision and Branch with its Branch
revision, including disappearance of the base. Refresh retains the selected file by ID when valid,
otherwise by path within a bounded search. An empty list is clean only after a healthy completed
load, never while calculating or unavailable.

Resident diff pages form a bounded contiguous window. Cursor chains and row anchors allow evicted
pages to be fetched again, while forward navigation follows the resident tail rather than the
furthest page ever seen. Graph rows reuse stable lane segments; older checkpoints retain lane
layout without duplicating the newest checkpoint's full seen-commit set. Commit openings have
unique generations so reopening the same commit cannot accept a stale prior response.

Viewed marks are memory-only and bind server, account, host, worktree, scope, base and exact content
revision. Changed content or identity finds no mark; logout/revocation clears them. Copy actions
resolve their captured file and snapshot against the current model at activation. An incomplete or
secret-filtered hunk copies only its resident visible portion and is labelled as partial. Diff rows
are not click targets; the explicit Copy sheet supplies touch-sized actions.

## Usage

A usage query is either host-local days 1…90 or an explicit inclusive range. Hourly mode requires
one day and belongs to the cache identity, so hourly and daily pages cannot substitute for each
other. Feed keys bind account/transport, host, query and filters. A changed key starts a generation;
replies from prior generations are discarded. A scope change clears cached pages; changing queries
inside one scope may reuse that query's recent page while visibly updating it.

The active feed refreshes without overlap: it awaits completion before starting the next delay.
Manual refresh and resume share lifecycle guards. Hidden/background/unmounted feeds stop polling
and obsolete their in-flight results, but can show aged same-context data. Failure keeps the last
good page with its age and error instead of showing stale data under another context.

All-space totals combine overall and not-linked rows exactly once. A chosen space uses workspace
rows, and not-linked uses its own rows. Null cost means unavailable or partially unpriced, never
zero; cost is not inferred client-side. Reasoning is already inside output tokens and is not added
again. Sessions are not additive across days or models. Missing older-server coverage fields remain
unknown rather than being displayed as zero. Display names come from the report, without embedded
provider names.

Limits describe current host/account quotas, ignoring period and space while respecting provider
filtering. Multiple provider accounts make a kind's reading ambiguous; timestamp cannot select an
account on the reader's behalf. Only fresh windows signal capacity. Expired aggregate windows do
not hide a fresh sibling; equal pressure chooses the earliest reset. Passive-source freshness
considers subsequent tool activity, while scheduled sources become suspect after missed checks or
failed attempts. Hook installation state is independent of reading freshness.

Manual limits reads succeed only when a matching attempt reports success and a newer reading; a
poll ending alone proves nothing. Setup commands derive only from validated report kind/format
values and are shell-quoted. They copy exact command lines. Provider pickers and pinned-space state
are device-local and scoped to account and host; cancel discards draft picker selection.
