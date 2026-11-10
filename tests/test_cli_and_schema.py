from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import yaml
from click.testing import CliRunner

from aimrg import __version__
from aimrg.cli import main
from aimrg.schema import SCHEMAS, export_schemas, render_schemas

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_version() -> None:
    result = CliRunner().invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_pack_list() -> None:
    result = CliRunner().invoke(main, ["pack", "list"])
    assert result.exit_code == 0
    assert "fin-us-banking@" in result.output
    assert "NOT YET REVIEWED" in result.output


def test_pack_validate_by_name_and_path(fin_pack_dir: Path) -> None:
    runner = CliRunner()
    by_name = runner.invoke(main, ["pack", "validate", "fin-us-banking"])
    by_path = runner.invoke(main, ["pack", "validate", str(fin_pack_dir)])
    assert by_name.exit_code == 0, by_name.output
    assert by_path.exit_code == 0
    assert "OK  fin-us-banking@" in by_name.output
    assert "controls" in by_name.output


def test_pack_validate_reports_errors(pack_copy: Path) -> None:
    (pack_copy / "sources.yaml").unlink()
    result = CliRunner().invoke(main, ["pack", "validate", str(pack_copy)])
    assert result.exit_code == 1
    assert "sources.yaml: file not found" in result.output


def test_schema_export_and_check(tmp_path: Path) -> None:
    runner = CliRunner()
    out = tmp_path / "schemas"
    stale = runner.invoke(main, ["schema", "export", str(out), "--check"])
    assert stale.exit_code == 1
    written = runner.invoke(main, ["schema", "export", str(out)])
    assert written.exit_code == 0
    assert len(list(out.glob("*.json"))) == len(SCHEMAS)
    fresh = runner.invoke(main, ["schema", "export", str(out), "--check"])
    assert fresh.exit_code == 0
    assert "up to date" in fresh.output


def test_committed_schemas_are_current() -> None:
    committed = REPO_ROOT / "schemas"
    for name, text in render_schemas().items():
        assert (committed / name).read_text(encoding="utf-8") == text, (
            f"schemas/{name} is stale; run `aimrg schema export schemas`"
        )


def test_pack_yaml_files_validate_against_published_schemas(
    tmp_path: Path, fin_pack_dir: Path
) -> None:
    """Editors use the JSON Schemas; make sure the shipped YAML passes them."""
    export_schemas(tmp_path)

    def schema(name: str) -> dict:  # type: ignore[type-arg]
        return json.loads((tmp_path / name).read_text(encoding="utf-8"))

    pairs = [
        (fin_pack_dir / "pack.yaml", "pack-manifest.schema.json"),
        (fin_pack_dir / "sources.yaml", "pack-sources.schema.json"),
        *[(p, "pack-controls.schema.json") for p in (fin_pack_dir / "controls").glob("*.yaml")],
        *[(p, "pack-tiering-profile.schema.json") for p in (fin_pack_dir / "tiering").glob("*")],
        *[(p, "pack-thresholds.schema.json") for p in (fin_pack_dir / "thresholds").glob("*")],
    ]
    for path, schema_name in pairs:
        instance = json.loads(
            json.dumps(yaml.safe_load(path.read_text(encoding="utf-8")), default=str)
        )
        jsonschema.validate(instance, schema(schema_name))
