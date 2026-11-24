# Client presentation and accessibility

Theme configuration supplies primitive scales, semantic roles and component colours. The application
resolves it once, and `design_system/tokens.py` exposes named constants that controls read;
controls should not define a parallel palette. The default purple presentation changes to light or system mode only after an
explicit user choice. Seasonal overlays affect decorative grounds, borders and fills, while status,
text and terminal colours retain their semantic meaning. The active window or process override
selects an overlay; default disables it.

Bundled fonts are registered by family alias. Because the pinned native loader supplies no weight
descriptors, static weights use distinct aliases, and weight selection rounds down to an available
face. Symbols use fallback-only faces. The terminal uses a bundled monospace face on web rather
than relying on unavailable OS fonts. Font configuration checks establish fallback selection, not
pixel-level glyph rendering.

Keyboard-actionable primitives provide Tab focus, Enter/Space activation and one click action while
preserving the content's geometry. The focus ring keeps a constant width across states; transparent
framework chrome avoids drawing a second border around the product surface. Async handlers are
awaited so ownership, disconnect and paste operations are not dropped as unawaited coroutines.

Destination data comes from the caller in display/traversal order. Bottom navigation applies below
600 dp and the side rail at wider sizes. Selection stays controlled by destination ID. Contrast
helpers compute token ratios against approximate framework surfaces; they cannot certify rendered
pixels or keyboard focusability. Identity labels use a darker light-theme ink against the actual
composited identity wash across normal, selected and hovered tiles, rather than checking bare-card
contrast alone.

Root-owned motion preferences are supplied to loading presentation. Static indicators do not need
an animation phase. Terminal status live-region text omits byte offsets and unseen-byte counters,
which would otherwise announce every output chunk. Optional haptics happen at the eligible tap instead of waiting through network
delivery; an unavailable native/browser feedback service cannot interrupt Send or Copy.

Terminal window builders receive a retained viewport child; they do not own sessions, leases or
writers. Input mode and overlays follow the session's current phase. Shell surfaces stay Direct
without a composer or agent operations. A take-over prompt replaces input controls while watching.
Selection overlays preserve the mounted engine and temporarily disable input. Stable keys and
ancestry let phone and wide layouts share the retained terminal rather than remounting it.

Compact key controls use the shared model for grouping, sticky Ctrl/Alt and long-press alternatives.
Expanded controls retain their full dock. In mixed inline/dock layouts, deduplication hides only
keys already available in the inline row; compact overlays expose the complete selected key page.
Visual chip size and hit region are distinct: compact controls use a 34 px visual inside a 40 px
region, while expanded controls use 48 px. No unverified multi-pane switcher is presented.

Usage charts receive precomputed fractions, labels and tooltips. Segment pixel allocation preserves
a visible sliver while removing rounding excess from taller segments, keeping the sum inside the
plot. The screen explicitly labels unavailable, partial and capacity states; absence of data does
not become no usage. The diff view's fixed row geometry reflects the pinned framework's lack of a
system text-scale field, so it cannot claim dynamic row scaling from that unavailable signal.

Collapsed initials show ?? for an empty display name. Compact host-filter chips use a 44 px target;
that specific compact presentation differs from the default 48 px chip. The focus-order audit
requires an explicit FOCUS_ORDER entry and test for every new screen.
