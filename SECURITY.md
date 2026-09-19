# Security Policy

## Reporting a vulnerability

Please don't open a public GitHub issue for a security problem — simf makes
live outbound requests driven by user input (character/realm names, Warcraft
Logs report URLs, item lookups), so a public report could double as a
tip-off before there's time to fix it.

- **Preferred:** use this repo's [private security advisory
  form](https://github.com/ainestal/simf/security/advisories/new) (GitHub's
  Security tab → "Report a vulnerability"). It stays private between you and
  the maintainer until you both agree to publish.
- **Alternative:** email info@simf.cc.

Include what you found, how to reproduce it, and its likely impact. Reports
will be acknowledged as soon as possible, with a fix targeted before any
public disclosure.

## Scope

simf is a self-hosted analysis tool; the maintainer also runs a public
read-only instance at https://simf.cc. Both are in scope. Of particular
interest: anything that reads or writes data outside a user's own session,
bypasses the public instance's read-only/rate-limit guards, or makes the
server issue requests to unintended internal or external targets (SSRF) via
user-supplied input.

## Supported versions

simf doesn't have tagged releases yet — `master` is the only supported
branch; fixes land there.
