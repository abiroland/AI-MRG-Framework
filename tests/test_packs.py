from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from aimrg.packs import (
    PackValidationError,
    builtin_pack_names,
    compile_expression,
    load_builtin_pack,
    load_pack,
)
from aimrg.packs.models import Control, Threshold, TieringProfile


def _rewrite(path: Path, mutate: object) -> None:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    mutate(data)  # type: ignore[operator]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _problems(pack_dir: Path) -> list[str]:
    with pytest.raises(PackValidationError) as excinfo:
        load_pack(pack_dir)
    return [str(p) for p in excinfo.value.problems]


# --- the shipped pack ---------------------------------------------------------


def test_fin_pack_loads(fin_pack_dir: Path) -> None:
    pack = load_pack(fin_pack_dir)
    assert pack.manifest.name == "fin-us-banking"
    assert pack.manifest.reviewed is False
    assert len(pack.controls) >= 25
    assert {s.id for s in pack.sources} >= {"SR-26-2", "ECOA-REGB", "NIST-AI-RMF-1.0"}
    assert pack.tiering_profile("default").id == "default"
    assert pack.control("FIN-CPR-001").title.startswith("Specific")


def test_fin_pack_is_builtin() -> None:
    assert "fin-us-banking" in builtin_pack_names()
    assert load_builtin_pack("fin-us-banking").manifest.ref.startswith("fin-us-banking@")
    with pytest.raises(KeyError):
        load_builtin_pack("nope")


def test_every_control_references_a_known_source(fin_pack_dir: Path) -> None:
    pack = load_pack(fin_pack_dir)
    source_ids = {s.id for s in pack.sources}
    for control in pack.controls:
        assert {r.source for r in control.references} <= source_ids


def test_genai_assets_get_ai_system_controls_not_model_validation(fin_pack_dir: Path) -> None:
    """SR 26-2 excludes GenAI/agentic AI; they must land on the AI-system track."""
    pack = load_pack(fin_pack_dir)
    context = {
        "track": "ai_system",
        "tier": 1,
        "ai_category": "generative",
        "uses": ["customer_service"],
        "consequential_decision": False,
        "third_party": True,
        "jurisdictions": ["US"],
        "stage": "intake",
    }
    applicable = {
        c.id for c in pack.controls if compile_expression(c.applies_when).evaluate(context)
    }
    assert {"FIN-AIS-001", "FIN-AIS-003", "FIN-TPM-001"} <= applicable
    assert "FIN-VAL-001" not in applicable
    assert "FIN-AIS-004" not in applicable  # agent-only


def test_default_tiering_profile_scores(fin_pack_dir: Path) -> None:
    profile = load_pack(fin_pack_dir).tiering_profile("default")
    ids = [f.id for f in profile.factors]
    assert profile.tier_for(profile.score(dict.fromkeys(ids, 5))) == 1
    assert profile.tier_for(profile.score(dict.fromkeys(ids, 1))) == 4
    with pytest.raises(ValueError, match="missing"):
        profile.score({})
    with pytest.raises(ValueError, match="outside"):
        profile.score(dict.fromkeys(ids, 9))


# --- validation catches problems ------------------------------------------------


def test_missing_directory(tmp_path: Path) -> None:
    assert "does not exist" in _problems(tmp_path / "absent")[0]


def test_duplicate_control_ids(pack_copy: Path) -> None:
    def dup(data: dict) -> None:  # type: ignore[type-arg]
        data["controls"].append(dict(data["controls"][0]))

    _rewrite(pack_copy / "controls" / "inventory.yaml", dup)
    assert any("duplicate control id FIN-INV-001" in p for p in _problems(pack_copy))


def test_unknown_source_reference(pack_copy: Path) -> None:
    def bad_ref(data: dict) -> None:  # type: ignore[type-arg]
        data["controls"][0]["references"] = [{"source": "SR-11-7"}]

    _rewrite(pack_copy / "controls" / "inventory.yaml", bad_ref)
    assert any("unknown source 'SR-11-7'" in p for p in _problems(pack_copy))


def test_unsafe_expression_is_reported(pack_copy: Path) -> None:
    def unsafe(data: dict) -> None:  # type: ignore[type-arg]
        data["controls"][0]["applies_when"] = "__import__('os')"

    _rewrite(pack_copy / "controls" / "inventory.yaml", unsafe)
    problems = _problems(pack_copy)
    assert any("controls/inventory.yaml" in p and "not allowed" in p for p in problems)


def test_invalid_yaml_and_empty_files(pack_copy: Path) -> None:
    (pack_copy / "controls" / "broken.yaml").write_text("controls: [\n", encoding="utf-8")
    (pack_copy / "thresholds" / "empty.yaml").write_text("", encoding="utf-8")
    problems = _problems(pack_copy)
    assert any("broken.yaml: invalid YAML" in p for p in problems)
    assert any("empty.yaml: file is empty" in p for p in problems)


def test_missing_manifest_and_sections(pack_copy: Path) -> None:
    (pack_copy / "pack.yaml").unlink()
    for sub in ("controls", "tiering"):
        for f in (pack_copy / sub).iterdir():
            f.unlink()
    problems = _problems(pack_copy)
    assert any("pack.yaml: file not found" in p for p in problems)
    assert any("no control files" in p for p in problems)
    assert any("no tiering profiles" in p for p in problems)


def test_duplicate_sources_profiles_and_thresholds(pack_copy: Path) -> None:
    _rewrite(pack_copy / "sources.yaml", lambda d: d["sources"].append(d["sources"][0]))
    (pack_copy / "tiering" / "copy.yaml").write_text(
        (pack_copy / "tiering" / "default.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (pack_copy / "thresholds" / "copy.yaml").write_text(
        (pack_copy / "thresholds" / "default.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    problems = _problems(pack_copy)
    assert any("duplicate source id SR-26-2" in p for p in problems)
    assert any("duplicate tiering profile id" in p for p in problems)
    assert any("duplicate threshold" in p for p in problems)


def test_schema_errors_include_location(pack_copy: Path) -> None:
    _rewrite(pack_copy / "pack.yaml", lambda d: d.update(version="one"))
    assert any("pack.yaml: version:" in p for p in _problems(pack_copy))


# --- model-level rules ----------------------------------------------------------


def _control(**overrides: object) -> Control:
    data: dict[str, object] = {
        "id": "FIN-TST-001",
        "title": "t",
        "objective": "o",
        "lifecycle_stage": "validation",
        "frequency": "once",
        "evidence_required": ["document"],
        "references": [{"source": "SR-26-2"}],
    }
    data.update(overrides)
    return Control.model_validate(data)


def test_control_with_checks_requires_check_evidence() -> None:
    with pytest.raises(ValidationError, match="check_result"):
        _control(checks=[{"id": "performance.out_of_time"}])


def test_control_id_format() -> None:
    with pytest.raises(ValidationError):
        _control(id="fin-1")


def test_tiering_profile_rules() -> None:
    factor = {"id": "exposure", "description": "d", "weight": 1}
    good_cutoffs = [
        {"tier": 1, "min_score": 0.7},
        {"tier": 2, "min_score": 0.4},
        {"tier": 3, "min_score": 0.2},
        {"tier": 4, "min_score": 0.0},
    ]
    TieringProfile.model_validate(
        {"id": "p", "description": "d", "factors": [factor], "cutoffs": good_cutoffs}
    )
    bad_order = [dict(c) for c in good_cutoffs]
    bad_order[1]["min_score"] = 0.9
    with pytest.raises(ValidationError, match="strictly decrease"):
        TieringProfile.model_validate(
            {"id": "p", "description": "d", "factors": [factor], "cutoffs": bad_order}
        )
    no_floor = [dict(c) for c in good_cutoffs]
    no_floor[3]["min_score"] = 0.1
    with pytest.raises(ValidationError, match="tier 4"):
        TieringProfile.model_validate(
            {"id": "p", "description": "d", "factors": [factor], "cutoffs": no_floor}
        )
    with pytest.raises(ValidationError, match="unique"):
        TieringProfile.model_validate(
            {"id": "p", "description": "d", "factors": [factor, factor], "cutoffs": good_cutoffs}
        )
    with pytest.raises(ValidationError, match="exactly once"):
        TieringProfile.model_validate(
            {
                "id": "p",
                "description": "d",
                "factors": [factor],
                "cutoffs": [*good_cutoffs[:3], {"tier": 3, "min_score": 0.0}],
            }
        )
    with pytest.raises(ValidationError, match="scale"):
        TieringProfile.model_validate(
            {
                "id": "p",
                "description": "d",
                "factors": [{**factor, "scale_min": 5, "scale_max": 1}],
                "cutoffs": good_cutoffs,
            }
        )
    with pytest.raises(ValidationError, match="outside the scale"):
        TieringProfile.model_validate(
            {
                "id": "p",
                "description": "d",
                "factors": [{**factor, "guide": {9: "x"}}],
                "cutoffs": good_cutoffs,
            }
        )


def test_threshold_rules() -> None:
    Threshold(warn=0.1, fail=0.25, direction="above")
    Threshold(warn=0.9, fail=0.8, direction="below")
    with pytest.raises(ValidationError, match="before fail"):
        Threshold(warn=0.3, fail=0.25, direction="above")
    with pytest.raises(ValidationError, match="at least one"):
        Threshold(direction="above")
