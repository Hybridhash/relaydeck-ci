# Native terminal transport

The Python control and Flutter widget share a binary DataChannel. The application retains one
control per terminal surface, keeping the native engine and byte counters across renders.
Application input sessions decide ownership and permissions; the extension transports bytes and
reports engine state. Runtime changes use packets because the rendered control is frozen.

Event annotations in the Python controls must evaluate at definition time. The pinned Flet
reflection code reads raw annotations and does not resolve postponed annotation strings.

## Packets and bounds

All integers below are big-endian. The first byte selects the packet kind; lengths describe the
following payload. Hardware keys are encoded by xterm2 and arrive as opaque input, while application
buttons and approved paste use the gateway lease path directly. There is no Python-to-widget key
packet that feeds the emulator as if it were remote output.

| Python → widget | Body and effect |
| --- | --- |
| WRITE `01` | u32 length and terminal bytes |
| INPUT_ENABLED `03` | one byte, display-only or Direct Input |
| REQUEST_SELECTION_TEXT `06` / CLEAR_SELECTION `07` | no body |
| RESET `08` | reset the emulator and establish an explicit recovery boundary |
| REQUEST_SNAPSHOT `09` | u32 request ID |
| REQUEST_KEYBOARD `0A` | no body; effective only while input is enabled |
| SCROLL_VIEWPORT `0B` | signed page count ±1…10, or `7F` for bottom |
| RESUME_PROBE `0C` | nonzero u32 request ID |
| RESUME_VERIFIED `0D` | no body; release the transport-loss input fence after verified recovery |
| RELEASE_KEYBOARD `0E` | no body; hide keyboard and disarm touch input |
| KEYBOARD_BUTTON `0F` | one byte visibility flag |
| CANCEL_SCROLL `10` | u32 sequence to retire widget scroll motion before a server-side key or paste |

| Widget → Python | Body and meaning |
| --- | --- |
| INPUT `81` | origin u8, u32 length, bytes |
| SIZE_REPORT `82` | u16 columns, u16 rows |
| SELECTION_TEXT `83` | u32 length, UTF-8 text |
| SNAPSHOT_CHUNK `84` | u32 length, u32 request ID, u32 total bytes, u32 offset, bytes |
| MODE_REPORT `85` | one byte bracketed-paste state after WRITE or RESET |
| VIEW_REPORT `86` | bit 0 alternate buffer, bit 1 at bottom, bit 2 mouse reporting; changed values, at most once per frame |
| RESUME_REPORT `87` | u32 request ID, u64 native instance, u32 reset count, u64 applied bytes; request 0 is mount hello |
| KEYBOARD_STATE `88` | one byte keyboard armed/shown state |
| KEYBOARD_TRACE `89` | event u8, flags u8, visual viewport height u16; `FFFF` means unavailable |
| SCROLL_CANCELLED `8A` | u32 sequence; unsolicited 0 advertises support and releases no outstanding cancel |
| PAGE_METRICS `8B` | reset u8, page scroll i16, visual viewport offset i16, visual viewport height u16, window height u16 |
| ACK `FF` | u64 cumulative applied output bytes |

Input origins are committed text (1), encoded key (2), mouse report (3), and swipe (4).
A swipe is exactly two bytes: direction 0 toward older output or 1 toward latest, then a count
1…255. Application policy translates valid alternate-buffer swipes to bounded PageUp/PageDown;
malformed or out-of-mode swipes are dropped rather than treated as raw PTY bytes.

Input chunks are bounded to 16 KiB, write chunks to 64 KiB, and each viewer's queued output and
resend journal to 1 MiB. Overflow raises a visible recovery condition rather than silently losing
bytes. The packet parser retains incomplete tails and dispatches complete packets synchronously;
a binding replacement discards only the dead connection's partial tail and pending packets.

Display text is decoded incrementally on both sides. The Python `IncrementalTextDecoder` and the
Dart `Utf8Accumulator` each hold an incomplete UTF-8 sequence until its remaining bytes arrive, so
a sequence is never split across a flush boundary, and each turns malformed bytes into U+FFFD
instead of raising. The Python decoder also holds back an incomplete `ESC [` sequence.

ACK and resume u64 fields use two u32 halves because dart2js cannot write them with setUint64.
Applied byte counts clamp negatives to zero and saturate at 2^53−1, JavaScript's largest exact
integer. Continuing past that boundary needs a reset with a new exact counter base.
An output-handler error remains visible; discarding output while acknowledging it would falsely
claim display continuity.

## Recovery and input fencing

Mount hello establishes native identity. A replacement transport must prove the same instance,
matching reset count, and an applied offset covered by the retained journal. A missing baseline
fails closed unless independent zero-traffic checks establish continuity. Resume reports have an
exact fixed 24-byte body, so their values do not control allocations.

During a probe, new writes remain counted but held. Success redelivers only the missing suffix up
to the probe-time end, then flushes later writes once, reasserts current input-enabled state and
sends RESUME_VERIFIED last. Counters do not move for redelivery. Successful resume changes no
viewer generation, controller lifetime, size or ownership and replays no input.

The optional synchronous ownership guard runs immediately before successful delivery. Rejection
or an exception returns incomplete/superseded with the fence armed, counters and journal unchanged,
and no VERIFIED packet. A superseded attempt flushes nothing because its successor owns the batch.
An owning failed attempt flushes its held batch without claiming the unknown gap was repaired;
capture or reset retires the batch and its offsets outright. A terminal that never attached is a
no-op. Any uncertain continuity requires explicit reset-as-watcher recovery by the caller.

The widget latches its input fence on live-to-reconnecting, retiring gesture state at that edge.
Re-registration proves UI plumbing, not engine continuity, and cannot release the fence. Python
also drops effects-bearing inbound packets while verification is pending; state reports and
waiter replies can still flow. Input sent into a dead transport is not replayed later.

## Scroll and keyboard boundaries

Normal-buffer mouse-mode scrolling comes from permanently mounted raw pointer listeners in both
watch and control modes. This works beyond mouse protocol coordinate limits and avoids remounting
the engine. Vertical mouse wheel reports are consumed locally instead of counted twice or sent
to the PTY. Alternate-buffer input follows the application's forwarding policy.

X10 uses `ESC[M btn col row`, with button 32 + ID + modifiers; SGR uses `ESC[<Cb;Cx;CyM/m`,
with Cb = ID + modifiers; urxvt uses `ESC[Cb;Cx;CyM`, with Cb = 32 + ID + modifiers.
Modifier offsets are 4, 8 and 16. IDs 64/65 are vertical wheels and 66/67 horizontal; clicks and
motion cannot forge the required wheel bit and direction bits. Classic X10 coordinates stop at
223; UTF encoding can represent logical coordinates through 2015. Mixed wheel chunks preserve
source-order direction runs and the first report's exact bytes for each run.

A one-shot swipe rounds absolute pixel depth to lines and clears its remainder. Timed wheel
flushes emit complete lines and retain a signed fraction, so slow scrolling accumulates without
moving faster than the input. Alternate-buffer wheel queues cap pending rows and pace each flush;
the initial response is immediate and subsequent arrivals share the armed tick.

Mode, reset and transport boundaries retire swipe, wheel, momentum and pending release velocity.
A new gesture requires a fresh pointer after retirement. CANCEL_SCROLL is sent only after support
is advertised, and only the latest acknowledged nonzero sequence releases the wheel block.
Wrapped u32 wire tokens map to a monotonic Python epoch; the sequence advances only on accepted
packet delivery. Dead/full channels, reset and rebinding cannot leave an outstanding block stuck.

Local page buttons scroll only the normal-buffer viewport and do nothing in the alternate buffer.
Tail following is controlled by reader gestures and jump-to-latest, then reapplied after output or
geometry changes. Layout notifications cannot decide that the reader left the tail.
Direct Input keyboard requests arm focus only after input is enabled; display-only mode blocks
engine output while preserving selection. Physical Shift+Enter explicitly emits LF to match the
application encoder; the ordinary physical Enter key uses the CR rule.

## Snapshots and browser paste

Selection text is active-buffer emulator truth: cursor overwrites and escapes are already applied.
Cell reads restore leading positions while preserving combining and wide glyphs; wrapped rows join,
trailing whitespace is stripped and empty trailing rows are dropped. Alternate-screen snapshots
contain that screen without normal-buffer scrollback.

The receiver validates declared total and offset before allocating, independently of frame size.
A snapshot may declare at most 4 MiB; at most eight requests and 16 MiB of declared totals may be
in flight per terminal. Chunks reassemble by ID and offset, including out-of-order delivery.
Timeouts/reset retire IDs so late chunks cannot contaminate later requests. Oversized truths fail
visibly rather than being silently truncated; engine dimensions must stay within the budget.
An unattached snapshot request fails immediately, allowing the caller's raw-output fallback.

Browser paste retains image Blobs in Dart. Python sees bounded offer metadata and a request ID,
then approves upload to a grant URL or aborts that request; image bytes cross neither Flet events
nor the terminal channel. Clipboard items take priority over file fallback with duplicates removed.
Terminal text follows the application paste event exactly once, while text fields insert natively.
The region changes neither layout nor focus. Terminal hyperlinks do not open untrusted output URLs
without an explicit application action.

Untrusted output cannot invoke clipboard storage, automatic URL opening or native notifications:
the corresponding xterm callbacks stay unset, including OSC 52 and OSC 9/777 handlers.
