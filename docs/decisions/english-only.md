# English for 0.3

## Context

The interface uses English copy and does not yet have a translation catalog or a
complete device-locale and right-to-left presentation policy.

## Decision

Ship the 0.3 series in English. Treat translation, locale-aware date and number
formats, currency presentation and right-to-left layout as a later coordinated
change. Unicode terminal content remains content supplied by the remote tools.

## Consequences

English is the documented interface language. Users should not expect device
language settings to translate the application or fully localize formats.
Reusable controls take text through their inputs to make later localization
possible.

## Revisit when

Additional languages are supported and translated copy, formats, layout,
accessibility and release checks can be maintained together.
