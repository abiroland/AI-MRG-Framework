from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aimrg.api import create_app, max_upload_bytes
from aimrg.registry import Registry
from aimrg.registry.accounts import Role

FIN_PACK = Path(__file__).resolve().parents[1] / "packs" / "fin-us-banking"


@pytest.fixture
def small(tmp_path: Path):  # type: ignore[no-untyped-def]
    reg = Registry.create(tmp_path / "gov.db", pack=str(FIN_PACK))
    token = reg.add_user("dana", [Role.DEVELOPER])[1]
    reg.close()
    client = TestClient(create_app(str(tmp_path / "gov.db"), max_upload_mb=0.01))
    return client, {"Authorization": f"Bearer {token}"}


def test_oversized_upload_rejected(small) -> None:  # type: ignore[no-untyped-def]
    client, headers = small
    r = client.post(
        "/assets/X/evidence",
        headers=headers,
        data={"control_id": "FIN-DEV-001", "type": "document", "summary": "big"},
        files={"file": ("big.bin", b"x" * 20_000, "application/octet-stream")},
    )
    assert r.status_code == 413
    assert "limit" in r.json()["detail"]


def test_chunked_body_is_counted() -> None:
    from starlette.applications import Starlette
    from starlette.requests import Request
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route

    from aimrg.api import BodyLimitMiddleware

    async def echo(request: Request) -> PlainTextResponse:
        return PlainTextResponse(str(len(await request.body())))

    app = BodyLimitMiddleware(Starlette(routes=[Route("/", echo, methods=["POST"])]), 5_000)
    client = TestClient(app)

    def chunks(n: int):  # type: ignore[no-untyped-def]
        for _ in range(n):
            yield b"y" * 1_000

    assert client.post("/", content=chunks(10)).status_code == 413
    ok = client.post("/", content=chunks(3))
    assert ok.status_code == 200 and ok.text == "3000"


def test_small_requests_pass_with_security_headers(small) -> None:  # type: ignore[no-untyped-def]
    client, headers = small
    r = client.get("/assets", headers=headers)
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


def test_limit_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIMRG_MAX_UPLOAD_MB", "2")
    assert max_upload_bytes() == 2 * 1024 * 1024
    monkeypatch.setenv("AIMRG_MAX_UPLOAD_MB", "lots")
    with pytest.raises(ValueError, match="must be a number"):
        max_upload_bytes()
    monkeypatch.delenv("AIMRG_MAX_UPLOAD_MB")
    assert max_upload_bytes() == 50 * 1024 * 1024
    with pytest.raises(ValueError, match="positive"):
        max_upload_bytes(0)


def test_artifact_suffix_is_sanitized(tmp_path: Path) -> None:
    reg = Registry.create(tmp_path / "gov.db", pack=str(FIN_PACK))
    try:
        for bad in ("../tmp/x", "./evil", ".a/b", ".tar.gz", ". pdf", "." + "x" * 40):
            uri, _ = reg.store_artifact(b"data-" + bad.encode(), suffix=bad)
            path = reg.artifact_path(uri)
            assert path.parent.parent == reg.artifacts_dir and "." not in path.name
        uri, _ = reg.store_artifact(b"pdf", suffix=".pdf")
        assert uri.endswith(".pdf")
    finally:
        reg.close()


def test_report_templates_are_sandboxed(tmp_path: Path) -> None:
    import shutil

    from jinja2.exceptions import SecurityError

    from aimrg.reports import render

    pack = tmp_path / "pack"
    shutil.copytree(FIN_PACK, pack)
    (pack / "templates" / "inventory_report.md.j2").write_text(
        "{{ ''.__class__.__mro__[1].__subclasses__() }}", encoding="utf-8"
    )
    reg = Registry.create(tmp_path / "gov.db", pack=str(pack))
    try:
        with pytest.raises(SecurityError):
            render(reg, "inventory_report")
    finally:
        reg.close()
