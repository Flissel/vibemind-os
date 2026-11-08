"""Naechtliche Wissens-Pruefung: wie gut haengen Rowboats Notizen zusammen?

Zaehlt nach Rowboats eigenen Graph-Regeln (Fork `apps/renderer/src/lib/graph-hierarchy.ts` + `wiki-links.ts`):
eine Kante entsteht nur aus `[[Ziel]]` (Text oder Frontmatter), dessen Pfad ab `knowledge/` mit `.md`
EXAKT (Gross-/Kleinschreibung) einer Notiz entspricht; Anker `#...` und Alias `|...` fallen weg. Kurzformen
wie `[[Notiz]]` sind im Editor klickbar, ergeben im Graph aber keine Kante – sie zaehlen hier als tot.
`Meetings/` und versteckte Ordner zeigt Rowboat nicht im Graph, sie zaehlen deshalb nicht mit.

Ergebnis: `knowledge/Kanal/Dashboards/Wissens-Gesundheit.md` (Konvention der Zentrale: Dashboards als
Notiz, nur Zahlen, keine Kontaktnamen) und ein Verlauf in `<stand>/verlauf.json`. Tote Ziele unter
`People/` und `companys/*/Kontakte/` werden nur gezaehlt, nie mit Namen genannt.

Lauf: `python3 pruefung.py [--wissen DIR] [--stand DIR] [--trocken]`. Exit 0 ok, 2 Wissensordner fehlt.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

BERLIN = ZoneInfo("Europe/Berlin")
ZIEL = "Kanal/Dashboards/Wissens-Gesundheit.md"
ERZEUGT_VON = "wissens-pruefung"
VERLAUF_MAX = 60
VERLAUF_ZEIGEN = 30
TOP = 15
OHNE_QUELLE = "(ohne Angabe)"
OBERSTE_EBENE = "(oberste Ebene)"

_LINK = re.compile(r"\[\[([^\[\]]+)\]\]")
_PRIVAT = re.compile(r"^(people/|companys/[^/]+/kontakte/)", re.IGNORECASE)
_AUSLOESER = re.compile(r"@(rowboat)", re.IGNORECASE)


def link_ziel(roh: str) -> str | None:
    """Notizpfad ab `knowledge/` wie Rowboats `toKnowledgePath`, oder None (ungueltig oder keine Notiz)."""
    ziel = roh.split("|")[0].split("#")[0].strip()
    ziel = re.sub(r"^/+", "", ziel)
    ziel = re.sub(r"^\./", "", ziel)
    if ziel.startswith("knowledge/"):
        ziel = ziel[len("knowledge/"):]
    if not ziel or ".." in ziel or ziel.endswith("/"):
        return None
    if ziel.lower().endswith(".md"):
        return ziel
    name = ziel.rsplit("/", 1)[-1]
    if "." in name and re.fullmatch(r"[A-Za-z0-9]{1,5}", name.rsplit(".", 1)[-1]):
        return None  # Bild, Tabelle, PDF ...: keine Notiz
    return f"{ziel}.md"


def _notizen(wissen: Path) -> dict[str, str]:
    texte: dict[str, str] = {}
    for wurzel, ordner, namen in os.walk(wissen, followlinks=False):
        basis = Path(wurzel)
        ordner[:] = sorted(o for o in ordner if not o.startswith(".") and not (basis / o).is_symlink())
        for name in sorted(namen):
            pfad = basis / name
            if not name.endswith(".md") or name.startswith(".") or pfad.is_symlink():
                continue
            rel = pfad.relative_to(wissen).as_posix()
            if rel.lower().startswith("meetings/"):
                continue
            try:
                texte[rel] = pfad.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
    return texte


def _quelle(text: str) -> str:
    if not text.startswith("---\n"):
        return OHNE_QUELLE
    ende = text.find("\n---", 4)
    for zeile in text[4:ende if ende > 0 else 4096].splitlines():
        if zeile.startswith("erzeugt_von:"):
            return zeile.split(":", 1)[1].strip().strip("\"'") or OHNE_QUELLE
    return OHNE_QUELLE


def _gruppe(rel: str) -> str:
    teile = rel.split("/")
    if len(teile) == 1:
        return OBERSTE_EBENE
    if teile[0] == "companys" and len(teile) >= 4:
        return "/".join(teile[:3])
    return teile[0]


@dataclass
class Kennzahlen:
    notizen: int = 0
    kanten: int = 0
    lose: int = 0
    tote: int = 0
    inseln: int = 0
    groesste_insel: int = 0
    tote_ziele: Counter = field(default_factory=Counter)
    je_gruppe: dict[str, tuple[int, int]] = field(default_factory=dict)  # Gruppe -> (lose, gesamt)
    je_quelle: dict[str, tuple[int, int]] = field(default_factory=dict)


def kennzahlen(wissen: Path) -> Kennzahlen:
    texte = _notizen(wissen)
    k = Kennzahlen(notizen=len(texte))
    nachbarn: dict[str, set[str]] = {rel: set() for rel in texte}
    for rel, text in texte.items():
        for treffer in _LINK.finditer(text):
            ziel = link_ziel(treffer.group(1))
            if ziel is None:
                continue
            if ziel in nachbarn:
                if ziel != rel:
                    nachbarn[rel].add(ziel)
                    nachbarn[ziel].add(rel)
            else:
                k.tote += 1
                k.tote_ziele[ziel] += 1
    k.kanten = sum(len(n) for n in nachbarn.values()) // 2
    lose = {rel for rel, n in nachbarn.items() if not n}
    k.lose = len(lose)

    gesehen: set[str] = set()
    for start in nachbarn:
        if start in gesehen:
            continue
        stapel, groesse = [start], 0
        gesehen.add(start)
        while stapel:
            x = stapel.pop()
            groesse += 1
            for y in nachbarn[x] - gesehen:
                gesehen.add(y)
                stapel.append(y)
        k.inseln += 1
        k.groesste_insel = max(k.groesste_insel, groesse)

    k.je_gruppe = _verteilung({rel: _gruppe(rel) for rel in texte}, lose)
    k.je_quelle = _verteilung({rel: _quelle(text) for rel, text in texte.items()}, lose)
    return k


def _verteilung(zuordnung: dict[str, str], lose: set[str]) -> dict[str, tuple[int, int]]:
    """Schluessel -> (lose, gesamt)."""
    zaehler: dict[str, list[int]] = {}
    for rel, schluessel in zuordnung.items():
        eintrag = zaehler.setdefault(schluessel, [0, 0])
        eintrag[0] += rel in lose
        eintrag[1] += 1
    return {s: (lo, ge) for s, (lo, ge) in zaehler.items()}


def _prozent(teil: int, ganz: int) -> str:
    return f"{round(100 * teil / ganz)} %" if ganz else "0 %"


def _tabelle(titel: tuple[str, ...], zeilen: list[tuple]) -> list[str]:
    kopf = "| " + " | ".join(titel) + " |"
    trenner = "|" + "|".join("---" for _ in titel) + "|"
    return [kopf, trenner, *("| " + " | ".join(str(w) for w in z) + " |" for z in zeilen)]


def _sauber(text: str) -> str:
    return _AUSLOESER.sub(r"@ \1", text.replace("|", "/").replace("[[", "[ ["))


def notiz(k: Kennzahlen, verlauf: list[dict], jetzt: datetime) -> str:
    """Markdown der Dashboard-Notiz. `verlauf`: fruehere Laeufe (aelteste zuerst), ohne den aktuellen."""
    vortag = verlauf[-1] if verlauf else {}

    def vor(name: str) -> str:
        return str(vortag[name]) if name in vortag else "–"

    zeit = jetzt.astimezone(BERLIN)
    zeilen = [
        "---",
        f"erzeugt_von: {ERZEUGT_VON}",
        "art: dashboard",
        f"stand: {zeit.date().isoformat()}",
        "---",
        "",
        "> Automatisch erzeugt (nächtliche Wissens-Prüfung). Nicht hier bearbeiten – der nächste Lauf "
        "überschreibt. Gezählt wird nach Rowboats Graph-Regeln: nur `[[voller/Pfad]]` mit exakter "
        "Schreibweise ergibt eine Linie.",
        "",
        "# Wissens-Gesundheit",
        "",
        f"Stand: {zeit:%d.%m.%Y %H:%M}",
        "",
        "## Kurzbild",
        "",
        *_tabelle(("Kennzahl", "Wert", "Vortag"), [
            ("Notizen", k.notizen, vor("notizen")),
            ("Lose Notizen", f"{k.lose} ({_prozent(k.lose, k.notizen)})", vor("lose")),
            ("Verbindungen", k.kanten, vor("kanten")),
            ("Tote Verbindungen", f"{k.tote} ({len(k.tote_ziele)} Ziele)", vor("tote")),
            ("Inseln", k.inseln, vor("inseln")),
            ("Größte Insel", k.groesste_insel, vor("groesste_insel")),
        ]),
        "",
        "## Lose Notizen je Ordner",
        "",
    ]
    gruppen = sorted(((g, lo, ge) for g, (lo, ge) in k.je_gruppe.items() if lo), key=lambda t: (-t[1], t[0]))
    zeilen += _tabelle(("Ordner", "lose", "gesamt", "Anteil"),
                       [(_sauber(g), lo, ge, _prozent(lo, ge)) for g, lo, ge in gruppen[:TOP]]) if gruppen \
        else ["Keine losen Notizen."]
    zeilen += ["", "## Lose Notizen je Quelle (`erzeugt_von`)", ""]
    quellen = sorted(((q, lo, ge) for q, (lo, ge) in k.je_quelle.items() if lo), key=lambda t: (-t[1], t[0]))
    zeilen += _tabelle(("Quelle", "lose", "gesamt", "Anteil"),
                       [(_sauber(q), lo, ge, _prozent(lo, ge)) for q, lo, ge in quellen[:TOP]]) if quellen \
        else ["Keine losen Notizen."]

    zeilen += ["", "## Tote Verbindungen: häufigste Ziele", ""]
    privat = sum(n for z, n in k.tote_ziele.items() if _PRIVAT.match(z) or "@" in z)
    offen = [(z, n) for z, n in k.tote_ziele.most_common() if not (_PRIVAT.match(z) or "@" in z)]
    zeilen += _tabelle(("Ziel", "Anzahl"), [(f"`{_sauber(z)}`", n) for z, n in offen[:TOP]]) if offen \
        else ["Keine."]
    if privat:
        zeilen += ["", f"Dazu {privat} tote Verbindungen auf Personen oder Kontakte (ohne Namen)."]

    zeilen += ["", "## Verlauf", ""]
    jetzt_zeile = {"zeit": jetzt.isoformat(), "notizen": k.notizen, "lose": k.lose, "tote": k.tote}
    reihe = [jetzt_zeile, *reversed(verlauf)][:VERLAUF_ZEIGEN]
    zeilen += _tabelle(("Datum", "Notizen", "lose", "tote"), [
        (f"{datetime.fromisoformat(v['zeit']).astimezone(BERLIN):%d.%m.%Y}", v.get("notizen", "–"),
         v.get("lose", "–"), v.get("tote", "–")) for v in reihe])
    return "\n".join(zeilen) + "\n"


def _atomar(pfad: Path, text: str) -> None:
    pfad.parent.mkdir(parents=True, exist_ok=True)
    tmp = pfad.with_name(f".{pfad.name}.wp.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, pfad)


def _verlauf_lesen(datei: Path) -> list[dict]:
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [d for d in daten if isinstance(d, dict) and isinstance(d.get("zeit"), str)] if isinstance(daten, list) else []


def main(argv: list[str] | None = None, *, jetzt: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lose Notizen und tote Verbindungen in Rowboats Wissen zaehlen")
    parser.add_argument("--wissen", type=Path, default=Path.home() / "rowboat-data" / "knowledge")
    parser.add_argument("--stand", type=Path, default=Path.home() / "rowboat-wissens-pruefung")
    parser.add_argument("--trocken", action="store_true", help="nur ausgeben, nichts schreiben")
    args = parser.parse_args(argv)
    jetzt = jetzt or datetime.now(timezone.utc)
    if not args.wissen.is_dir():
        print(f"Wissensordner fehlt: {args.wissen}", file=sys.stderr)
        return 2
    k = kennzahlen(args.wissen)
    verlauf_datei = args.stand / "verlauf.json"
    verlauf = _verlauf_lesen(verlauf_datei)
    text = notiz(k, verlauf, jetzt)
    if args.trocken:
        print(text)
        return 0
    _atomar(args.wissen / ZIEL, text)
    eintrag = {"zeit": jetzt.isoformat(), "notizen": k.notizen, "lose": k.lose, "kanten": k.kanten,
               "tote": k.tote, "inseln": k.inseln, "groesste_insel": k.groesste_insel}
    _atomar(verlauf_datei, json.dumps([*verlauf, eintrag][-VERLAUF_MAX:], ensure_ascii=False, indent=1))
    print(f"Wissens-Pruefung: {k.notizen} Notizen, {k.lose} lose, {k.tote} tote Verbindungen, {k.inseln} Inseln")
    return 0


if __name__ == "__main__":
    sys.exit(main())
