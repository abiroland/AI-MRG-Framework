"""The governance registry: assets, evidence, findings, lifecycle events, users, webhooks.

Design notes
------------
* Storage is SQLite (one file, the default; ADR-0005) or PostgreSQL (multi-user;
  ADR-0006). Pass a file path or a ``postgresql://`` URL. Records are stored as validated
  JSON so the Pydantic models remain the single source of truth for structure.
* Evidence and lifecycle events are **append-only**: database triggers reject ``UPDATE``
  and ``DELETE``, and each evidence item stores the hash of the previous item for the
  same asset. :meth:`Registry.verify` recomputes the chain and checks stored artifacts.
* Artifacts (check results, documents) are stored by content address (SHA-256) in an
  artifacts directory: next to a SQLite file by default, or configured explicitly.
* Webhooks, if configured, are notified after changes are committed. No outbound calls
  are made unless an administrator adds a webhook.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aimrg.core.models import (
    AssetRecord,
    CheckOutcome,
    Classification,
    EvidenceItem,
    EvidenceType,
    Finding,
    FindingStatus,
    LifecycleEvent,
    RiskAssessment,
    verify_chain,
)
from aimrg.packs.loader import builtin_pack_names, builtin_packs_dir, load_pack
from aimrg.packs.models import Pack
from aimrg.registry.accounts import (
    Delivery,
    Role,
    User,
    Webhook,
    deliver,
    hash_token,
    new_token,
)
from aimrg.registry.backends import (
    BackendError,
    Database,
    IntegrityViolation,
    SQLiteDatabase,
    connect,
    is_postgres_url,
)

SCHEMA_VERSION = 3
ARTIFACT_SCHEME = "artifact://"
_RESOLVED = (FindingStatus.CLOSED, FindingStatus.RISK_ACCEPTED)


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


def _describe(location: str | Path) -> str:
    text = str(location)
    if is_postgres_url(text) and "@" in text:
        scheme, rest = text.split("://", 1)
        return f"{scheme}://***@{rest.split('@', 1)[1]}"  # hide credentials
    return text


@dataclass
class _Hooks:
    deliveries: list[Delivery] = field(default_factory=list)
    listeners: list[Callable[[str, dict[str, Any]], None]] = field(default_factory=list)


class Registry:
    """A governance registry (SQLite file or PostgreSQL database)."""

    def __init__(self, location: str | Path, db: Database) -> None:
        self.location = str(location)
        self._db = db
        self._pack: Pack | None = None
        self._hooks = _Hooks()

    # --- lifecycle ------------------------------------------------------------

    @classmethod
    def create(
        cls,
        location: str | Path,
        pack: str = "fin-us-banking",
        artifacts_dir: str | Path | None = None,
    ) -> Registry:
        """Create a new registry bound to ``pack`` (built-in name or directory).

        ``location`` is a SQLite file path or a ``postgresql://`` URL. PostgreSQL
        registries need ``artifacts_dir`` (a directory or shared mount all users can reach).
        """
        text = str(location)
        postgres = is_postgres_url(text)
        if not postgres and Path(text).exists():
            raise RegistryError(f"{text} already exists")
        if postgres and artifacts_dir is None:
            raise RegistryError("a PostgreSQL registry needs an artifacts directory")
        loaded = resolve_pack(pack)
        if not postgres:
            Path(text).parent.mkdir(parents=True, exist_ok=True)
        try:
            db = connect(text)
        except BackendError as exc:
            raise RegistryError(str(exc)) from exc
        if postgres and db.query_one(
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'meta'"
        ):
            db.close()
            raise RegistryError(f"{_describe(text)} already contains a registry")
        db.create_schema()
        pack_setting = str(Path(pack).resolve()) if Path(pack).is_dir() else pack
        settings = [
            ("schema_version", str(SCHEMA_VERSION)),
            ("pack", pack_setting),
            ("pack_ref", loaded.manifest.ref),
            ("created_at", _now()),
        ]
        if artifacts_dir is not None:
            settings.append(("artifacts_dir", str(Path(artifacts_dir).resolve())))
        with db.transaction():
            for key, value in settings:
                db.run("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
        registry = cls(text, db)
        registry._pack = loaded
        return registry

    @classmethod
    def open(cls, location: str | Path) -> Registry:
        text = str(location)
        if not is_postgres_url(text) and not Path(text).is_file():
            raise RegistryError(f"no registry at {text}; create one with `aimrg init`")
        try:
            db = connect(text)
        except BackendError as exc:
            raise RegistryError(str(exc)) from exc
        incompatible = RegistryError(f"{_describe(text)} is not a compatible AI-MRG registry")
        try:
            row = db.query_one("SELECT value FROM meta WHERE key = 'schema_version'")
        except BackendError as exc:
            db.close()
            raise incompatible from exc
        if row is None or not 1 <= int(row[0]) <= SCHEMA_VERSION:
            db.close()
            raise incompatible
        if int(row[0]) < SCHEMA_VERSION:
            cls._migrate(db)
        return cls(text, db)

    @staticmethod
    def _migrate(db: Database) -> None:
        """Upgrade an older (SQLite) registry in place. All migrations are additive."""
        db.create_schema()
        if isinstance(db, SQLiteDatabase):
            columns = {r[1] for r in db.query("PRAGMA table_info(events)")}
            if "seq" not in columns:
                db.run("ALTER TABLE events ADD COLUMN seq INTEGER NOT NULL DEFAULT 0")
            db.script(
                "DROP TRIGGER IF EXISTS events_no_update;"
                "UPDATE events SET seq = (SELECT COUNT(*) FROM events e2 "
                "WHERE e2.asset_id = events.asset_id AND e2.rowid <= events.rowid);"
            )
            db.create_schema()  # restore the trigger
        db.run("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(SCHEMA_VERSION),))

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Registry:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def backend(self) -> str:
        return self._db.kind

    def meta(self, key: str) -> str | None:
        row = self._db.query_one("SELECT value FROM meta WHERE key = ?", (key,))
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
        configured = self.meta("artifacts_dir")
        if configured:
            return Path(configured)
        path = Path(self.location)
        return path.parent / f"{path.stem}-artifacts"

    # --- notifications ----------------------------------------------------------

    def subscribe(self, listener: Callable[[str, dict[str, Any]], None]) -> None:
        """Call ``listener(event, payload)`` after each notifiable change."""
        self._hooks.listeners.append(listener)

    @property
    def deliveries(self) -> list[Delivery]:
        """Webhook deliveries attempted through this registry object."""
        return list(self._hooks.deliveries)

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        body = {"pack": self.pack.manifest.ref, **payload}
        for listener in self._hooks.listeners:
            listener(event, body)
        for webhook in self.webhooks():
            if webhook.wants(event):
                self._hooks.deliveries.append(deliver(webhook, event, body))

    # --- assets -----------------------------------------------------------------

    def register(self, asset: AssetRecord) -> AssetRecord:
        unknown = [dep for dep in asset.depends_on if self._find(dep) is None]
        if unknown:
            raise RegistryError(f"unknown upstream asset(s): {unknown}")
        try:
            self._db.run(
                "INSERT INTO assets (id, name, data, updated_at) VALUES (?, ?, ?, ?)",
                (asset.id, asset.name, asset.model_dump_json(), _now()),
            )
        except IntegrityViolation as exc:
            raise RegistryError(f"an asset named {asset.name!r} or id {asset.id} exists") from exc
        return asset

    def _find(self, ref: str) -> AssetRecord | None:
        row = self._db.query_one("SELECT data FROM assets WHERE id = ? OR name = ?", (ref, ref))
        return None if row is None else AssetRecord.model_validate_json(row[0])

    def get(self, ref: str) -> AssetRecord:
        """Look up an asset by id or exact name."""
        asset = self._find(ref)
        if asset is None:
            raise RegistryError(f"no asset with id or name {ref!r}")
        return asset

    def assets(self) -> list[AssetRecord]:
        rows = self._db.query("SELECT data FROM assets ORDER BY name")
        return [AssetRecord.model_validate_json(r[0]) for r in rows]

    def save(self, asset: AssetRecord) -> AssetRecord:
        """Persist changes to an existing asset (re-validated before saving)."""
        validated = AssetRecord.model_validate(asset.model_dump())
        current = self._find(validated.id)
        if current is not None and current.status != validated.status:
            raise RegistryError(
                "status changes go through the workflow (aimrg.workflow.transition), not save()"
            )
        try:
            count = self._db.run(
                "UPDATE assets SET name = ?, data = ?, updated_at = ? WHERE id = ?",
                (validated.name, validated.model_dump_json(), _now(), validated.id),
            )
        except IntegrityViolation as exc:
            raise RegistryError(f"an asset named {validated.name!r} exists") from exc
        if count == 0:
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
        relative = uri.removeprefix(ARTIFACT_SCHEME)
        if ".." in Path(relative).parts or Path(relative).is_absolute():
            raise RegistryError(f"invalid artifact uri: {uri}")
        return self.artifacts_dir / relative

    def read_artifact(self, uri: str) -> bytes:
        return self.artifact_path(uri).read_bytes()

    # --- evidence ---------------------------------------------------------------

    def add_evidence(self, item: EvidenceItem) -> EvidenceItem:
        """Append evidence, linking it to the previous item for the same asset."""
        asset = self.get(item.asset_id)
        if item.control_id not in {c.id for c in self.pack.controls}:
            raise RegistryError(f"unknown control {item.control_id!r} in {self.pack.manifest.ref}")
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT seq, record_hash FROM evidence WHERE asset_id = ? "
                "ORDER BY seq DESC LIMIT 1",
                (asset.id,),
            )
            seq = 1 if row is None else int(row[0]) + 1
            linked = item.model_copy(
                update={"asset_id": asset.id, "prev_hash": None if row is None else row[1]}
            )
            linked = EvidenceItem.model_validate(linked.model_dump())
            self._db.run(
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
        if linked.type is EvidenceType.CHECK_RESULT and linked.outcome is CheckOutcome.FAIL:
            self._emit(
                "check.failed",
                {
                    "asset": {"id": asset.id, "name": asset.name},
                    "evidence": linked.model_dump(mode="json"),
                },
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
        return [EvidenceItem.model_validate_json(r[0]) for r in self._db.query(query, params)]

    def verify(self) -> AuditReport:
        """Recompute every evidence chain and check stored artifacts are unchanged."""
        problems: list[str] = []
        assets = self.assets()
        checked = 0
        for asset in assets:
            rows = self._db.query(
                "SELECT data, record_hash FROM evidence WHERE asset_id = ? ORDER BY seq",
                (asset.id,),
            )
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
        self._db.run(
            "INSERT INTO findings (id, asset_id, data) VALUES (?, ?, ?)",
            (stored.id, asset.id, stored.model_dump_json()),
        )
        self._emit(
            "finding.opened",
            {
                "asset": {"id": asset.id, "name": asset.name},
                "finding": stored.model_dump(mode="json"),
            },
        )
        return stored

    def save_finding(self, finding: Finding) -> Finding:
        validated = Finding.model_validate(finding.model_dump())
        row = self._db.query_one("SELECT data FROM findings WHERE id = ?", (validated.id,))
        if row is None:
            raise RegistryError(f"finding {validated.id} does not exist")
        previous = Finding.model_validate_json(row[0])
        self._db.run(
            "UPDATE findings SET data = ? WHERE id = ?",
            (validated.model_dump_json(), validated.id),
        )
        if previous.status not in _RESOLVED and validated.status in _RESOLVED:
            asset = self.get(validated.asset_id)
            self._emit(
                "finding.resolved",
                {
                    "asset": {"id": asset.id, "name": asset.name},
                    "finding": validated.model_dump(mode="json"),
                },
            )
        return validated

    def get_finding(self, finding_id: str) -> Finding:
        rows = self._db.query(
            "SELECT data FROM findings WHERE id = ? OR id LIKE ?",
            (finding_id, f"{finding_id}%"),
        )
        if len(rows) != 1:
            raise RegistryError(f"no unique finding matching {finding_id!r}")
        return Finding.model_validate_json(rows[0][0])

    def findings(self, ref: str | None = None) -> list[Finding]:
        if ref is None:
            rows = self._db.query("SELECT data FROM findings")
        else:
            rows = self._db.query(
                "SELECT data FROM findings WHERE asset_id = ?", (self.get(ref).id,)
            )
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
        with self._db.transaction():
            row = self._db.query_one(
                "SELECT COALESCE(MAX(seq), 0) FROM events WHERE asset_id = ?", (asset.id,)
            )
            seq = int(row[0]) + 1 if row else 1
            self._db.run(
                "INSERT INTO events (id, asset_id, occurred_at, data, seq) VALUES (?, ?, ?, ?, ?)",
                (
                    stored.id,
                    asset.id,
                    stored.occurred_at.isoformat(),
                    stored.model_dump_json(),
                    seq,
                ),
            )
            self._db.run(
                "UPDATE assets SET data = ?, updated_at = ? WHERE id = ?",
                (updated.model_dump_json(), _now(), asset.id),
            )
        self._emit(
            "lifecycle.transition",
            {
                "asset": {"id": asset.id, "name": asset.name},
                "event": stored.model_dump(mode="json"),
            },
        )
        return stored

    def events(self, ref: str) -> list[LifecycleEvent]:
        rows = self._db.query(
            "SELECT data FROM events WHERE asset_id = ? ORDER BY seq, occurred_at",
            (self.get(ref).id,),
        )
        return [LifecycleEvent.model_validate_json(r[0]) for r in rows]

    # --- users ------------------------------------------------------------------

    def add_user(self, name: str, roles: list[Role | str]) -> tuple[User, str]:
        """Create an API user. Returns the user and its token (shown once, not stored)."""
        user = User(name=name, roles=[Role(r) for r in roles])
        token = new_token()
        try:
            self._db.run(
                "INSERT INTO users (id, name, token_hash, data) VALUES (?, ?, ?, ?)",
                (user.id, user.name, hash_token(token), user.model_dump_json()),
            )
        except IntegrityViolation as exc:
            raise RegistryError(f"a user named {name!r} exists") from exc
        return user, token

    def users(self) -> list[User]:
        rows = self._db.query("SELECT data FROM users ORDER BY name")
        return [User.model_validate_json(r[0]) for r in rows]

    def user_for_token(self, token: str) -> User | None:
        row = self._db.query_one(
            "SELECT data FROM users WHERE token_hash = ?", (hash_token(token),)
        )
        if row is None:
            return None
        user = User.model_validate_json(row[0])
        return user if user.active else None

    def _get_user(self, name: str) -> User:
        row = self._db.query_one("SELECT data FROM users WHERE name = ?", (name,))
        if row is None:
            raise RegistryError(f"no user named {name!r}")
        return User.model_validate_json(row[0])

    def set_user_active(self, name: str, active: bool) -> User:
        user = self._get_user(name).model_copy(update={"active": active})
        self._db.run("UPDATE users SET data = ? WHERE id = ?", (user.model_dump_json(), user.id))
        return user

    def rotate_token(self, name: str) -> str:
        user = self._get_user(name)
        token = new_token()
        self._db.run("UPDATE users SET token_hash = ? WHERE id = ?", (hash_token(token), user.id))
        return token

    # --- webhooks ---------------------------------------------------------------

    def add_webhook(self, webhook: Webhook) -> Webhook:
        self._db.run(
            "INSERT INTO webhooks (id, data) VALUES (?, ?)", (webhook.id, webhook.model_dump_json())
        )
        return webhook

    def webhooks(self) -> list[Webhook]:
        rows = self._db.query("SELECT data FROM webhooks")
        return sorted((Webhook.model_validate_json(r[0]) for r in rows), key=lambda w: w.created_at)

    def remove_webhook(self, webhook_id: str) -> None:
        matches = [w for w in self.webhooks() if w.id.startswith(webhook_id)]
        if len(matches) != 1:
            raise RegistryError(f"no unique webhook matching {webhook_id!r}")
        self._db.run("DELETE FROM webhooks WHERE id = ?", (matches[0].id,))


def _now() -> str:
    return datetime.now(UTC).isoformat()
