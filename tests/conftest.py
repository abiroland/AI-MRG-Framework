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
