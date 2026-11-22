"""``--json`` output for ``aimrg list``, ``aimrg plan`` and ``aimrg finding list`` (issue #14)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from click.testing import CliRunner, Result

from aimrg.cli import main

ASSET = "Card PD"


@pytest.fixture
def cli(tmp_path: Path, portfolio: dict[str, pd.DataFrame], monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.chdir(tmp_path)
    for role, frame in portfolio.items():
        frame.to_csv(tmp_path / f"{role}.csv", index=False)
    runner = CliRunner()

    def invoke(*args: str, input: str | None = None) -> Result:
        return runner.invoke(
            main,
            ["--db", "gov.db", "--actor", "tester", *args],
            input=input,
            catch_exceptions=False,
        )

    return invoke


def _json(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _register_tier1_model(cli) -> None:  # type: ignore[no-untyped-def]
    args = ["--owner", "risk", "--use", "credit_decision", "--category", "ml_non_generative"]
    assert cli("register", ASSET, *args).exit_code == 0
    assert cli("classify", ASSET, input="y\ny\nn\nUS\nPD model\n").exit_code == 0
    tier = cli("tier", ASSET, "--score", "exposure=5", "--score", "purpose=5", input="3\n3\n2\n")
    assert tier.exit_code == 0


def _setup_asset(cli) -> None:  # type: ignore[no-untyped-def]
    assert cli("init").exit_code == 0
    _register_tier1_model(cli)


def test_list_json(cli) -> None:  # type: ignore[no-untyped-def]
    assert cli("init").exit_code == 0
    assert _json(cli("list", "--json")) == []  # empty registry is still valid JSON
    _register_tier1_model(cli)

    rows = _json(cli("list", "--json"))
    assert len(rows) == 1
    row = rows[0]
    assert set(row) == {"id", "name", "owner", "track", "tier", "status", "open_findings"}
    assert row["name"] == ASSET and row["owner"] == "risk"
    assert row["track"] == "model" and row["tier"] == 1
    assert row["open_findings"] == 0
    # the human table is unchanged without the flag
    assert "Open findings" in cli("list").output


def test_plan_json(cli) -> None:  # type: ignore[no-untyped-def]
    _setup_asset(cli)
    rows = _json(cli("plan", ASSET, "--stage", "validation", "--json"))
    assert rows, "a tier-1 model has validation controls"
    by_id = {r["control"]: r for r in rows}
    val = by_id["FIN-VAL-003"]
    assert val["stage"] == "validation"
    assert val["status"] == "missing"
    assert isinstance(val["missing"], list) and val["missing"]
    assert all({"id", "available"} == set(c) for c in val["checks"])
    assert all(r["stage"] == "validation" for r in rows)
    # same controls as the table
    table = cli("plan", ASSET, "--stage", "validation").output
    assert all(r["control"] in table for r in rows)
    assert cli("plan", "nope", "--json").exit_code != 0


def test_finding_list_json(cli) -> None:  # type: ignore[no-untyped-def]
    _setup_asset(cli)
    assert _json(cli("finding", "list", "--json")) == []
    opened = cli(
        "finding", "open", ASSET, "--title", "Doc gap", "--severity", "low", "--due", "2027-01-31"
    )
    assert opened.exit_code == 0

    rows = _json(cli("finding", "list", "--json"))
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Doc gap" and row["severity"] == "low"
    assert row["status"] == "open" and row["due_date"] == "2027-01-31"
    assert row["asset"] == ASSET
    assert len(row["id"]) > 8  # full id, not the shortened one in the table
    assert _json(cli("finding", "list", ASSET, "--json")) == rows
