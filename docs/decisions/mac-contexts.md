# Mac contexts

## Context

Private-file handling, persistence, network adapters and release verification are
shared capabilities. Application services should not import each other's
composition roots to obtain them.

## Decision

Put shared Mac capabilities in host foundations and signed-release handling in
releases. Separate gateway application composition, HTTP adapters and features.
Keep the companion as the Mac composition root, including the hosted web
application. Gateway and connector remain independent; repository reading and
usage expose public APIs. Features receive narrow capabilities.

## Consequences

Construction and shutdown have explicit owners. HTTP framework details remain at
the boundary. Host foundations can be reused without importing application policy,
and the device client remains separate from Mac services.

## Revisit when

A gateway or connector ships as an independent service, or a new platform needs a
different composition root.
