from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FIN_PACK = REPO_ROOT / "packs" / "fin-us-banking"


@pytest.fixture
def fin_pack_dir() -> Path:
    return FIN_PACK


@pytest.fixture
def pack_copy(tmp_path: Path) -> Path:
    """A writable copy of the fin-us-banking pack for negative tests."""
    target = tmp_path / "pack"
    shutil.copytree(FIN_PACK, target)
    return target


@pytest.fixture
def registry(tmp_path: Path):  # type: ignore[no-untyped-def]
    from aimrg.registry import Registry

    reg = Registry.create(tmp_path / "gov.db", pack=str(FIN_PACK))
    yield reg
    reg.close()


@pytest.fixture(scope="session")
def portfolio():  # type: ignore[no-untyped-def]
    from aimrg.datasets import synthetic_credit_portfolio

    return synthetic_credit_portfolio(seed=7)


def make_model_asset(registry, name: str = "PD Model", tier_scores=None, **classify_kw):  # type: ignore[no-untyped-def]
    """Register, classify and tier a credit model; returns the stored asset."""
    from aimrg.core.classify import classify
    from aimrg.core.models import AICategory, AssetRecord
    from aimrg.tiering import assess_asset

    asset = registry.register(
        AssetRecord(
            name=name,
            owner="credit-risk",
            business_uses=["credit_decision"],
            ai_category=AICategory.ML_NON_GENERATIVE,
        )
    )
    options = {
        "is_model_sr26_2": True,
        "consequential_decision": True,
        "rationale": "PD model",
        "jurisdictions": ["US"],
    }
    options.update(classify_kw)
    registry.set_classification(
        asset.id, classify(ai_category=AICategory.ML_NON_GENERATIVE, **options)
    )
    scores = tier_scores or {
        "complexity": 3,
        "assumptions": 3,
        "data_quality": 2,
        "exposure": 5,
        "purpose": 5,
    }
    return assess_asset(registry, asset.id, scores, assessed_by="validator")
