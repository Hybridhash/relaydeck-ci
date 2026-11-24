# Safe repository reads and stable captures

## Execution boundary

Repository queries use a trusted Git executable, allowlisted subcommands with fixed arguments,
a scrubbed environment and bounded stdout. The request deadline includes waiting
for a process slot. Git reads attributes from the empty tree so worktree files
cannot attach filters or diff drivers. Effective configuration that can run a
helper, read unrelated files, redirect the worktree or use object alternates is
refused. Driver assignments in the repository attributes file are refused too;
that file must be small and regular and is opened without following links or
blocking on a special file.

Agent folders are resolved to validated worktrees with ownership and symlink
checks. Public worktree IDs use path-free directory identity; recreating a
repository at the same path produces a new identity. File content is opened
relative to pinned no-follow descriptors, so a replaced parent cannot redirect a
read. Untracked-file diffs use a private directory outside the repository.
Failures expose fixed codes, never Git stderr or local paths.

## Reuse, revisions and base selection

Metadata probes can authorize reuse without reading worktree content only while
bounded traversal and supported ref storage establish the same admitted inputs.
Uncertain proof falls back to a guarded Git capture. A stable snapshot is probed
before and after reading; a changing worktree is retried once, then reported
partial rather than complete. Captures and cached pages are bounded, and stale
or evicted ownership invalidates their handles.

A saved base includes both its OID and the saved choice's revision. Saving even
the same ref gives Branch captures a new revision and retires older pages. When
a saved ref disappears, selection falls back to the default while retaining that
choice revision. If no remote names a default branch, an unambiguous conventional
remote trunk wins over a local trunk; local main/master/trunk candidates are
considered otherwise. A trunk is not compared with an arbitrary sibling trunk:
when no suitable choice exists, the reader chooses the base explicitly.

## Content fidelity and masking

Diff parsing reads one file's patch body with caller-supplied identity and ignores
header paths. Huge lines and invalid UTF-8 become render states rather than
silently altered content. Function-context text in hunk headers is omitted
because it would bypass row masking. Rows are masked before page/byte limits
are applied, and long hunks continue as fragments with consistent counts.
The gateway supplies opaque continuation cursors.

Secret-path matching is case-insensitive on every component; a withheld directory
covers its subtree and configured repository-relative exclusions add to the
policy. Both sides of a rename are checked. Key-block and known-token masking
retains line lengths, with separate old/new-side state across pages so a block
cannot escape masking at a page boundary. Word marks are removed for masked rows
because they could reveal which secret characters changed. These known-shape
rules are defense in depth, not a guarantee that every secret is recognized.

Worker flights coalesce requests and expose cancellation to Git calls. Shutdown
retires caches and requests cancellation of queued/running workers; worker exit
can still be delayed by unbounded external I/O, so bounded reads remain essential.
