"""API users, roles and webhook subscriptions."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

log = logging.getLogger("aimrg.webhooks")

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Role(StrEnum):
    VIEWER = "viewer"
    """Read everything."""
    DEVELOPER = "developer"
    """Register assets, record development evidence, run checks, move toward validation."""
    VALIDATOR = "validator"
    """Classify, tier, record evidence, run checks, open and close findings."""
    APPROVER = "approver"
    """Approve, approve with limits or reject; accept risk on findings."""
    AUDITOR = "auditor"
    """Read everything and verify the audit trail."""
    ADMIN = "admin"
    """Everything."""


class User(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    name: NonEmptyStr
    roles: list[Role] = Field(min_length=1)
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def has(self, *roles: Role) -> bool:
        return Role.ADMIN in self.roles or any(r in self.roles for r in roles)


def new_token() -> str:
    """A new API token. Shown once; only its hash is stored."""
    return f"aimrg_{secrets.token_urlsafe(32)}"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


WEBHOOK_EVENTS = (
    "finding.opened",
    "finding.resolved",
    "check.failed",
    "lifecycle.transition",
)


class Webhook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    url: NonEmptyStr
    events: list[str] = Field(default_factory=lambda: ["*"])
    secret: str = Field(default_factory=lambda: secrets.token_hex(32))
    active: bool = True
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("url")
    @classmethod
    def _http(cls, value: str) -> str:
        if not value.startswith(("http://", "https://")):
            raise ValueError("webhook URL must start with http:// or https://")
        return value

    @field_validator("events")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        unknown = [e for e in value if e != "*" and e not in WEBHOOK_EVENTS]
        if unknown:
            raise ValueError(f"unknown event(s) {unknown}; choose from {list(WEBHOOK_EVENTS)}")
        return value

    def wants(self, event: str) -> bool:
        return self.active and ("*" in self.events or event in self.events)


@dataclass(frozen=True)
class Delivery:
    webhook_id: str
    event: str
    ok: bool
    detail: str


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def deliver(
    webhook: Webhook, event: str, payload: dict[str, Any], timeout: float = 5.0
) -> Delivery:
    """POST a signed JSON notification. Never raises; failures are logged and returned."""
    body = json.dumps(
        {"event": event, "sent_at": datetime.now(UTC).isoformat(), "data": payload},
        default=str,
        sort_keys=True,
    ).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - URL scheme validated on the model
        webhook.url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "aimrg-webhook",
            "X-AIMRG-Event": event,
            "X-AIMRG-Signature": sign(webhook.secret, body),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            status = int(response.status)
        ok = 200 <= status < 300
        detail = f"HTTP {status}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        ok, detail = False, str(exc)
    if not ok:
        log.warning(
            "webhook %s (%s) delivery of %s failed: %s", webhook.id[:8], webhook.url, event, detail
        )
    return Delivery(webhook_id=webhook.id, event=event, ok=ok, detail=detail)
