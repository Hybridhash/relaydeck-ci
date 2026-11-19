# Client drafts, input and uncertain delivery

Composer drafts are local state until an explicit send. A screen owns its composer store and passes
controlled props and callbacks to the presentation. Construction starts no sockets, timers or
storage work. Native edits can arrive before their on-change event; a parent render keeps the
native value while the model has not changed, then applies an authoritative model replacement.
Retired editor lifetimes have separate keyed native subtrees and revocable callback tokens.

## Prompt delivery

A valid send records one command envelope and observes the returned identity. Observation only reads
status and never submits a second prompt. Same-ID deduplication belongs to the journal; a changed
user draft and new Send is a new command. Validation or a concurrent submission does not touch the
transport. Draft clearing follows successful delivery only while the draft still equals the sent
value, so later edits survive.

Typed pre-delivery refusals differ from timeouts or transport loss after dispatch: the latter can
mean the command already reached its target and therefore remain unknown. Production recovery
queries exact account/host-bound command identities and never infers success from agent activity or
missing history. Proven terminal outcomes survive later unreadable status queries. Successful
creations settle only after the UI verifies the exact created resource; partial, failed and uncertain
creations stay available for explicit review.

An earlier unconfirmed prompt can block a new prompt to the same target. Check status reads the
existing command. Explicit dismissal is available only after the recorded unknown prompt is proven
unresolvable; it sends nothing and retains no prompt text. A late result can settle only its exact
submission, never a newer draft or send. The injected direct source-callback test path alone has a
bounded agent-not-ready retry loop; it is not a second production dispatch path.

## Blocked insertion and paste

When an agent is blocked, approved draft insertion uses exact UTF-8 through the current PTY writer,
without adding Enter or bracketed-paste framing. Multiline or executable-control content requires
explicit review because existing line breaks can execute even without an appended Enter. The review
captures the writer generation and exact bytes; confirmation rechecks both and clears pending
state before sending. A failed later chunk cannot undo earlier PTY chunks, so partial delivery is
never implicitly retried. The draft stays available and uncertainty is disclosed.

The paste-review path separately captures bracketed-paste framing from actual engine mode and binds
it to the live writer generation. Dead, stale or absent writers fail closed. Lifecycle transitions
retire pending reviews and saved callbacks rather than retargeting them to a replacement terminal.
Terminal output, labels and approval-looking notification strings are inert and cannot confirm a
review or generate a reply.

Staged markers are local placeholder tokens with resolution state. Unresolved staged tokens still
in the draft block Send; token-shaped text not staged by this store is ordinary text. Resolution
replaces the first exact occurrence and removes duplicates. Staging owners supply replacements;
this model does not provide an attachment, file or snippet backend. Blocked delivery switches the
store to tools keys; leaving that surface explicitly resets composer mode, while normal send,
status and reconnect do not reset it.

## Keys, text and ownership

Input mode separates workflow from keyboard presentation. Agent sessions bind a composer store;
shell sessions have no draft store. IME updates emit nothing, commit banks one text value for
consumption, and cancellation drops composition. The mounting send boundary must check committed
input before calling the store; active composition returns without draft mutation or dispatch.

The Show keyboard icon ignores pointer events so that a tap reaches the text field beneath it
natively. iOS raises the keyboard only from inside the user's tap, and a server-side focus request
would arrive after that gesture has ended.

Physical printable keydowns are held for native text delivery, while non-text keys use the encoded
key path. Matching echo suppression avoids sending a consumed physical key again through its text event.
The deduper has one slot and assumes ordered events: a second consumed key overwrites its armed
echo, so an eventual unmatched first echo can flow as fresh text and permit duplicate delivery.
This is an implementation limit, not a guarantee for arbitrary interleaving. Key encodings are pinned by
fixtures; unsupported names or modifier combinations return no encoding rather than inventing
bytes. Tools keys combine armed and physical modifiers. A committed single character can consume
armed modifiers only after successful encoding; a failed encoding preserves the original committed
text and modifier state. Already encoded engine bytes neither consult nor consume armed modifiers.

Queued emissions bind both target and live writer generation. Retiring a writer drops queued input;
resume obtains a fresh binding rather than reactivating old handles. A lease taken over elsewhere
still fails server ticket checks. No previously selected terminal receives a delayed key. One-shot
modifiers clear only after accepted emission, and teardown invalidates the callback token.
Before server-side key or paste input, scroll cancellation retires stale native wheel motion when
supported. Local scroll and selection actions do not write PTY input or mutate composer drafts.

## Pictures and selection

Picker and browser-paste sources share grant, upload, polling and final staging/insertion policy.
One pending operation owns its source and busy gate. It captures session, workflow, draft, writer
and activation facts, then rechecks them before every consequential step. A late browser offer or
text event is refused and its retained Blob released when those facts have changed; it is never
rebound to the successor context. Abandoned pickers get retired control identities, so native late
results cannot affect a fresh attempt.

Composer stages the accepted picture marker; Direct Input inserts only through the bound writer.
Neither a selected filename nor browser MIME metadata proves safe image content; the server checks
bytes and the grant binds the upload. Browser image bytes stay outside Flet events and terminal
packets. Cancellation and failures preserve explicit state without silently selecting another source.

Select Text freezes the live emulator snapshot for the sheet lifetime, including cursor overwrites
and alternate-screen content. Copy targets bind file/snapshot or terminal selection facts at open
and validate them again at activation, so a late action cannot copy a new context. The headless
selection helper groups zero-width continuations with a preceding same-line base; it is not a full
Unicode grapheme-break implementation. The bounded raw-output fallback counts evicted bytes and
shows incomplete history rather than claiming an exact full transcript.

The headless VT screen model intentionally recognizes a pinned subset, skips unknown sequences,
and supplies deterministic output checks rather than a full emulator. Native snapshots remain the
source for the rendered selection sheet.

Paste counts and preview cuts use a separate UAX-29 approximation: combining, variation and emoji
modifier marks, unconditional ZWJ joins, regional-indicator pairs and CRLF are grouped. Prepend and
Hangul Jamo composition are not implemented; arbitrary a + ZWJ + b also joins. These approximations
can affect counts and preview boundaries, never the exact bytes approved for transmission. They
are distinct from the terminal selection helper’s simpler same-line zero-width grouping, and from
the full cluster splitter that counts display names (see
[grapheme counting](contracts.md#grapheme-counting)).
