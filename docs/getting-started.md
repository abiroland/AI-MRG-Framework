# Getting started

## Install

AI-MRG is not on PyPI yet. Install from source:

```bash
git clone https://github.com/abiroland/ai-mrg-framework
cd ai-mrg-framework
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Requires Python 3.11 or later.

## Explore the built-in pack

```bash
aimrg pack list
aimrg pack validate fin-us-banking
```

## Use the models from Python

```python
from aimrg.core import AICategory, AssetRecord, Classification
from aimrg.packs import compile_expression, load_builtin_pack

pack = load_builtin_pack("fin-us-banking")

classification = Classification.from_answers(
    is_model_sr26_2=True,
    ai_category=AICategory.ML_NON_GENERATIVE,
    consequential_decision=True,
    rationale="Gradient-boosted credit card underwriting model",
)
asset = AssetRecord(
    name="Retail Card Underwriting v3",
    owner="credit-risk",
    business_uses=["credit_decision"],
    ai_category=AICategory.ML_NON_GENERATIVE,
    classification=classification,
)

profile = pack.tiering_profile("default")
score = profile.score(
    {"complexity": 4, "assumptions": 3, "data_quality": 2, "exposure": 5, "purpose": 5}
)
tier = profile.tier_for(score)  # → 1

context = {
    "track": classification.track.value,
    "tier": tier,
    "ai_category": asset.ai_category.value,
    "uses": asset.business_uses,
    "consequential_decision": classification.consequential_decision,
    "third_party": classification.third_party,
    "jurisdictions": ["US"],
    "stage": asset.status.value,
}
required = [c for c in pack.controls if compile_expression(c.applies_when).evaluate(context)]
for control in required:
    print(control.id, control.title)
```

Phase 1 wraps this into a registry, a control resolver and the `aimrg register / tier / plan` commands.

## Export JSON Schemas

```bash
aimrg schema export schemas
```

The published schemas also give YAML editors (for example VS Code with the YAML extension) autocompletion and validation for pack files.
