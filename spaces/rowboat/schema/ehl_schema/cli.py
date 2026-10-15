"""Command line: python -m ehl_schema.cli build|lint|migrate|map --workdir <Rowboat data folder>."""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .build import build
from .lint import errors, lint
from .migrate import apply, diff, plan
from .model import DEFAULT_SCHEMA, SchemaError, load_schema
from .profile import Profile, default_profile, load_profile
from .vaultmap import run_map

log = logging.getLogger("ehl_schema")


def _profile(args: argparse.Namespace, schema) -> Profile:
    if getattr(args, "profile", None):
        return load_profile(args.profile, schema)
    return default_profile(getattr(args, "company", None) or "Company")


def _map_once(schema, profile: Profile, workdir: Path) -> None:
    result = run_map(schema, profile, workdir)
    for issue in result.issues:
        log.info("%s %s [%s] %s", issue.severity.upper(), issue.path, issue.code, issue.message)
    log.info("map: %d note(s) placed, %d excluded, %d structure note(s) written, vault-map.json %s",
             result.notes, result.excluded, result.structure, "updated" if result.written else "unchanged")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser(prog="ehl-schema", description="Vault schema: build, lint, migrate, map")
    ap.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA, help="schema file (default: schema/vault-schema.yaml)")
    sub = ap.add_subparsers(dest="command", required=True)
    for name, text in (("build", "write structure notes, rules note and notes.json"),
                       ("lint", "check every note"), ("migrate", "add missing front matter (dry run by default)"),
                       ("map", "write structure notes and config/vault-map.json (never edits existing notes)")):
        p = sub.add_parser(name, help=text)
        p.add_argument("--workdir", type=Path, required=True, help="Rowboat data folder, e.g. ~/.rowboat-ehl")
        if name != "migrate":
            p.add_argument("--profile", help="vault profile: a name in schema/profiles or a .yaml path")
    for name in ("build", "map"):
        sub.choices[name].add_argument("--company", default=None,
                                       help="title of the L0 note (default: from the profile, else 'Company')")
    sub.choices["migrate"].add_argument("--write", action="store_true", help="write the changes")
    sub.choices["map"].add_argument("--watch", type=float, default=0,
                                    help="repeat every N seconds until Ctrl+C (0 = once)")
    args = ap.parse_args(argv)

    schema = load_schema(args.schema)
    workdir = args.workdir.expanduser()
    vault = workdir / "knowledge"
    if not vault.is_dir():
        log.error("no vault at %s", vault)
        return 2
    try:
        profile = _profile(args, schema) if args.command != "migrate" else None
    except SchemaError as err:
        log.error("%s", err)
        return 2
    if args.command == "build":
        result = build(schema, workdir, profile.company, profile if args.profile else None)
        log.info("build: %d structure note(s) written, notes.json %s", result["notes"], result["notes_json"])
        return 0
    if args.command == "map":
        while True:
            try:
                _map_once(schema, profile, workdir)
            except OSError as err:  # a writer replaced a file while we read it; the next run catches up
                log.warning("map: %s", err)
            if not args.watch:
                return 0
            try:
                time.sleep(args.watch)
            except KeyboardInterrupt:
                return 0
    if args.command == "lint":
        issues = lint(schema, vault, profile if args.profile else None)
        for issue in issues:
            log.info("%s %s [%s] %s", issue.severity.upper(), issue.path, issue.code, issue.message)
        n_errors = len(errors(issues))
        log.info("lint: %d error(s), %d warning(s)", n_errors, len(issues) - n_errors)
        return 1 if n_errors else 0
    report = plan(schema, vault)
    if args.write:
        log.info("migrate: wrote %d note(s)", apply(report, vault))
    else:
        if report.changes:
            log.info("%s", diff(report))
        log.info("migrate (dry run): %d note(s) would change; add --write to apply", len(report.changes))
    for path in report.unmapped:
        log.warning("unmapped (folder not in the schema): %s", path)
    for path in report.unresolved:
        log.warning("unresolved (no parent found, no default area): %s", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
