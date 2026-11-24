# One wheel

## Context

Installation, updates and release tooling consume one wheel. Native packaging
selects source folders rather than installing a separate client distribution.

## Decision

Keep one distribution containing the client and Mac packages. Use the companion
extra for server dependencies and import contracts for architectural isolation.

## Consequences

One release version covers both sides. Packaging stays simple, while contracts
and native staging checks must prevent Mac dependencies from reaching devices.

## Revisit when

A service ships independently, dependencies conflict between platforms, external
consumers need a contracts-only distribution, or native packaging installs a
client wheel.
