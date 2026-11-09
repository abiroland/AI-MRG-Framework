# Security Policy

## Supported versions

AI-MRG is pre-1.0. Security fixes are made on the latest release only.

## Reporting a vulnerability

Please **do not** open a public issue. Use GitHub's [private vulnerability reporting](https://github.com/abiroland/ai-mrg-framework/security/advisories/new).

Include what you found, how to reproduce it, and its likely impact. You can expect an acknowledgment within 5 business days and a status update within 15.

## Scope notes

AI-MRG stores governance evidence that may reference sensitive data. Issues of particular interest:

- ways to alter evidence without breaking the hash chain
- bypassing role separation (for example, a developer approving their own model)
- code execution through pack content (control expressions must never execute arbitrary code)
- unintended outbound network calls
