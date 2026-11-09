# Contributing to AI-MRG

Thanks for helping. Contributions come in two kinds, and they are reviewed differently:

| Kind | Examples | Reviewed for |
|---|---|---|
| **Code** | checks, storage, CLI, API, dashboard | correctness, tests, typing, security |
| **Content** | controls, citations, thresholds, tiering profiles, templates in `packs/` | regulatory accuracy and sourcing |

## Ground rules

- **Cite primary sources.** Every control reference must point to a source in the pack's `sources.yaml`. Quote the regulator's document, not a vendor blog.
- **No compliance claims.** Templates and docs must never state or imply that using AI-MRG makes an institution compliant.
- **No real customer data** in issues, tests or examples. Use public or synthetic data.

## Development setup

```bash
git clone https://github.com/abiroland/ai-mrg-framework
cd ai-mrg-framework
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Before opening a PR, run the same checks CI runs:

```bash
ruff check . && ruff format --check .
mypy
pytest
aimrg pack validate packs/fin-us-banking
aimrg schema export schemas --check
```

If you changed a model in `src/aimrg/core` or `src/aimrg/packs/models.py`, regenerate the published schemas with `aimrg schema export schemas`.

## Changing a pack

1. Edit the YAML under `packs/<pack>/`.
2. Run `aimrg pack validate packs/<pack>`.
3. Add an entry to `CHANGELOG.md` under *Unreleased → Packs*, citing the source that motivated the change.
4. Use the **Control change** issue template if you want to discuss it first.

## Sign-off (DCO)

We use the [Developer Certificate of Origin](https://developercertificate.org/) instead of a CLA. Sign each commit:

```bash
git commit -s -m "your message"
```

By contributing, you agree that your contribution is licensed under the Apache License 2.0.
