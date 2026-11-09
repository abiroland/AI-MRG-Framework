from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aimrg.core.models import (
    AICategory,
    AssetRecord,
    EvidenceItem,
    EvidenceType,
    Finding,
    FindingStatus,
    LifecycleStatus,
    Severity,
)
from aimrg.registry import Registry, RegistryError, import_assets, resolve_pack


def _asset(name: str = "A", **kw: object) -> AssetRecord:
    data: dict[str, object] = {
        "name": name,
        "owner": "o",
        "business_uses": ["credit_decision"],
        "ai_category": AICategory.TRADITIONAL,
    }
    data.update(kw)
    return AssetRecord.model_validate(data)


def _evidence(registry: Registry, asset_id: str, **kw: object) -> EvidenceItem:
    data: dict[str, object] = {
        "asset_id": asset_id,
        "control_id": "FIN-INV-001",
        "type": EvidenceType.DOCUMENT,
        "pack_version": registry.pack.manifest.ref,
        "actor": "tester",
    }
    data.update(kw)
    return EvidenceItem.model_validate(data)


def test_create_and_reopen(tmp_path: Path) -> None:
    path = tmp_path / "gov.db"
    with Registry.create(path, pack="fin-us-banking") as reg:
        reg.register(_asset())
        assert reg.meta(
            "pack_ref",
        ) and reg.meta("pack_ref").startswith("fin-us-banking@")  # type: ignore[union-attr]
    with Registry.open(path) as reopened:
        assert [a.name for a in reopened.assets()] == ["A"]
        assert reopened.pack.manifest.name == "fin-us-banking"


def test_create_refuses_existing_and_bad_pack(tmp_path: Path) -> None:
    path = tmp_path / "gov.db"
    Registry.create(path).close()
    with pytest.raises(RegistryError, match="already exists"):
        Registry.create(path)
    with pytest.raises(RegistryError, match="neither"):
        Registry.create(tmp_path / "other.db", pack="nope")
    with pytest.raises(RegistryError, match="neither"):
        resolve_pack("nope")


def test_open_missing_or_foreign_db(tmp_path: Path) -> None:
    with pytest.raises(RegistryError, match="no registry"):
        Registry.open(tmp_path / "absent.db")
    foreign = tmp_path / "foreign.db"
    conn = sqlite3.connect(foreign)
    conn.execute("CREATE TABLE meta (key TEXT, value TEXT)")
    conn.commit()
    conn.close()
    with pytest.raises(RegistryError, match="compatible"):
        Registry.open(foreign)


def test_register_get_and_save(registry: Registry) -> None:
    a = registry.register(_asset("Alpha"))
    assert registry.get("Alpha").id == a.id
    assert registry.get(a.id).name == "Alpha"
    with pytest.raises(RegistryError, match="exists"):
        registry.register(_asset("Alpha"))
    with pytest.raises(RegistryError, match="no asset"):
        registry.get("missing")
    with pytest.raises(RegistryError, match="unknown upstream"):
        registry.register(_asset("Beta", depends_on=["nope"]))
    renamed = registry.save(a.model_copy(update={"description": "changed"}))
    assert registry.get(a.id).description == "changed" == renamed.description
    assert (
        registry.set_status(a.id, LifecycleStatus.VALIDATION).status is LifecycleStatus.VALIDATION
    )
    with pytest.raises(RegistryError, match="not registered"):
        registry.save(_asset("Ghost"))


def test_risk_assessment_requires_classification(registry: Registry) -> None:
    from aimrg.tiering import assess_asset

    a = registry.register(_asset())
    with pytest.raises(RegistryError, match="classify"):
        assess_asset(
            registry,
            a.id,
            {"complexity": 1, "assumptions": 1, "data_quality": 1, "exposure": 1, "purpose": 1},
            assessed_by="me",
        )


def test_evidence_chain_and_append_only(registry: Registry) -> None:
    a = registry.register(_asset())
    first = registry.add_evidence(_evidence(registry, a.name))
    second = registry.add_evidence(_evidence(registry, a.id, summary="second"))
    assert first.prev_hash is None
    assert second.prev_hash == first.record_hash()
    assert first.asset_id == a.id  # name resolved to id
    assert [e.id for e in registry.evidence(a.id)] == [first.id, second.id]
    assert registry.evidence(control_id="FIN-INV-001")
    assert registry.verify().ok

    conn = registry._conn
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("UPDATE evidence SET data = '{}'")
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        conn.execute("DELETE FROM evidence")


def test_evidence_rejects_unknown_control(registry: Registry) -> None:
    a = registry.register(_asset())
    with pytest.raises(RegistryError, match="unknown control"):
        registry.add_evidence(_evidence(registry, a.id, control_id="FIN-XXX-999"))


def test_verify_detects_tampering(registry: Registry) -> None:
    a = registry.register(_asset())
    uri, digest = registry.store_artifact(b"original", suffix=".txt")
    registry.add_evidence(_evidence(registry, a.id, payload_uri=uri, payload_sha256=digest))
    registry.add_evidence(_evidence(registry, a.id, summary="second"))

    # Tamper with an artifact on disk.
    registry.artifact_path(uri).write_bytes(b"edited")
    # Tamper with the database by bypassing the triggers.
    conn = registry._conn
    conn.execute("DROP TRIGGER evidence_no_update")
    row = conn.execute("SELECT id, data FROM evidence WHERE seq = 2").fetchone()
    conn.execute(
        "UPDATE evidence SET data = ? WHERE id = ?",
        (row[1].replace('"second"', '"rewritten"'), row[0]),
    )
    conn.commit()
    problems = registry.verify().problems
    assert any("artifact altered" in p for p in problems)
    assert any("content was altered" in p for p in problems)

    registry.artifact_path(uri).unlink()
    assert any("artifact missing" in p for p in registry.verify().problems)


def test_artifacts_are_content_addressed(registry: Registry) -> None:
    uri1, sha1 = registry.store_artifact(b"same")
    uri2, sha2 = registry.store_artifact(b"same")
    assert (uri1, sha1) == (uri2, sha2)
    assert registry.read_artifact(uri1) == b"same"
    with pytest.raises(RegistryError, match="not an artifact"):
        registry.artifact_path("file:///etc/passwd")


def test_findings_lifecycle(registry: Registry) -> None:
    a = registry.register(_asset())
    f = registry.add_finding(
        Finding(asset_id=a.name, severity=Severity.HIGH, title="gap", owner="o")
    )
    assert f.asset_id == a.id
    assert registry.get_finding(f.id[:6]).id == f.id
    with pytest.raises(RegistryError, match="unique"):
        registry.get_finding("zzzz")
    closed = registry.save_finding(
        f.model_copy(update={"status": FindingStatus.CLOSED, "remediation_evidence": ["e1"]})
    )
    assert registry.findings(a.id)[0].status is FindingStatus.CLOSED == closed.status
    assert len(registry.findings()) == 1
    with pytest.raises(RegistryError, match="does not exist"):
        registry.save_finding(Finding(asset_id=a.id, severity=Severity.LOW, title="t", owner="o"))


# --- import ---------------------------------------------------------------------


def test_import_csv_with_dependencies(registry: Registry, tmp_path: Path) -> None:
    csv_file = tmp_path / "inventory.csv"
    csv_file.write_text(
        "name,owner,business_uses,ai_category,vendor,depends_on\n"
        "Scorecard,risk,credit_decision;pricing,traditional,,Bureau Feed\n"
        "Bureau Feed,data,feature_engineering,traditional,Acme Data,\n",
        encoding="utf-8",
    )
    created = import_assets(registry, csv_file)
    assert [a.name for a in created] == ["Bureau Feed", "Scorecard"]
    scorecard = registry.get("Scorecard")
    assert scorecard.business_uses == ["credit_decision", "pricing"]
    assert scorecard.depends_on == [registry.get("Bureau Feed").id]
    assert registry.get("Bureau Feed").vendor == "Acme Data"


def test_import_yaml(registry: Registry, tmp_path: Path) -> None:
    registry.register(_asset("Existing"))
    yml = tmp_path / "inv.yaml"
    yml.write_text(
        "- name: Chatbot\n  owner: cx\n  business_uses: [customer_service]\n"
        "  ai_category: generative\n  depends_on: [Existing]\n",
        encoding="utf-8",
    )
    (created,) = import_assets(registry, yml)
    assert created.depends_on == [registry.get("Existing").id]


def test_import_is_all_or_nothing(registry: Registry, tmp_path: Path) -> None:
    bad = tmp_path / "bad.csv"
    bad.write_text(
        "name,owner,business_uses,ai_category\n"
        "Good,o,credit_decision,traditional\n"
        "Bad,o,credit_decision,quantum\n"
        "Good,o,credit_decision,traditional\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError) as excinfo:
        import_assets(registry, bad)
    message = str(excinfo.value)
    assert "row 3: ai_category" in message
    assert "duplicate asset name 'Good'" in message
    assert registry.assets() == []


def test_import_detects_unknown_upstream_and_cycles(registry: Registry, tmp_path: Path) -> None:
    unknown = tmp_path / "u.yaml"
    unknown.write_text(
        "- {name: A, owner: o, business_uses: [x], ai_category: traditional, depends_on: [Z]}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="unknown upstream"):
        import_assets(registry, unknown)
    cycle = tmp_path / "c.yaml"
    cycle.write_text(
        "- {name: A, owner: o, business_uses: [x], ai_category: traditional, depends_on: [B]}\n"
        "- {name: B, owner: o, business_uses: [x], ai_category: traditional, depends_on: [A]}\n",
        encoding="utf-8",
    )
    with pytest.raises(RegistryError, match="cycle"):
        import_assets(registry, cycle)
    assert registry.assets() == []


def test_import_rejects_bad_files(registry: Registry, tmp_path: Path) -> None:
    txt = tmp_path / "inv.txt"
    txt.write_text("x", encoding="utf-8")
    with pytest.raises(RegistryError, match="unsupported"):
        import_assets(registry, txt)
    notlist = tmp_path / "inv.yaml"
    notlist.write_text("name: A\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="list of assets"):
        import_assets(registry, notlist)
