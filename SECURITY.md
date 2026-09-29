---
name: rpnh-security-policy
description: "Define the supported prerelease and private vulnerability-reporting path."
metadata:
  document-kind: project-policy
  audience: user-and-contributor
  language: en
  counterpart: SECURITY_ZH.md
  revision: "2026-09-29.1"
  status: v0.1.0rc1
---

[English](SECURITY.md) | [中文](SECURITY_ZH.md)

# Security policy

## Supported version

Security fixes are currently prepared for the `0.1.0rc1` prerelease only.
Older source snapshots and development branches are not maintained release
lines.

## Report a vulnerability

Use GitHub's **Privately report a security vulnerability** action on this
repository's Security tab. Do not open a public issue for an undisclosed
vulnerability, and never include credentials, provider tokens, private endpoints,
raw Registry databases or model transcripts in a report.

Include the affected RPNH version or commit, supported platform, minimal redacted
reproduction, expected and observed boundary, and whether the issue involves the
core runtime, a provider adapter, a frontend/host integration or the read-only
Viewer. Maintainers will triage the report through the private advisory.

Ordinary bugs without a confidentiality impact may use the public issue tracker.
