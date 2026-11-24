# Signed releases

## Context

A downloaded web application and an update notice need an authenticity check
before they become trusted local inputs.

## Decision

Verify the web bundle archive and update record with the published minisign
public key before use. Keep signing keys outside the repository. Production
native signing and store upload remain manual owner operations.

## Consequences

Unsigned or invalid web bundles and update records are rejected. The Python wheel
and installer remain HTTPS release assets, and the installer checks its embedded
wheel hash. These checks do not make the wheel, installer or native package
minisign-signed and do not establish a supply-chain certification.

## Revisit when

The wheel needs an authenticated signed digest, signing keys rotate, or releases
need a different provenance or distribution model.
