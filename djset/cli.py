"""Einsprungpunkt mit den Unterkommandos `plan` und `build`."""

import argparse
import sys
from datetime import date
from pathlib import Path

from djset.agents.architect import arrange
from djset.agents.base import AgentClient, AgentError, Budget
from djset.agents.curator import NotEnoughTracks, curate
from djset.agents.designer import design
from djset.agents.profiler import build_profile
from djset.agents.prompt_writer import render_markdown, write_prompts
from djset.analysis import analyse_all
from djset.contracts import MixPlan, Profile, Setlist
from djset.critic import critique
from djset.run import Run, RunNotFound
from djset.watch import NotEnoughMaterial, check_enough, scan

WATCH_DIRNAME = "watch"

PLAN_BUDGET_TOKENS = 120_000
# Wanduhrgrenze der Plan-Phase. Sie besteht aus hoechstens acht
# Modellaufrufen (Profiler plus Prompt-Writer mit seinen Wiederholungen);
# zehn Minuten sind dafuer reichlich und begrenzen trotzdem einen Lauf, der
# in Timeouts und Backoffs haengenbleibt.
PLAN_BUDGET_SECONDS = 600.0

# Budget fuer einen build-Lauf. Deutlich groesser als bei plan: hier laeuft ein
# Modellaufruf pro Track plus vier Stufen. Gilt pro Aufruf, nicht pro Lauf -
# ein resume startet frisch, sonst koennte ein Lauf, der einmal ans Limit
# gestossen ist, nie mehr fertig werden.
BUILD_BUDGET_TOKENS = 600_000
BUILD_BUDGET_SECONDS = 3600.0

# Spec Abschnitt 7: weist der Critic zweimal zurueck, wird der beste bisherige
# Plan genommen. Weder Endlosschleife noch Abbruch.
CRITIC_ROUNDS = 2


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

    budget = Budget(
        max_tokens=PLAN_BUDGET_TOKENS, max_seconds=PLAN_BUDGET_SECONDS
    )
    run = Run.create(base, genre, today=today or date.today())
    # Der Ordner, in den der Nutzer gleich seine Suno-Downloads legen soll -
    # die Schlussmeldung nennt ihn, also muss er auch existieren.
    watch = base / WATCH_DIRNAME
    watch.mkdir(parents=True, exist_ok=True)

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
        f"lege die Downloads in {watch} ab."
    )
    return 0


def cmd_build(
    base: Path,
    run_id: str | None,
    client: AgentClient,
    budget: Budget,
) -> int:
    """Baut aus dem Material in watch einen geprueften Mixplan."""
    try:
        run = Run.load(base, run_id) if run_id else Run.latest_unfinished(base)
    except RunNotFound as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1
    if run is None:
        print(
            "Fehler: kein unfertiger Lauf gefunden. Starte zuerst dj-set plan.",
            file=sys.stderr,
        )
        return 1

    # Ein Lauf, der beim Planen abgebrochen ist, hat kein Profil. Ohne das
    # taugt er nicht als Ausgangspunkt.
    if not run.has("profile.json"):
        print(
            f"Fehler: der Lauf {run.run_id} hat keine profile.json. "
            f"Er wurde vor dem Ende der Planungsphase abgebrochen.",
            file=sys.stderr,
        )
        return 1
    profile = run.read_model("profile.json", Profile)

    try:
        files = scan(base / WATCH_DIRNAME)
        check_enough(files, profile)
    except NotEnoughMaterial as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    try:
        analyses, unreadable = analyse_all(client, files, profile, run, budget)
        shortlist = curate(client, analyses, unreadable, profile, budget)
        run.write_model("shortlist.json", shortlist)

        best: MixPlan | None = None
        best_setlist: Setlist | None = None
        fewest = -1
        complaints: tuple[str, ...] = ()

        for _round in range(CRITIC_ROUNDS + 1):
            setlist = arrange(
                client, shortlist, analyses, profile, budget, complaints=complaints
            )
            plan = design(client, setlist, analyses, profile, budget)
            verdict = critique(plan, analyses, profile)
            if best is None or fewest < 0 or len(verdict.complaints) < fewest:
                best, best_setlist, fewest = plan, setlist, len(verdict.complaints)
            if verdict.ok:
                break
            complaints = tuple(verdict.complaints)
    except (NotEnoughTracks, AgentError) as error:
        print(f"Fehler: {error}", file=sys.stderr)
        return 1

    if best is None or best_setlist is None:
        print("Fehler: es entstand kein Mixplan.", file=sys.stderr)
        return 1

    run.write_model("setlist.json", best_setlist)
    run.write_model("mixplan.json", best)

    print(f"Lauf {run.run_id}")
    print(f"  {len(analyses)} Tracks analysiert, {len(unreadable)} unlesbar")
    print(f"  {len(shortlist.accepted)} im Set, {len(shortlist.rejected)} aussortiert")
    if fewest:
        print(f"  Critic hat {fewest} Beanstandungen offen gelassen:")
        for complaint in critique(best, analyses, profile).complaints:
            print(f"    - {complaint}")
    print(f"  Mixplan: {run.path('mixplan.json')}")
    print("  Das Rendern kommt in einer spaeteren Ausbaustufe.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dj-set")
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan", help="Genre-Profil und Suno-Prompts erzeugen")
    plan.add_argument("--genre", required=True, help="frei waehlbar, z.B. 'liquid dnb'")
    plan.add_argument("--length", type=float, required=True, help="Ziellaenge in Minuten")
    plan.add_argument("--base", type=Path, default=Path("."), help="Projektwurzel")

    build = subparsers.add_parser("build", help="Set aus dem Material in watch bauen")
    build.add_argument("--run", default=None, help="Lauf-ID, sonst der letzte unfertige")

    args = parser.parse_args(argv)
    if args.command == "plan":
        from djset.agents.client import ClaudeClient

        return cmd_plan(
            base=args.base,
            genre=args.genre,
            length_minutes=args.length,
            client=ClaudeClient(),
        )
    if args.command == "build":
        from djset.agents.client import ClaudeClient

        return cmd_build(
            Path.cwd(),
            args.run,
            ClaudeClient(),
            Budget(
                max_tokens=BUILD_BUDGET_TOKENS,
                max_seconds=BUILD_BUDGET_SECONDS,
            ),
        )
    return 1


if __name__ == "__main__":
    sys.exit(main())
