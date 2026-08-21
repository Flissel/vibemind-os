"""Einsprungpunkt. In diesem Plan gibt es nur das Unterkommando `plan`."""

import argparse
import sys
from datetime import date
from pathlib import Path

from djset.agents.base import AgentClient, AgentError, Budget
from djset.agents.profiler import build_profile
from djset.agents.prompt_writer import render_markdown, write_prompts
from djset.run import Run

PLAN_BUDGET_TOKENS = 120_000


def cmd_plan(
    base: Path,
    genre: str,
    length_minutes: float,
    client: AgentClient,
    today: date | None = None,
) -> int:
    # Ruling 13: argparse laesst "--length -5" und ein leeres "--genre"
    # anstandslos durch. Beides wird hier abgefangen, bevor es als rohe
    # pydantic-ValidationError aus build_profile herausfaellt.
    if not genre.strip():
        print("Fehler: --genre darf nicht leer sein.", file=sys.stderr)
        return 1
    if length_minutes <= 0:
        print(
            f"Fehler: --length muss positiv sein, war {length_minutes}.",
            file=sys.stderr,
        )
        return 1

    budget = Budget(max_tokens=PLAN_BUDGET_TOKENS)
    run = Run.create(base, genre, today=today or date.today())

    try:
        profile = build_profile(client, genre, length_minutes, budget)
        run.write_model("profile.json", profile)

        prompt_set = write_prompts(client, profile, budget)
        run.write_text("prompts.md", render_markdown(profile, prompt_set))
    except AgentError as error:
        # Schemaverletzung nach allen Versuchen oder Budget erschoepft - beides
        # ist ein bekannter, benannter Fehlerfall, kein Programmierfehler. Eine
        # rohe ValidationError von tiefer unten bleibt bewusst unbehandelt.
        print(f"Fehler beim Erzeugen des Plans: {error}", file=sys.stderr)
        return 1

    print(f"Run: {run.run_id}")
    print(f"Profil:  {run.path('profile.json')}")
    print(f"Prompts: {run.path('prompts.md')}")
    print(
        f"\nGeneriere die {profile.track_count.request} Tracks bei Suno und "
        f"lege die Downloads in {base / 'watch'} ab."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dj-set")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Genre-Profil und Suno-Prompts erzeugen")
    plan.add_argument("--genre", required=True, help="frei waehlbar, z.B. 'liquid dnb'")
    plan.add_argument("--length", type=float, required=True, help="Ziellaenge in Minuten")
    plan.add_argument("--base", type=Path, default=Path("."), help="Projektwurzel")

    args = parser.parse_args(argv)
    if args.command == "plan":
        from djset.agents.client import ClaudeClient

        return cmd_plan(
            base=args.base,
            genre=args.genre,
            length_minutes=args.length,
            client=ClaudeClient(),
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
