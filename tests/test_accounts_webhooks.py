from __future__ import annotations

import hashlib
import hmac
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from click.testing import CliRunner
from conftest import make_model_asset
from pydantic import ValidationError

from aimrg.cli import main
from aimrg.core.models import Finding, FindingStatus, Severity
from aimrg.registry import Registry, RegistryError
from aimrg.registry.accounts import Role, User, Webhook, deliver, hash_token, sign
from aimrg.workflow import transition


class _Receiver:
    def __init__(self) -> None:
        self.requests: list[tuple[dict[str, str], bytes]] = []
        self.status = 200
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers["Content-Length"]))
                receiver.requests.append((self.headers, body))  # type: ignore[arg-type]
                self.send_response(receiver.status)
                self.end_headers()

            def log_message(self, *args: object) -> None:
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def events(self) -> list[str]:
        return [json.loads(body)["event"] for _, body in self.requests]


@pytest.fixture
def receiver() -> Iterator[_Receiver]:
    r = _Receiver()
    yield r
    r.server.shutdown()
    r.server.server_close()


def test_users_and_tokens(registry: Registry) -> None:
    user, token = registry.add_user("alice", ["validator", Role.APPROVER])
    assert token.startswith("aimrg_") and hash_token(token) != token
    assert registry.user_for_token(token) == user
    assert registry.user_for_token("wrong") is None
    with pytest.raises(RegistryError, match="exists"):
        registry.add_user("alice", ["viewer"])
    new = registry.rotate_token("alice")
    assert registry.user_for_token(token) is None and registry.user_for_token(new)
    registry.set_user_active("alice", False)
    assert registry.user_for_token(new) is None
    with pytest.raises(RegistryError, match="no user"):
        registry.rotate_token("bob")
    assert [u.name for u in registry.users()] == ["alice"]
    assert User(name="x", roles=["admin"]).has(Role.VALIDATOR)
    assert not User(name="x", roles=["viewer"]).has(Role.VALIDATOR)


def test_webhook_model_validation() -> None:
    with pytest.raises(ValidationError, match="http"):
        Webhook(url="ftp://x")
    with pytest.raises(ValidationError, match="unknown event"):
        Webhook(url="https://x", events=["nope"])
    hook = Webhook(url="https://x", events=["finding.opened"])
    assert hook.wants("finding.opened") and not hook.wants("check.failed")
    assert Webhook(url="https://x").wants("anything")


def test_webhooks_fire_with_signatures(registry: Registry, receiver: _Receiver, portfolio) -> None:  # type: ignore[no-untyped-def]
    hook = registry.add_webhook(Webhook(url=receiver.url))
    registry.add_webhook(Webhook(url=receiver.url, events=["lifecycle.transition"], active=False))
    seen: list[str] = []
    registry.subscribe(lambda event, payload: seen.append(event))

    asset = make_model_asset(registry)
    finding = registry.add_finding(
        Finding(asset_id=asset.id, severity=Severity.HIGH, title="gap", owner="o")
    )
    registry.save_finding(finding.model_copy(update={"title": "renamed"}))  # not a resolution
    registry.save_finding(
        finding.model_copy(
            update={"status": FindingStatus.RISK_ACCEPTED, "risk_acceptance_by": "c"}
        )
    )
    transition(registry, asset.id, "development", actor="dev")

    from aimrg.checks import run_control_checks
    from aimrg.datasets import SCORE, TARGET

    run_control_checks(
        registry,
        asset.id,
        "FIN-MON-002",
        {"current": portfolio["current"]},
        actor="v",
        target=TARGET,
        score=SCORE,
    )
    expected = [
        "finding.opened",
        "finding.resolved",
        "lifecycle.transition",
        "check.failed",
        "finding.opened",
    ]
    assert receiver.events() == expected
    assert seen == expected
    headers, body = receiver.requests[0]
    assert headers["X-AIMRG-Event"] == "finding.opened"
    expected_sig = "sha256=" + hmac.new(hook.secret.encode(), body, hashlib.sha256).hexdigest()
    assert headers["X-AIMRG-Signature"] == expected_sig == sign(hook.secret, body)
    payload = json.loads(body)["data"]
    assert payload["asset"]["name"] == "PD Model" and payload["pack"].startswith("fin-us-banking@")
    assert all(d.ok for d in registry.deliveries)

    registry.remove_webhook(hook.id[:8])
    with pytest.raises(RegistryError, match="no unique"):
        registry.remove_webhook("zzzz")


def test_failed_delivery_never_raises(receiver: _Receiver) -> None:
    receiver.status = 500
    result = deliver(Webhook(url=receiver.url), "test", {})
    assert not result.ok and "500" in result.detail
    closed = deliver(Webhook(url="http://127.0.0.1:9/unreachable"), "test", {}, timeout=1)
    assert not closed.ok


def test_users_and_webhooks_cli(tmp_path: Path, receiver: _Receiver, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    def cli(*args: str):  # type: ignore[no-untyped-def]
        return runner.invoke(main, ["--db", "gov.db", *args], catch_exceptions=False)

    assert cli("init").exit_code == 0
    out = cli("user", "add", "alice", "--role", "validator", "--role", "approver")
    assert out.exit_code == 0 and "aimrg_" in out.output
    assert cli("user", "add", "alice", "--role", "viewer").exit_code != 0
    assert "validator, approver" in cli("user", "list").output
    assert "New token" in cli("user", "rotate-token", "alice").output
    assert cli("user", "disable", "alice").exit_code == 0
    assert "no" in cli("user", "list").output
    assert cli("user", "disable", "bob").exit_code != 0
    assert cli("user", "rotate-token", "bob").exit_code != 0

    assert "No webhooks" in cli("webhook", "list").output
    out = cli("webhook", "add", receiver.url, "--event", "finding.opened")
    assert out.exit_code == 0 and "Signing secret" in out.output
    assert cli("webhook", "add", "ftp://bad").exit_code != 0
    hook_id = cli("webhook", "list").output.splitlines()[2].split()[0]
    assert "Delivered" in cli("webhook", "test", hook_id).output
    receiver.status = 500
    assert cli("webhook", "test", hook_id).exit_code != 0
    assert cli("webhook", "test", "zzzz").exit_code != 0
    assert cli("webhook", "remove", hook_id).exit_code == 0
    assert cli("webhook", "remove", hook_id).exit_code != 0


def test_postgres_init_needs_artifacts_dir() -> None:
    with pytest.raises(RegistryError, match="artifacts directory"):
        Registry.create("postgresql://user@localhost:1/none")


def test_artifact_paths_cannot_escape(registry: Registry) -> None:
    with pytest.raises(RegistryError, match="invalid artifact"):
        registry.artifact_path("artifact://../../etc/passwd")
