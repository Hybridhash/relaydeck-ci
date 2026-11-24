# Security policy

Security fixes are supported for the latest release in the 0.3.x series. Update
to the latest 0.3.x release before reporting a problem; older series and
unreleased development snapshots are not supported.

## Report a vulnerability privately

Use GitHub private vulnerability reporting: open the repository's **Security** tab
and choose **Report a vulnerability**.

Do not put exploit details, terminal content, credentials or private repositories
in public issues. Include the affected version, platform, impact and a minimal
reproduction using synthetic data.

## Release verification

The root [minisign.pub](minisign.pub) verifies the web bundle archive and the
release update record. It does not sign the Python wheel, installer or native
application. The wheel and installer are HTTPS release assets; the installer
checks its embedded wheel hash. A signature authenticates the signed bytes,
not the security of every dependency.

## Scope

RelayDeck authentication, authorization, pairing, terminal control, private
storage, hosted web application and release verification are in scope. Report
upstream dependency vulnerabilities to the upstream project too when relevant.

Third-party services, the private network configuration, operating-system
security and the tools run inside terminals have their own security boundaries.
A compromised Mac or an intentionally authorized terminal command is outside
RelayDeck's isolation guarantees.
