"""The ``aimrg`` command-line interface."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import click

from aimrg import __version__
from aimrg.packs.loader import (
    PackValidationError,
    builtin_pack_names,
    builtin_packs_dir,
    load_pack,
)
from aimrg.schema import export_schemas, stale_schemas


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.version_option(__version__, prog_name="aimrg")
def main() -> None:
    """AI-MRG: open-source AI and model risk governance toolkit.

    Not legal advice. Using AI-MRG does not by itself establish compliance.
    """


# --- packs --------------------------------------------------------------------


@main.group()
def pack() -> None:
    """Work with content packs."""


@pack.command("list")
def pack_list() -> None:
    """List the packs shipped with AI-MRG."""
    names = builtin_pack_names()
    if not names:
        click.echo("No built-in packs found.")
        return
    for name in names:
        try:
            loaded = load_pack(builtin_packs_dir() / name)
        except PackValidationError:
            click.echo(f"{name}  (invalid; run `aimrg pack validate {name}`)")
            continue
        m = loaded.manifest
        reviewed = "reviewed" if m.reviewed else "NOT YET REVIEWED"
        click.echo(f"{m.ref}  {m.title}  [effective as of {m.effective_as_of}; {reviewed}]")


@pack.command("validate")
@click.argument("target")
def pack_validate(target: str) -> None:
    """Validate a pack. TARGET is a pack directory or the name of a built-in pack."""
    path = Path(target)
    if not path.is_dir() and target in builtin_pack_names():
        path = builtin_packs_dir() / target
    try:
        loaded = load_pack(path)
    except PackValidationError as exc:
        click.secho(str(exc), fg="red", err=True)
        raise SystemExit(1) from exc

    m = loaded.manifest
    stages = Counter(c.lifecycle_stage.value for c in loaded.controls)
    click.secho(f"OK  {m.ref}  ({m.title})", fg="green")
    click.echo(f"  effective as of : {m.effective_as_of}")
    click.echo(f"  reviewed        : {'yes' if m.reviewed else 'no (draft content)'}")
    click.echo(f"  sources         : {len(loaded.sources)}")
    click.echo(f"  controls        : {len(loaded.controls)}")
    for stage, count in sorted(stages.items()):
        click.echo(f"    {stage:<15} {count}")
    click.echo(f"  tiering profiles: {', '.join(p.id for p in loaded.tiering_profiles)}")
    click.echo(f"  thresholds      : {len(loaded.thresholds)}")


# --- schemas ------------------------------------------------------------------


@main.group()
def schema() -> None:
    """Work with the published JSON Schemas."""


@schema.command("export")
@click.argument("out_dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--check", is_flag=True, help="Only check that OUT_DIR is up to date.")
def schema_export(out_dir: Path, check: bool) -> None:
    """Write JSON Schemas for the domain and pack models to OUT_DIR."""
    if check:
        stale = stale_schemas(out_dir)
        if stale:
            click.secho(f"Out of date in {out_dir}: {', '.join(stale)}", fg="red", err=True)
            click.echo(f"Run: aimrg schema export {out_dir}", err=True)
            raise SystemExit(1)
        click.secho(f"Schemas in {out_dir} are up to date.", fg="green")
        return
    for path in export_schemas(out_dir):
        click.echo(f"wrote {path}")


if __name__ == "__main__":  # pragma: no cover
    main()
