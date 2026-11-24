# Recovery, backups and retention

There is no automatic backup or general restore command. On the Mac, a copied
encrypted persistence database alone cannot recover protected state: it also
needs its original Keychain material, metadata and compatible schema. Losing
those keys can make hosted sign-ins and drafts unrecoverable. Native device
drafts use a separate secure store and are not part of a Mac database backup.

## What survives

| Data | Lifetime and recovery boundary |
| --- | --- |
| Hosted sign-ins | Survive Mac restarts only when durable storage is enabled and usable. Otherwise they are held in memory. Their credential and session lifetimes still apply. |
| Hosted drafts with configured Mac persistence | Survive restarts when that store is usable. They expire after seven days without access and may be evicted earlier to enforce storage bounds. |
| Native device drafts | Account-scoped drafts use the device's secure credential adapter. There is no automatic seven-day expiry; they remain until explicit draft deletion or account clearing. Removing Mac state does not remove these device drafts. |
| Memory-only drafts | When persistence is deliberately absent, drafts live in page-owned memory for the page/account lifetime. They have no seven-day timer or durable recovery. Memory storage is not substituted for a configured secure store that failed. |
| Device credentials | Native clients use platform secure storage. A secure-storage failure does not silently switch to plaintext storage. |
| Command journal | Nonterminal records retain full bodies. Completed, failed, rejected and expired commands retain bodies for 24 hours, then routing and deduplication tombstones for up to seven days after the terminal update. Frame receipts are retained for one hour. |
| Uncertain commands | Dispatched or acknowledged commands become unknown after an uncertain outcome or recovery. Unknown records are nonterminal and can retain prompt and result content without a fixed expiry. |
| Usage ledger | Retains a 90-day history, including retention-bounded deduplication data. It stores usage measurements rather than conversation bodies. |
| Uploaded pictures | Sanitized files on the Mac are eligible for cleanup after one day; cancellation and upload expiry can remove them earlier. Maintenance must run for cleanup to occur. |
| Push delivery records | Sent or dropped deliveries and expired links are eligible for purge one day after expiry. Device tombstones are retained for 30 days after their last update. |
| Login-service logs | Output rotates by size rather than age. Each stream has a 5 MiB active file and three archives; old archives are replaced as output grows. |
| Configuration, keys and enrollment | Remain until deliberately replaced or removed. A configuration backup is not a database or Keychain backup. |

Expiry and purge depend on normal maintenance. These are application retention
rules, not secure-erasure guarantees for filesystem snapshots, backups or the
tools running inside terminals.

## Make a backup

This procedure covers Mac configuration and SQLite state. Native credentials
and drafts depend on the device platform's protected storage; RelayDeck has no
native secure-storage export or restore command. A Mac backup does not include
them.

1. Record the installed version and stop RelayDeck and its login service so no
   process writes the state while it is being handled.
2. Keep configuration and private state together in an access-controlled,
   encrypted backup. Preserve owner-only permissions and record which stores and
   external keys are needed. Do not put keys or backups in the repository.
3. Use a consistent SQLite snapshot, such as the SQLite backup API, for each
   database. A database using WAL must not be copied on its own while writers
   are active. For a stopped copy, retain the complete consistent database state,
   including any required sidecars.
4. Verify recovery in an isolated environment with the matching application
   version and original key provider. A successful file copy is not a restore
   test. Never export Keychain secrets into an unprotected file.

## Recover safely

For Mac recovery, start with `relaydeck setup --check` and `relaydeck status`.
Preserve the original state before troubleshooting. A locked Keychain should be
unlocked through the operating system; do not replace missing keys to make an old
database open.
Unknown schemas, missing or wrong keys and corrupt snapshots fail closed.

A supported storage snapshot can recover committed records when the original
keys and schema remain valid. It does not restore running terminals or prove
which terminal commands executed. After restoring durable authentication state,
run `RELAYDECK_DURABLE_SIGNINS=1 relaydeck setup --re-enroll` with the original
Keychain available. Confirm that enrollment replacement and the external
authentication-generation advance succeed before reconnecting devices. Re-pair
devices rather than reviving credentials from a stale backup.

Do not resume a rolled-back command journal against live terminals. Its older
accepted row may describe a command sent after the snapshot. Confirm affected
operations independently and treat unknown outcomes as uncertain; never replay
them automatically. A frame acknowledgment proves receipt, not execution.

If Mac keys are permanently lost, reconfigure and pair a clean installation after
preserving the old data for diagnosis. Cross-Mac key migration, automatic
rollback and recovery from an encrypted database without its keys are not
supported recovery procedures.

## Remove data

Signing out clears local account credentials and native secure drafts and
attempts server revocation.
A failed server revoke is reported; local sign-out alone does not prove that the
server has removed its records. It does not erase the command journal, usage
history or backups. Ordinary preferences and pins use separate storage and are
not erased by account credential clearing.

Use the [uninstall instructions](../../README.md#uninstall) for Mac removal after
stopping services. Delete native drafts explicitly or clear the device's account
storage separately; removing the Mac state cannot remove them. Review unknown
command outcomes before removing their journal: deleting deduplication records
loses their protection. Remove
backup copies and platform credentials separately when complete removal is
required. Removing RelayDeck does not erase terminal history or data held by
other tools.
