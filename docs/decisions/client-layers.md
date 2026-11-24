# Client layers

## Context

UI composition, feature behavior and transport have different lifetimes and
dependencies. Shared controls must remain reusable without product state.

## Decision

Use downward imports through application composition, screens, widgets, features,
design system, platform, ports and models. Screens do not import each other.
Cross-feature calls use declared public entries in an acyclic graph. Models and
ports are framework-free. The application owns lifetimes and binds data sources.

## Consequences

Features can be tested independently. Callbacks keep controls free of transport and
application state, and one tokens module supplies the theme values; the
[architecture overview](../architecture/README.md#client-layers) lists the product words
and literal sizes that remain in the design system.
An exhaustive layer contract covers every downward pair, with compatibility
imports beside the design system. Initializers have no exemptions. Keys, ancestry,
hook order and draft ownership remain part of the presentation contract.

## Revisit when

A concrete feature cannot fit these boundaries without cyclic ownership or a
capability that the current interfaces cannot express.
