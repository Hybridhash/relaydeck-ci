# Architecture

RelayDeck ships one Python distribution under `src/relaydeck`. The client runs on
devices; the companion, gateway, connector, repository reader and usage collector
run on the Mac. The companion extra supplies server dependencies, while native
packaging selects the client and shared kernel. Separate distributions would not
enforce imports; the contracts in [pyproject.toml](../../pyproject.toml) do.

## Package boundaries

The shared kernel contains contracts, pure domain state, version information and
lease diagnostics. It uses only the standard library and may be used by both the
client and Mac services.

Host foundations own private files, trusted executables, persistence, concurrency,
transport, security and observability. They depend on the kernel, not the client
or application services. Releases own signed records, downloads and installed web
bundles above those foundations.

The gateway separates application composition, the HTTP boundary and domain
features. HTTP adapters own framework details, validation and error responses.
Features own policy, expose a public Python entry and receive narrow typed
capabilities. Cross-feature workflows belong to application composition.

The companion is the Mac composition root. It constructs the gateway, connector,
repository and usage services, composes the hosted web application and owns startup
and shutdown. Standalone gateway and connector commands have small bootstraps.
The gateway and connector stay independent. Repository reading and usage stay
independent and are consumed through their public APIs. Development tooling is
not a production dependency; an explicit demo mode loads its tooling lazily.
Runtime code never imports scripts.

## Client layers

The target order is application composition, screens, product widgets, features,
design system, platform, ports and models. Imports point downward, and a layer
may skip intermediate layers when its contract allows it. Models are pure values.
Ports define interfaces and shared errors. Neither imports Flet, network clients
or platform implementations.

Platform adapters implement transport, storage and framework helpers. Only the
Flet-specific platform boundary imports Flet there. The design system takes data
and callbacks from its callers rather than transport or application state, and
reads one tokens module (`design_system/tokens.py`) whose values come from
`theme.toml`. Product widgets compose controls without owning application
lifetimes. Features own behavior; screens assemble a feature's presentation and
never import other screens. The application owns page, navigation, account and
session lifetimes.

Three things about the design system are not yet in their final form. Its controls
read the tokens module instead of taking the tokens as an argument. Some sizes, radii and
paddings are literals in its modules, and some type styles are defined outside
`theme.py`. Some product words remain: terminal lease state words (`controls.py`),
agent status labels (`theme.py`, shown by `status.py`), the agent identity and
space tint helpers (`theme.py`), the usage colour type on `Sheet` (`sheets.py`) and
default keys such as `usage-loading-icon` and `settings-header`. Every colour
comes from the theme: no colour literal appears in the client outside
`design_system/theme.py`. Passing the tokens to each control, removing the product
words and moving the sizes into `theme.toml` is planned for 0.3.1.

Features use each other only through explicitly declared public entries, and
their graph has no cycles. Data reaches screens through sources; screens and
widgets do not import transport implementations. Caches identify their account,
host and generation. Page-owned state does not become a process-global singleton.

## Enforcement

Import contracts cover every client layer pair. Only exact permanent exceptions
are allowed; unused exceptions fail. Public-API edges stay visible to cycle checks.
Initializers are inert. Native packaging must work without Mac-only packages,
and the installed wheel must work without a source checkout.

See the [decision index](../decisions/README.md) for all records, including the
decisions on [one wheel](../decisions/one-wheel.md),
[client layers](../decisions/client-layers.md),
[Mac contexts](../decisions/mac-contexts.md) and
[signed releases](../decisions/signed-releases.md), and the
[compatibility policy](../development/compatibility.md).

## Runtime details

- [Inventory reduction and selection](inventory.md)
- [Shared wire contracts](contracts.md)
- [Development runtime](development-runtime.md)
- [Host files and durable commands](host-runtime.md)
- [Release verification](release-verification.md)
- [Repository reads](repository-reads.md)
- [Connector lifetimes](connector-lifetimes.md)
- [Companion lifetimes](companion-lifetimes.md)
- [Usage collection](usage-collection.md)
- [Gateway security](gateway-security.md)
- [Gateway streams](gateway-streams.md)
- [Native notification extension](notification-extension.md)
- [Native terminal transport](terminal-extension.md)
- [Client ownership and recovery](client-lifetimes.md)
- [Drafts and input safety](client-input.md)
- [Reading contexts and refresh](client-data.md)
- [Presentation and accessibility](client-appearance.md)

See also [usage hook management](../development/usage-hooks.md).
