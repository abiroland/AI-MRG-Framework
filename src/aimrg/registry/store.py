"""SQLite-backed registry for assets, evidence and findings.

Design notes
------------
* Uses the standard-library ``sqlite3`` module: zero setup, one file per institution
  (see ADR-0005). Records are stored as validated JSON so the Pydantic models remain the
  single source of truth for structure.
* Evidence is **append-only**. Database triggers reject ``UPDATE`` and ``DELETE`` on the
  evidence table, and each item stores the hash of the previous item for the same asset.
  :meth:`Registry.verify` recomputes the chain and checks stored artifacts.
* Artifacts (check results, documents) are stored by content address (SHA-256) in an
  ``artifacts/`` directory next to the database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aimrg.core.models import (
    AssetRecord,
    Classification,
    EvidenceItem,
    Finding,
    LifecycleEvent,
    RiskAssessment,
    verify_chain,
)
from aimrg.packs.loader import builtin_pack_names, builtin_packs_dir, load_pack
from aimrg.packs.models import Pack

SCHEMA_VERSION = 2
ARTIFACT_SCHEME = "artifact://"

_DDL = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence (
    id          TEXT PRIMARY KEY,
    asset_id    TEXT NOT NULL REFERENCES assets(id),
    seq         INTEGER NOT NULL,
    control_id  TEXT NOT NULL,
    data        TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    UNIQUE (asset_id, seq)
);
CREATE TRIGGER IF NOT EXISTS evidence_no_update BEFORE UPDATE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TRIGGER IF NOT EXISTS evidence_no_delete BEFORE DELETE ON evidence
BEGIN SELECT RAISE(ABORT, 'evidence is append-only'); END;
CREATE TABLE IF NOT EXISTS findings (
    id       TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(id),
    data     TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    asset_id    TEXT NOT NULL REFERENCES assets(id),
    occurred_at TEXT NOT NULL,
    data        TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'lifecycle events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'lifecycle events are append-only'); END;
"""


class RegistryError(Exception):
    """Raised for invalid registry operations (unknown asset, duplicate name, ...)."""


@dataclass(frozen=True)
class AuditReport:
    assets_checked: int
    evidence_checked: int
    problems: list[str]

    @property
    def ok(self) -> bool:
        return not self.problems


def resolve_pack(pack: str) -> Pack:
    """Load a pack given a directory path or the name of a built-in pack."""
    path = Path(pack)
    if path.is_dir():
        return load_pack(path)
    if pack in builtin_pack_names():
        return load_pack(builtin_packs_dir() / pack)
    raise RegistryError(f"pack {pack!r} is neither a directory nor a built-in pack")


class Registry:
    """A governance registry stored in one SQLite file."""

    def __init__(self, path: Path, connection: sqlite3.Connection) -> None:
        self.path = path
        self._conn = connection
        self._pack: Pack | None = None

    # --- lifecycle ------------------------------------------------------------

    @classmethod
    def create(cls, path: str | Path, pack: str = "fin-us-banking") -> Registry:
        """Create a new registry bound to ``pack`` (built-in name or directory)."""
        db_path = Path(path)
        if db_path.exists():
            raise RegistryError(f"{db_path} already exists")
        loaded = resolve_pack(pack)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = cls._connect(db_path)
        conn.executescript(_DDL)
        pack_setting = str(Path(pack).resolve()) if Path(pack).is_dir() else pack
        with conn:
            conn.executemany(
                "INSERT INTO meta (key, value) VALUES (?, ?)",
                [
                    ("schema_version", str(SCHEMA_VERSION)),
                    ("pack", pack_setting),
                    ("pack_ref", loaded.manifest.ref),
                    ("created_at", datetime.now(UTC).isoformat()),
                ],
            )
        registry = cls(db_path, conn)
        registry._pack = loaded
        return registry

    @classmethod
    def open(cls, path: str | Path) -> Registry:
        db_path = Path(path)
        if not db_path.is_file():
            raise RegistryError(f"no registry at {db_path}; create one with `aimrg init`")
        conn = cls._connect(db_path)
        try:
            version = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.DatabaseError as exc:
            conn.close()
            raise RegistryError(f"{db_path} is not a compatible AI-MRG registry") from exc
        if version is None or not 1 <= int(version[0]) <= SCHEMA_VERSION:
            conn.close()
            raise RegistryError(f"{db_path} is not a compatible AI-MRG registry")
        if int(version[0]) < SCHEMA_VERSION:
            cls._migrate(conn)
        return cls(db_path, conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Upgrade an older registry in place. All migrations are additive."""
        with conn:
            conn.executescript(_DDL)
            conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),)
            )

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    def meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    @property
    def pack(self) -> Pack:
        if self._pack is None:
            setting = self.meta("pack")
            if setting is None:  # pragma: no cover - created registries always have one
                raise RegistryError("registry has no pack configured")
            self._pack = resolve_pack(setting)
        return self._pack

    @property
    def artifacts_dir(self) -> Path:
        return self.path.parent / f"{self.path.stem}-artifacts"

    # --- assets -----------------------------------------------------------------

    def register(self, asset: AssetRecord) -> AssetRecord:
        unknown = [dep for dep in asset.depends_on if self._find(dep) is None]
        if unknown:
            raise RegistryError(f"unknown upstream asset(s): {unknown}")
        try:
            with self._tx() as conn:
                conn.execute(
                    "INSERT INTO assets (id, name, data, updated_at) VALUES (?, ?, ?, ?)",
                    (asset.id, asset.name, asset.model_dump_json(), _now()),
                )
        except sqlite3.IntegrityError as exc:
            raise RegistryError(f"an asset named {asset.name!r} or id {asset.id} exists") from exc
        return asset

    def _find(self, ref: str) -> AssetRecord | None:
        row = self._conn.execute(
            "SELECT data FROM assets WHERE id = ? OR name = ?", (ref, ref)
        ).fetchone()
        return None if row is None else AssetRecord.model_validate_json(row[0])

    def get(self, ref: str) -> AssetRecord:
        """Look up an asset by id or exact name."""
        asset = self._find(ref)
        if asset is None:
            raise RegistryError(f"no asset with id or name {ref!r}")
        return asset

    def assets(self) -> list[AssetRecord]:
        rows = self._conn.execute("SELECT data FROM assets ORDER BY name").fetchall()
        return [AssetRecord.model_validate_json(r[0]) for r in rows]

    def save(self, asset: AssetRecord) -> AssetRecord:
        """Persist changes to an existing asset (re-validated before saving)."""
        validated = AssetRecord.model_validate(asset.model_dump())
        current = self._find(validated.id)
        if current is not None and current.status != validated.status:
            raise RegistryError(
                "status changes go through the workflow (aimrg.workflow.transition), not save()"
            )
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE assets SET name = ?, data = ?, updated_at = ? WHERE id = ?",
                (validated.name, validated.model_dump_json(), _now(), validated.id),
            )
        if cursor.rowcount == 0:
            raise RegistryError(f"asset {validated.id} is not registered")
        return validated

    def set_classification(self, ref: str, classification: Classification) -> AssetRecord:
        asset = self.get(ref)
        updated = asset.model_copy(update={"classification": classification})
        return self.save(updated)

    def set_risk_assessment(self, ref: str, assessment: RiskAssessment) -> AssetRecord:
        asset = self.get(ref)
        if asset.classification is None:
            raise RegistryError(f"classify {asset.name!r} before assessing its risk tier")
        return self.save(asset.model_copy(update={"risk_assessment": assessment}))

    def dependents(self, asset_id: str) -> list[AssetRecord]:
        return [a for a in self.assets() if asset_id in a.depends_on]

    # --- artifacts --------------------------------------------------------------

    def store_artifact(self, content: bytes, suffix: str = "") -> tuple[str, str]:
        """Store bytes by content address. Returns ``(uri, sha256)``."""
        digest = hashlib.sha256(content).hexdigest()
        target = self.artifacts_dir / digest[:2] / f"{digest}{suffix}"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        return f"{ARTIFACT_SCHEME}{digest[:2]}/{target.name}", digest

    def store_json_artifact(self, payload: Any) -> tuple[str, str]:
        text = json.dumps(payload, indent=2, sort_keys=True, default=str)
        return self.store_artifact(text.encode("utf-8"), suffix=".json")

    def artifact_path(self, uri: str) -> Path:
        if not uri.startswith(ARTIFACT_SCHEME):
            raise RegistryError(f"not an artifact uri: {uri}")
        return self.artifacts_dir / uri.removeprefix(ARTIFACT_SCHEME)

    def read_artifact(self, uri: str) -> bytes:
        return self.artifact_path(uri).read_bytes()

    # --- evidence ---------------------------------------------------------------

    def add_evidence(self, item: EvidenceItem) -> EvidenceItem:
        """Append evidence, linking it to the previous item for the same asset."""
        asset = self.get(item.asset_id)
        if item.control_id not in {c.id for c in self.pack.controls}:
            raise RegistryError(f"unknown control {item.control_id!r} in {self.pack.manifest.ref}")
        with self._tx() as conn:
            row = conn.execute(
                "SELECT seq, record_hash FROM evidence WHERE asset_id = ? "
                "ORDER BY seq DESC LIMIT 1",
                (asset.id,),
            ).fetchone()
            seq = 1 if row is None else int(row[0]) + 1
            linked = item.model_copy(
                update={"asset_id": asset.id, "prev_hash": None if row is None else row[1]}
            )
            linked = EvidenceItem.model_validate(linked.model_dump())
            conn.execute(
                "INSERT INTO evidence (id, asset_id, seq, control_id, data, record_hash) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    linked.id,
                    asset.id,
                    seq,
                    linked.control_id,
                    linked.model_dump_json(),
                    linked.record_hash(),
                ),
            )
        return linked

    def evidence(self, ref: str | None = None, control_id: str | None = None) -> list[EvidenceItem]:
        query = "SELECT data FROM evidence"
        clauses: list[str] = []
        params: list[str] = []
        if ref is not None:
            clauses.append("asset_id = ?")
            params.append(self.get(ref).id)
        if control_id is not None:
            clauses.append("control_id = ?")
            params.append(control_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY asset_id, seq"
        rows = self._conn.execute(query, params).fetchall()
        return [EvidenceItem.model_validate_json(r[0]) for r in rows]

    def verify(self) -> AuditReport:
        """Recompute every evidence chain and check stored artifacts are unchanged."""
        problems: list[str] = []
        assets = self.assets()
        checked = 0
        for asset in assets:
            rows = self._conn.execute(
                "SELECT data, record_hash FROM evidence WHERE asset_id = ? ORDER BY seq",
                (asset.id,),
            ).fetchall()
            items = [EvidenceItem.model_validate_json(r[0]) for r in rows]
            checked += len(items)
            for item, (_, stored_hash) in zip(items, rows, strict=True):
                if item.record_hash() != stored_hash:
                    problems.append(f"{asset.name}: evidence {item.id} content was altered")
                if item.payload_uri and item.payload_uri.startswith(ARTIFACT_SCHEME):
                    path = self.artifact_path(item.payload_uri)
                    if not path.is_file():
                        problems.append(f"{asset.name}: artifact missing for evidence {item.id}")
                    elif hashlib.sha256(path.read_bytes()).hexdigest() != item.payload_sha256:
                        problems.append(f"{asset.name}: artifact altered for evidence {item.id}")
            problems += [f"{asset.name}: {p}" for p in verify_chain(items)]
        return AuditReport(assets_checked=len(assets), evidence_checked=checked, problems=problems)

    # --- findings ---------------------------------------------------------------

    def add_finding(self, finding: Finding) -> Finding:
        asset = self.get(finding.asset_id)
        stored = finding.model_copy(update={"asset_id": asset.id})
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO findings (id, asset_id, data) VALUES (?, ?, ?)",
                (stored.id, asset.id, stored.model_dump_json()),
            )
        return stored

    def save_finding(self, finding: Finding) -> Finding:
        validated = Finding.model_validate(finding.model_dump())
        with self._tx() as conn:
            cursor = conn.execute(
                "UPDATE findings SET data = ? WHERE id = ?",
                (validated.model_dump_json(), validated.id),
            )
        if cursor.rowcount == 0:
            raise RegistryError(f"finding {validated.id} does not exist")
        return validated

    def get_finding(self, finding_id: str) -> Finding:
        row = self._conn.execute(
            "SELECT data FROM findings WHERE id = ? OR id LIKE ?",
            (finding_id, f"{finding_id}%"),
        ).fetchall()
        if len(row) != 1:
            raise RegistryError(f"no unique finding matching {finding_id!r}")
        return Finding.model_validate_json(row[0][0])

    def findings(self, ref: str | None = None) -> list[Finding]:
        if ref is None:
            rows = self._conn.execute("SELECT data FROM findings").fetchall()
        else:
            rows = self._conn.execute(
                "SELECT data FROM findings WHERE asset_id = ?", (self.get(ref).id,)
            ).fetchall()
        items = [Finding.model_validate_json(r[0]) for r in rows]
        return sorted(items, key=lambda f: f.opened_at)

    # --- lifecycle events -------------------------------------------------------

    def record_event(self, event: LifecycleEvent) -> LifecycleEvent:
        """Append a lifecycle event and update the asset's status (one transaction).

        Use :func:`aimrg.workflow.transition` rather than calling this directly: it
        enforces the gates.
        """
        asset = self.get(event.asset_id)
        if asset.status != event.from_status:
            raise RegistryError(
                f"{asset.name!r} is {asset.status.value}, not {event.from_status.value}"
            )
        stored = event.model_copy(update={"asset_id": asset.id})
        updated = asset.model_copy(update={"status": event.to_status})
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO events (id, asset_id, occurred_at, data) VALUES (?, ?, ?, ?)",
                (stored.id, asset.id, stored.occurred_at.isoformat(), stored.model_dump_json()),
            )
            conn.execute(
                "UPDATE assets SET data = ?, updated_at = ? WHERE id = ?",
                (updated.model_dump_json(), _now(), asset.id),
            )
        return stored

    def events(self, ref: str) -> list[LifecycleEvent]:
        rows = self._conn.execute(
            "SELECT data FROM events WHERE asset_id = ? ORDER BY occurred_at, rowid",
            (self.get(ref).id,),
        ).fetchall()
        return [LifecycleEvent.model_validate_json(r[0]) for r in rows]


def _now() -> str:
    return datetime.now(UTC).isoformat()
