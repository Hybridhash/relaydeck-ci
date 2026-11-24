# Release verification and bundle storage

## Verification before installation

Signed web releases use prehashed minisign ED signatures and the packaged public
key. Verification checks the key ID, Ed25519 signature over the BLAKE2b-512
message digest, and the global signature over the signature plus trusted
comment. Public-key files contain base64 text; a decoded 42-byte key blob starts
with the raw bytes Ed, followed by its key ID and public key. Malformed or
oversized keys and signatures are refused.

A release archive is installed only after its signature, build-manifest version
and bundle repair markers verify. Repair checks use calibrated literal counts
that survive minification, so a missing required repair refuses the bundle.
There is no unsigned download/install path for a signed release.

## Policy metadata

A bundle is installed or served only with its policy metadata, `csp.json`. The record holds the
SHA-256 of `index.html`, the hashes of its inline scripts and the policy mode, and it must equal
what is recomputed from the bundle's own index. Installation reads the record from the bundle's
sibling build manifest, writes `csp.json` into the stored bundle when it is absent, and refuses a
bundle whose existing `csp.json` differs. At start the companion also requires the hash of its
hosted runtime configuration script to be among the recorded inline-script hashes. A bundle without
a matching record is refused with `csp_metadata_required`, which `relaydeck start`,
`relaydeck web install` and `relaydeck web fetch` print.

## Storage destinations

Local built bundles live under
`~/Library/Application Support/RelayDeck/web/<build-id>/`. Their build ID is a
validated bounded hexadecimal revision/hash form, derived from the sibling
build manifest when a valid revision is available, otherwise from the hash.
`installed.json` records the built-bundle installation.

Signed releases use the separate destination `web/signed/<archive_sha256>/`.
The archive SHA-256 is a validated 64-character hexadecimal component. A signed
installation records an inventory of every file's size and SHA-256. An existing
destination is reused only while that complete inventory matches with no extra
files; otherwise installation replaces it through a fresh directory. Selection
requires the exact installed application version rather than any other version.

## Descriptor-pinned copying and cleanup

The bundle store is pinned as a real directory owned by the current user and
not writable by others. Copying addresses children through pinned parent
descriptors. Each directory uses O_DIRECTORY and O_NOFOLLOW and must still be
the object that was listed. Each file is opened without following links and
must still be that same regular file. Symlinks, devices, sockets, FIFOs and
entries replaced after listing refuse the install. Copied bytes count toward
the size bound as they are written.

Staging directories have unpredictable names and are created relative to the
pinned store. Only the staging directory created by that operation is removed
on failure. Repair markers verify on the copied files before publication, so a
failed copy does not become an installed bundle.

## Downloads and archives

Downloads require HTTPS and fixed host allowlists. Redirects are handled
explicitly; one absolute deadline covers resolution, connection, TLS, headers
and body reads. Environment proxies, automatic redirects and content coding
are disabled. Streaming checks the remaining deadline and bounds bytes.

Local archives follow the same verification path after being copied into
private staging while hashing; later operations read only that staged copy.
Extraction accepts bounded regular files and directories, refusing escapes,
absolute names, parent traversal and duplicate members. The decompressed stream
has a separate allowance for tar headers, padding and read-ahead so metadata
cannot grow without bound before member checks. Every failure removes its
private staging directory.
