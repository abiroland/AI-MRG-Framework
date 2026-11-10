# Security Policy

## Supported versions

AI-MRG is pre-1.0. Security fixes are made on the latest release only.

## Reporting a vulnerability

Please **do not** open a public issue. Use GitHub's [private vulnerability reporting](https://github.com/abiroland/ai-mrg-framework/security/advisories/new).

Include what you found, how to reproduce it, and its likely impact. You can expect an acknowledgment within 5 business days and a status update within 15.

## Threat model and release verification

See the [threat model](docs/security/threat-model.md) for what AI-MRG protects, the
mitigations in place, and a deployer hardening checklist. Releases from 0.4.0 carry build
provenance attestations (`gh attestation verify`), a CycloneDX SBOM and `SHA256SUMS`.

## Scope notes

AI-MRG stores governance evidence that may reference sensitive data. Issues of particular interest:

- ways to alter evidence without breaking the hash chain
- bypassing role separation (for example, a developer approving their own model)
- code execution through pack content (control expressions must never execute arbitrary code)
- unintended outbound network calls
- template sandbox escapes in pack report templates
- path traversal through artifact URIs or upload names
