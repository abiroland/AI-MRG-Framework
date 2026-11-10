"""A small client for the AI-MRG REST API, used by the dashboard.

The dashboard talks to the API rather than to the database, so role checks and the
workflow's independence rules apply to every action taken in the UI (ADR-0004).
"""

from __future__ import annotations

from typing import Any

import httpx


class ApiError(Exception):
    def __init__(self, status: int, detail: Any) -> None:
        self.status = status
        self.detail = detail
        if isinstance(detail, dict):
            blockers = "".join(f"\n- {b}" for b in detail.get("blockers", []))
            message = f"{detail.get('message', detail)}{blockers}"
        else:
            message = str(detail)
        super().__init__(message)


class ApiClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        token: str = "",
        http: httpx.Client | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._http = http or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = {"Authorization": f"Bearer {token}"} if token else {}

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        headers = {**self._headers, **kwargs.pop("headers", {})}
        response = self._http.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise ApiError(response.status_code, detail)
        return response

    def _json(self, method: str, path: str, **kwargs: Any) -> Any:
        return self._request(method, path, **kwargs).json()

    # --- reads ----------------------------------------------------------------------

    def health(self) -> dict[str, Any]:
        return dict(self._json("GET", "/health"))

    def me(self) -> dict[str, Any]:
        return dict(self._json("GET", "/me"))

    def assets(self) -> list[dict[str, Any]]:
        return list(self._json("GET", "/assets"))

    def asset(self, ref: str) -> dict[str, Any]:
        return dict(self._json("GET", f"/assets/{ref}"))

    def plan(self, ref: str, stage: str | None = None) -> list[dict[str, Any]]:
        params = {"stage": stage} if stage else None
        return list(self._json("GET", f"/assets/{ref}/plan", params=params))

    def evidence(self, ref: str) -> list[dict[str, Any]]:
        return list(self._json("GET", f"/assets/{ref}/evidence"))

    def check_results(self, ref: str, check_id: str | None = None) -> list[dict[str, Any]]:
        params = {"check_id": check_id} if check_id else None
        return list(self._json("GET", f"/assets/{ref}/check-results", params=params))

    def findings(self, asset: str | None = None, open_only: bool = True) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"open_only": open_only}
        if asset:
            params["asset"] = asset
        return list(self._json("GET", "/findings", params=params))

    def aging(self) -> list[dict[str, Any]]:
        return list(self._json("GET", "/findings/aging"))

    def status(self, ref: str) -> dict[str, Any]:
        return dict(self._json("GET", f"/assets/{ref}/status"))

    def templates(self) -> dict[str, list[str]]:
        return dict(self._json("GET", "/templates"))

    def report(self, template: str, asset: str | None = None, fmt: str = "md") -> bytes:
        path = f"/assets/{asset}/reports/{template}" if asset else f"/reports/{template}"
        return self._request("GET", path, params={"format": fmt}).content

    def export(self, assets: list[str] | None = None) -> tuple[bytes, str]:
        response = self._request("GET", "/export", params={"asset": assets} if assets else None)
        return response.content, response.headers.get("X-AIMRG-Sums-SHA256", "")

    # --- writes ---------------------------------------------------------------------

    def transition(
        self, ref: str, status: str, limits: str | None = None, note: str = ""
    ) -> dict[str, Any]:
        body = {"status": status, "limits": limits or None, "note": note}
        return dict(self._json("POST", f"/assets/{ref}/transitions", json=body))

    def add_evidence(
        self,
        ref: str,
        control_id: str,
        kind: str,
        summary: str,
        file: tuple[str, bytes] | None = None,
    ) -> dict[str, Any]:
        files = {"file": (file[0], file[1])} if file else None
        data = {"control_id": control_id, "type": kind, "summary": summary}
        return dict(self._json("POST", f"/assets/{ref}/evidence", data=data, files=files))

    def open_finding(self, ref: str, **body: Any) -> dict[str, Any]:
        return dict(self._json("POST", f"/assets/{ref}/findings", json=body))

    def close_finding(self, finding_id: str, evidence_ids: list[str]) -> dict[str, Any]:
        return dict(
            self._json("POST", f"/findings/{finding_id}/close", json={"evidence_ids": evidence_ids})
        )

    def accept_finding(self, finding_id: str) -> dict[str, Any]:
        return dict(self._json("POST", f"/findings/{finding_id}/accept"))
