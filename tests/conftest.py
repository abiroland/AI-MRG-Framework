from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager
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


POSTGRES_URL = os.environ.get("AIMRG_TEST_POSTGRES")


@contextmanager
def _postgres_database():  # type: ignore[no-untyped-def]
    import psycopg

    assert POSTGRES_URL
    name = f"aimrg_test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield POSTGRES_URL.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(POSTGRES_URL, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


@pytest.fixture(params=["sqlite", "postgres"])
def registry(request: pytest.FixtureRequest, tmp_path: Path):  # type: ignore[no-untyped-def]
    """A fresh registry on each backend. PostgreSQL runs when AIMRG_TEST_POSTGRES is set
    to a server URL whose user may create databases."""
    from aimrg.registry import Registry

    if request.param == "sqlite":
        reg = Registry.create(tmp_path / "gov.db", pack=str(FIN_PACK))
        yield reg
        reg.close()
        return
    if not POSTGRES_URL:
        pytest.skip("set AIMRG_TEST_POSTGRES to run PostgreSQL tests")
    with _postgres_database() as url:
        reg = Registry.create(url, pack=str(FIN_PACK), artifacts_dir=tmp_path / "artifacts")
        yield reg
        reg.close()


def disable_append_only(registry, table: str) -> None:  # type: ignore[no-untyped-def]
    """Drop the append-only trigger on ``table`` (to simulate an attacker in tests)."""
    if registry.backend == "sqlite":
        prefix = "evidence" if table == "evidence" else "events"
        registry._db.script(f"DROP TRIGGER {prefix}_no_update; DROP TRIGGER {prefix}_no_delete;")
    else:
        registry._db.script(f"DROP TRIGGER {table}_append_only ON {table};")


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
