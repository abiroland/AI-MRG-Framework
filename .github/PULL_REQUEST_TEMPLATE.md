## What and why

<!-- What does this change, and what problem does it solve? Link issues. -->

## Type

- [ ] Code
- [ ] Pack content (controls, citations, thresholds, templates)
- [ ] Docs

## Checklist

- [ ] `ruff check . && ruff format --check .` passes
- [ ] `mypy` passes
- [ ] `pytest` passes, with tests added for new behavior
- [ ] `aimrg pack validate packs/fin-us-banking` passes (if packs changed)
- [ ] `aimrg schema export schemas --check` passes (if models changed)
- [ ] `CHANGELOG.md` updated
- [ ] Pack changes cite a primary source
- [ ] Commits are signed off (`git commit -s`)
