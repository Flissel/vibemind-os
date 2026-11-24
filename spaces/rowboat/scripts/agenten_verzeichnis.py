"""VibeMind-Spaces fuer Rowboat: je aktivem Space mit Agent eine Lese-Karte, KEIN Rowboat-Agent.

Quelle ist das Verzeichnis des Gehirns (`config/space_agent_registry.yml`). Die Beispielsaetze kommen aus
`brain/the_brain/data/capabilities.yaml` (`anchor_phrases`), zugeordnet ueber
`core.step_space.space_for_step` wie im Plan des Gehirns.

Ausgabe: `spaces/rowboat/vibemind-spaces/<space>.md`, ausgeliefert nach `WorkDir/vibemind-spaces/` (nicht
`WorkDir/agents/`). Der YAML-Kopf hat eigene Felder (space, agent, titel, rolle, stand, beispiele bzw. geplant)
und bewusst KEIN Feld des Agenten-Schemas von Rowboat (name, instructions, tools, provider, model): Selbst in
`agents/` kopiert, liesse `FSAgentsRepo` die Datei nicht als Agent gelten (`name` fehlt, die Pruefung bricht
ab). Gelesen wird der Ordner nur von der Ansicht "VibeMind-Spaces" der App. Ausgefuehrt wird nichts davon;
Auftraege gehen an das Gehirn, das ueber OpenFang ausfuehrt.

    python spaces/rowboat/scripts/agenten_verzeichnis.py            # schreibt den Ordner neu
    python spaces/rowboat/scripts/agenten_verzeichnis.py --pruefen  # Exit 1, wenn der Ordner veraltet ist

Schreibt nur in den Ausgabeordner im Arbeitsbaum, nie nach ~/.rowboat (Auslieferung: README im Ordner).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

import yaml

WURZEL = Path(__file__).resolve().parents[3]
REGISTRY = WURZEL / "config" / "space_agent_registry.yml"
FAEHIGKEITEN = WURZEL / "brain" / "the_brain" / "data" / "capabilities.yaml"
AUSGABE = WURZEL / "spaces" / "rowboat" / "vibemind-spaces"
ZIELORDNER = "vibemind-spaces"  # unter dem WorkDir von Rowboat; nie "agents"
MIN_BEISPIELE, MAX_BEISPIELE = 2, 4
LAEUFT, NICHT_ANGESCHLOSSEN = "läuft", "noch nicht angeschlossen"
# Felder des Agenten-Schemas von Rowboat (apps/x/packages/shared/src/agent.ts): duerfen nie im Kopf stehen.
AGENTEN_FELDER = frozenset({"name", "instructions", "tools", "provider", "model", "description"})

# Stand "läuft" nur fuer Spaces mit mindestens einem festen Weg, der heute nachweislich geht
# (Entscheidung der Koordination). Der Test verlangt dazu ein Ereignis mit `execution` in der Registry.
LAUFENDE_SPACES = frozenset({"ideas", "bubbles", "desktop"})

# Titel und Rolle in einem deutschen Satz; fuer nicht angeschlossene Spaces dazu ein Satz, was geplant ist.
# Fehlt hier ein aktiver Space mit Agent, bricht der Erzeuger ab (kein stiller Platzhalter).
ROLLEN: dict[str, tuple[str, str]] = {
    "bubbles": ("Bubbles", "Legt Bubbles (Ideen-Sammlungen) auf der VibeMind-Leinwand an, bewertet sie, "
                           "befördert ganze Bubbles zu Projekten und wechselt zwischen ihnen."),
    "ideas": ("Ideen", "Pflegt die Ideen in einer Bubble: anlegen, ändern, verknüpfen, formatieren, bewerten "
                       "und zu Zusammenfassung oder White Paper ausbauen."),
    "coding": ("Coding", "Erzeugt, ändert, testet und prüft Code über die Coding-Engine."),
    "desktop": ("Desktop", "Liest den Bildschirm und führt Desktop-Aufgaben Schritt für Schritt über die "
                           "Schleife des Gehirns aus, ohne ausdrücklichen Schalter nur als Trockenlauf."),
    "research": ("Recherche", "Recherchiert im Netz, fasst Quellen zusammen und legt das Ergebnis als Idee ab."),
    "rowboat": ("Rowboat", "Beantwortet Fragen aus dem Wissensgraphen von Rowboat und meldet dessen Stand."),
    "minibook": ("Minibook", "Meldet den Stand des Minibook-Bereichs für gemeinsame Projekte."),
    "schedule": ("Termine", "Legt geplante Aufgaben und Erinnerungen an und ändert, verschiebt oder löscht sie."),
    "n8n": ("n8n", "Legt n8n-Abläufe an, listet, startet, schaltet und beschreibt sie."),
    "sales": ("Sales", "Nimmt im Auftragsbuch Aufträge einer festen Art entgegen, die das Gehirn dort ablegt."),
    "learning": ("Lernen", "Erstellt Kurse aus Quellen und begleitet das Üben mit Aufgaben, Hinweisen und "
                           "Fortschritt."),
    "video": ("Video", "Erzeugt Videos, wertet Bilder und Videos aus und übernimmt Lippensynchronisierung "
                       "und Stimme."),
    "flowzen": ("Flowzen", "Begleitet das Wohlbefinden mit Check-ins, Tagebuch und Tagesablauf."),
    "mirofish": ("MiroFish", "Rechnet Prognosen und Simulationen und durchsucht die Graphen dazu."),
}
GEPLANT: dict[str, str] = {
    "coding": "Aufträge an die Coding-Engine mit Rückmeldung des Ergebnisses in den Chat.",
    "research": "Recherche mit Quellen, deren Ergebnis als Idee in der Bubble landet.",
    "rowboat": "Fragen an den Wissensgraphen aus dem VibeMind-Chat, mit Antwort und Quelle.",
    "minibook": "Gemeinsame Projekte im Minibook anlegen und ihren Stand abfragen.",
    "schedule": "Erinnerungen und wiederkehrende Aufgaben aus einem Satz anlegen und verwalten.",
    "n8n": "n8n-Abläufe aus einer Beschreibung erzeugen und ihren Stand abfragen.",
    "sales": "Aufträge einer festen Art, die das Gehirn im Auftragsbuch ablegt; noch nicht aus Sätzen erreichbar.",
    "learning": "Kurse anlegen, üben und den Fortschritt abfragen, über die Werkzeuge des Lern-Space.",
    "video": "Videos erzeugen und auswerten, mit Stand der Aufträge im Chat.",
    "flowzen": "Check-ins und Empfehlungen für den Tag, mit Tagebuch.",
    "mirofish": "Prognosen und Simulationen starten und ihre Ergebnisse abfragen.",
}

_DEUTSCH = frozenset(
    "der die das den dem des ein eine einen einem ich mir mich mein meine du dein ist sind und oder nicht "
    "zeig zeige mach mache bitte neue neuen neues alle was wie wo für fuer mit von auf aus zu zum zur "
    "erstelle öffne oeffne lösche loesche starte erinner erinnere".split()
)
_WORT = re.compile(r"[a-zäöüß]+")
# Kein Satz fuer Menschen: Adresse oder Ereignis-Kennung vorneweg ("research.web latest ..."), oder ein
# Kennungsname mit Unterstrich (konkrete Karten- oder Bubble-Namen gehoeren nicht in die Ansicht).
_TECHNISCH = re.compile(r"https?://|^\s*[a-z_]+\.[a-z_.]+\b|\w_\w", re.I)
# Beispiele, die einen Weg zeigen wuerden, der noch nicht nachweislich geht (Desktop: nur Lesen laeuft).
_NICHT_ZEIGEN: dict[str, re.Pattern[str]] = {"desktop": re.compile(r"^\s*desktop-aufgabe", re.I)}


def deutsch(satz: str) -> bool:
    return bool(set(_WORT.findall(satz.lower())) & _DEUTSCH)


def aktive_spaces(registry: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    """Spaces, die eine Karte bekommen: eingeschaltet und mit Agent.

    Ohne Rechtetraeger (``rights_carrier: true``, z. B. ``recherche``): die haben nur Ausweis und Rechte
    fuer feste Aufrufe des Gehirns, keinen Agenten, der Nachrichten bearbeitet (Spec 8.3).
    """
    return {
        sid: spec for sid, spec in (registry.get("spaces") or {}).items()
        if isinstance(spec, Mapping) and spec.get("enabled") is True and isinstance(spec.get("agent"), str)
        and spec["agent"].strip() and spec.get("rights_carrier") is not True
    }


def feste_wege(spec: Mapping[str, Any]) -> list[str]:
    """Ereignisse mit festem Ausfuehrungsweg (`execution`) in der Registry."""
    return sorted(e for e, v in (spec.get("events") or {}).items() if isinstance(v, Mapping) and v.get("execution"))


def saetze_je_space(faehigkeiten: Iterable[Mapping[str, Any]],
                    space_von: Callable[[str, str], Optional[str]]) -> dict[str, list[str]]:
    """Beispielsaetze eingeschalteter Faehigkeiten je Space, deutsche zuerst, ohne Doppelte und Technisches."""
    roh: dict[str, list[str]] = {}
    for f in faehigkeiten:
        if not isinstance(f, Mapping) or f.get("enabled") is False:
            continue
        space = space_von(str(f.get("capability") or ""), str(f.get("execution_target") or ""))
        if not space:
            continue
        for satz in f.get("anchor_phrases") or ():
            if not isinstance(satz, str) or not satz.strip() or _TECHNISCH.search(satz):
                continue
            if space in _NICHT_ZEIGEN and _NICHT_ZEIGEN[space].search(satz):
                continue
            liste = roh.setdefault(space, [])
            if satz.strip() not in liste:
                liste.append(satz.strip())
    return {s: sorted(l, key=lambda x: not deutsch(x))[:MAX_BEISPIELE] for s, l in roh.items()}


def karte(sid: str, spec: Mapping[str, Any], saetze: list[str]) -> str:
    if sid not in ROLLEN:
        raise KeyError(f"keine deutsche Rolle fuer den Space {sid!r} in ROLLEN")
    titel, rolle = ROLLEN[sid]
    kopf: dict[str, Any] = {"space": sid, "agent": spec["agent"], "titel": titel, "rolle": rolle}
    if sid in LAUFENDE_SPACES:
        if len(saetze) < MIN_BEISPIELE:
            raise ValueError(f"Space {sid!r} laeuft, hat aber weniger als {MIN_BEISPIELE} Beispielsaetze")
        kopf.update(stand=LAEUFT, beispiele=list(saetze))
    else:
        if sid not in GEPLANT:
            raise KeyError(f"kein Satz in GEPLANT fuer den nicht angeschlossenen Space {sid!r}")
        kopf.update(stand=NICHT_ANGESCHLOSSEN, geplant=GEPLANT[sid])
    text = yaml.safe_dump(kopf, allow_unicode=True, sort_keys=False, width=1000)
    return (f"---\n{text}---\n"
            "<!-- Erzeugt von spaces/rowboat/scripts/agenten_verzeichnis.py aus config/space_agent_registry.yml; "
            "nicht von Hand ändern. Lese-Karte für die Ansicht VibeMind-Spaces, kein Rowboat-Agent. -->\n")


def karten(registry: Mapping[str, Any], saetze: Mapping[str, list[str]]) -> dict[str, str]:
    """Dateiname -> Inhalt, je aktivem Space mit Agent genau eine Karte."""
    return {f"{sid}.md": karte(sid, spec, list(saetze.get(sid, [])))
            for sid, spec in sorted(aktive_spaces(registry).items())}


def _space_von() -> Callable[[str, str], Optional[str]]:
    sys.path.insert(0, str(WURZEL / "brain" / "the_brain"))
    from core.step_space import space_for_step  # noqa: E402

    return lambda name, ziel: space_for_step(name, ziel)


def erzeugen() -> dict[str, str]:
    registry = yaml.safe_load(REGISTRY.read_text(encoding="utf-8"))
    faehigkeiten = yaml.safe_load(FAEHIGKEITEN.read_text(encoding="utf-8")) or []
    return karten(registry, saetze_je_space(faehigkeiten, _space_von()))


def _bestand() -> dict[str, str]:
    if not AUSGABE.exists():
        return {}
    return {p.name: p.read_text(encoding="utf-8") for p in AUSGABE.glob("*.md") if p.name != "README.md"}


def main(argv: Optional[list[str]] = None) -> int:
    teil = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    teil.add_argument("--pruefen", action="store_true", help="nichts schreiben; Exit 1, wenn veraltet")
    args = teil.parse_args(argv)
    soll, ist = erzeugen(), _bestand()
    if args.pruefen:
        veraltet = sorted(set(soll) ^ set(ist) | {n for n in soll if ist.get(n) != soll[n]})
        for name in veraltet:
            print(f"veraltet: {name}")
        return 1 if veraltet else 0
    AUSGABE.mkdir(parents=True, exist_ok=True)
    for name in sorted(set(ist) - set(soll)):
        print(f"ohne aktiven Space, bitte entfernen: {name}")  # nichts loeschen; der Test meldet es auch
    for name, text in soll.items():
        (AUSGABE / name).write_text(text, encoding="utf-8", newline="\n")
    print(f"{len(soll)} Karten in {AUSGABE.relative_to(WURZEL)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
