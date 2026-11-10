# Releasing

Releases are published to PyPI as `ai-mrg` by `.github/workflows/release.yml`, using
PyPI trusted publishing (no API token is stored in GitHub).

## One-time setup (maintainer)

1. Create an account on pypi.org and enable two-factor authentication.
2. Account settings → **Publishing** → **Add a new pending publisher**:
   - PyPI project name: `ai-mrg`
   - Owner: `abiroland`
   - Repository name: `AI-MRG-Framework`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
3. In the GitHub repository: Settings → **Environments** → create `pypi`. Optionally add
   yourself as a required reviewer so every release needs a click to publish.

## Each release

1. Update `__version__` in `src/aimrg/__init__.py` and move the `Unreleased` entries in
   `CHANGELOG.md` under the new version. Merge to `main`.
2. On GitHub: **Releases → Draft a new release**, tag `v<version>` (for example `v0.3.0`),
   target `main`, paste the changelog section, **Publish**.
3. The workflow checks that the tag matches `__version__`, builds, smoke-tests the
   wheel, and publishes. The package appears at https://pypi.org/project/ai-mrg/.

Versions follow semantic versioning. Packs carry their own versions (see each
`pack.yaml`).
