"""Naechtlicher Geschaeftsdaten-Lauf: sammeln -> rendern -> planen -> ausfuehren (CLI).

Ablauf (Plan Teil B, Task 9): Laeden aus den sales-claw-Env-Dateien; je Laden `hat_sichten`, sonst
uebersprungen. **Alle DB-Lesungen vor jedem Schreiben**; ein `DbFehler` endet mit Exit 2, ohne dass etwas
geschrieben ist. Laeden werden nach Marke gruppiert, Marke -> `marken()`-Name -> `companys/<Firma>`.
Sammelkontakte und `source='system'` bekommen keinen Steckbrief. Namen sind je Firma eindeutig.
Das Wochenbild der letzten abgeschlossenen ISO-Woche entsteht nur, wenn es fehlt (`--wochenbild-neu`
erzwingt). Protokoll nur mit Zaehlungen, Schema-Namen, Firmen-Namen, Fehlertypen und id8 - nie Namen von
Kontakten, E-Mails oder Pfade unterhalb der Firma.

Schutzregeln: Eine Firma, bei der ein Laden fehlt (keine Sichten, abgelehnte Env-Datei), wird in diesem
Lauf gar nicht abgeglichen - ihre Kontakte saehen sonst verschwunden aus. Wuerden mehr als 20 % (und mehr
als 5) ihrer Steckbriefe auf einmal archiviert, bleibt die Firma ebenfalls stehen; nur Kopien gesperrter
Kontakte werden trotzdem entfernt (Datenschutz vor Vollstaendigkeit). Termin-Notizen (`Termine/`) zaehlt der
Waechter getrennt mit denselben Schwellen; greift einer der beiden Bereiche, steht die Firma. Notizen
abgesagter Termine und gesperrter Kontakte sind Datenschutz-Loeschungen wie gesperrte Steckbriefe;
verschwundene Termine gehen nach `Termine/_archiv/`. Je Lauf hoechstens 15 Loeschungen
ueber alle Firmen, gesperrte Kopien zuerst. `knowledge/.gitignore` haelt `companys/*/Kontakte/` aus
Rowboats Versionsgeschichte heraus (nur eine eigene oder fehlende Datei wird angefasst).

Exit-Codes: 0 ok; 1 Teilerfolg oder Konfig-Fehler (Laden/Firma/Kontakt uebersprungen, Abweichung,
Dateifehler); 2 DB-Fehler vor jedem Schreiben. `stand.json` wird ausser bei `--trocken` immer geschrieben.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_HIER = Path(__file__).resolve().parent
for _pfad in (_HIER.parents[2], _HIER.parent / "scripts", _HIER):  # Repo-Wurzel, wissens_spiegel, Module
    if str(_pfad) not in sys.path:
        sys.path.insert(0, str(_pfad))

from ablage import (ARCHIV_ORDNER, TERMIN_ARCHIV, TERMIN_UEBERSICHT, UEBERSICHT_PRAEFIX, Ist, Plan,  # noqa: E402
                    Soll, Takt, aufraeumen, ausfuehren, bestand, planen)
from laeden import FIRMEN, Laden, firmenordner, pruefen  # noqa: E402
from namen import dateistamm, eindeutig, relpfad  # noqa: E402
from newsletter import newsletter_notiz  # noqa: E402
from newsletter import stamm as nl_stamm  # noqa: E402
from quelle import DbFehler, Ereignis, Kontakt, Medium, NewsletterInhalt, PsqlQuelle, Quelle, Versand  # noqa: E402
from steckbrief import (ARCHIV, KATEGORIEN, OHNE, UEBERSICHT_GESAMT, UEBERSICHTEN,  # noqa: E402
                        bereich_uebersicht, bereiche_uebersicht, kategorie, people_index, steckbrief,
                        uebersicht_stamm, weitere_kategorien)
from termine import (TERMIN_PRAEFIX, UEBERSICHT_TERMINE, gesperrte_termin_schluessel,  # noqa: E402
                     termine_aus, termine_soll)
from wochenbild import letzte_kw, wochenbild, zeitraum  # noqa: E402
from wochenbild import stamm as wb_stamm  # noqa: E402

log = logging.getLogger("geschaeftsdaten")

# Stamm-Laengen so, dass `relpfad` praktisch nie kuerzen muss (Kuerzen koennte Namen zusammenfallen lassen;
# doppelte Endpfade werden trotzdem unten abgefangen).
MAX_NAME = 100
MAX_BETREFF = 120
ABLAGE_OPTIONEN: dict = {}
ARCHIV_VERMERK = "> Nicht mehr in sales-claw gefunden, deshalb archiviert."
MASSEN_ANTEIL = 0.20  # mehr als dieser Anteil der Steckbriefe einer Firma auf einmal ins Archiv -> Stopp
MASSEN_MIN = 5        # ... aber erst ab mehr als so vielen (kleine Firmen sollen archivieren koennen)
ALLE = "*"            # in `gestoert`: keine Firma abgleichen
GI_ANFANG = "# rowboat-geschaeftsdaten: Anfang (automatisch gepflegt, bitte nicht bearbeiten)"
GI_ENDE = "# rowboat-geschaeftsdaten: Ende"
GI_ZEILEN = ("companys/*/Kontakte/",)


@dataclass
class Ergebnis:
    je_firma: dict[str, dict[str, int]] = field(default_factory=dict)
    uebersprungen: list[str] = field(default_factory=list)
    exit_code: int = 0
    gitignore: str = ""


@dataclass
class _Gelesen:
    laden: Laden
    kontakte: list[Kontakt]
    ereignisse: list[Ereignis]
    versand: list[Versand]


def _id8(wert: str) -> str:
    return wert[:8]


def _archiv_text(firma: Path):
    archiv_link = f'parent: "[[{FIRMEN}/{firma.name}/{UEBERSICHTEN}/{uebersicht_stamm(ARCHIV)}]]"'

    def archiv_inhalt(ist: Ist) -> str:
        text = (firma / ist.rel).read_text(encoding="utf-8")
        ende = text.find("\n---\n", 4)
        if ende < 0 or ARCHIV_VERMERK in text:
            return text
        if ist.rel.startswith("Termine/"):
            # Termin-Notiz: Kopf samt parent auf `Termine/_overview` bleibt, nur der Vermerk kommt dazu.
            ende += len("\n---\n")
            return text[:ende] + "\n" + ARCHIV_VERMERK + "\n" + text[ende:]
        # Im Archiv haengt der Steckbrief an der Archiv-Uebersicht statt an seinem alten Bereich.
        kopf = [z for z in text[:ende].split("\n") if not z.startswith(("parent:", "weitere_bereiche:"))]
        ende += len("\n---\n")
        return "\n".join([*kopf, archiv_link]) + "\n---\n\n" + ARCHIV_VERMERK + "\n" + text[ende:]
    return archiv_inhalt


def _lesen(quelle: Quelle, laeden: list[Laden], heute: date) -> tuple[dict, list[_Gelesen], list[str],
                                                                       dict[str, NewsletterInhalt], list[Medium]]:
    """Alle Lesungen. Wirft DbFehler, bevor irgendetwas geschrieben ist."""
    uebersprungen: list[str] = []
    gelesen: list[_Gelesen] = []
    for laden in laeden:
        if not quelle.hat_sichten(laden.schema):
            log.warning("Laden %s: keine Sichten fuer rowboat_leser, uebersprungen", laden.schema)
            uebersprungen.append(laden.schema)
            continue
        gelesen.append(_Gelesen(laden, quelle.kontakte(laden.schema), quelle.ereignisse(laden.schema),
                                quelle.versand(laden.schema)))
    marken = quelle.marken()
    ids = sorted({v.newsletter for g in gelesen for v in g.versand})
    inhalte = {i.id: i for i in quelle.newsletter_inhalte(ids)} if ids else {}
    von, bis = zeitraum(*letzte_kw(heute))
    medien = quelle.medien(von, bis)
    return marken, gelesen, uebersprungen, inhalte, medien


def _empfehler(ereignisse: list[Ereignis]) -> tuple[str | None, str | None]:
    empf = [e for e in ereignisse if e.typ == "empfehlung" and (e.empfohlen_von_lead or e.empfohlen_von_name)]
    if not empf:
        return None, None
    e = max(empf, key=lambda e: e.zeit)
    return e.empfohlen_von_lead, e.empfohlen_von_name


_Eintrag = tuple[Kontakt, str, list[Ereignis]]  # (Kontakt, Laden-Schema, Ereignisse)


def _sammeln(gruppe: list[_Gelesen]) -> tuple[list[_Eintrag], set[str], list[Kontakt], list[Ereignis]]:
    """(Eintraege mit Steckbrief, gesperrte Lead-IDs, Kontakte und Ereignisse fuers Wochenbild)."""
    gesperrt: set[str] = set()
    eintraege: list[_Eintrag] = []
    alle_kontakte: list[Kontakt] = []
    alle_ereignisse: list[Ereignis] = []
    for g in gruppe:
        je_kontakt: dict[str, list[Ereignis]] = {}
        for e in g.ereignisse:
            je_kontakt.setdefault(e.lead_id, []).append(e)
        alle_ereignisse += g.ereignisse
        for k in g.kontakte:
            if k.gesperrt:  # zuerst: auch ein gesperrter Sammel-/Systemkontakt verliert seine alte Kopie
                gesperrt.add(k.id)
                if k.id not in g.laden.sammel_ids and k.source != "system":
                    alle_kontakte.append(k)
                continue
            if k.id in g.laden.sammel_ids or k.source == "system":
                continue
            alle_kontakte.append(k)
            eintraege.append((k, g.laden.schema, je_kontakt.get(k.id, [])))
    return eintraege, gesperrt, alle_kontakte, alle_ereignisse


def _steckbrief_pfade(firma: Path, gruppe: list[_Gelesen]) -> tuple[dict[str, str], dict[str, str], set[str], int]:
    """(Steckbrief-Pfade je Lead-ID ab `knowledge/` mit `.md`, Namen der platzierten Kontakte, gesperrte
    Lead-IDs, Zahl uebersprungener Kontakte). Namen sind eindeutig ueber den ganzen Kontakte-Baum der Firma."""
    praefix = f"{FIRMEN}/{firma.name}"
    eintraege, gesperrt, _, _ = _sammeln(gruppe)
    uebersprungen = 0
    staemme = eindeutig([(k.id, dateistamm(k.name, f"Kontakt {_id8(k.id)}", MAX_NAME), k.created_at)
                         for k, _, _ in eintraege])
    pfade: dict[str, str] = {}  # id -> rel ab knowledge/
    belegt: set[str] = set()
    for k, _, _ in eintraege:
        try:
            rel = relpfad(f"{praefix}/Kontakte/{kategorie(k)}", staemme[k.id])
        except ValueError:
            log.warning("%s: Kontakt %s hat keinen gueltigen Pfad, uebersprungen", firma.name, _id8(k.id))
            uebersprungen += 1
            continue
        if rel.casefold() in belegt:
            log.warning("%s: Kontakt %s faellt auf einen belegten Pfad, uebersprungen", firma.name, _id8(k.id))
            uebersprungen += 1
            continue
        belegt.add(rel.casefold())
        pfade[k.id] = rel
    namen = {k.id: k.name or "" for k, _, _ in eintraege if k.id in pfade}
    return pfade, namen, gesperrt, uebersprungen


def _termine_fuer_firma(firma: Path, gruppe: list[_Gelesen], pfade: dict[str, str], namen: dict[str, str], *,
                        heute: date) -> tuple[list[Soll], set[str]]:
    """(Soll der Termin-Notizen samt Uebersicht, abgesagte uids). Termine haengen nur an platzierten
    Steckbriefen (`pfade`); gesperrte Kontakte haben keinen Pfad und damit keine Termin-Notiz."""
    termine, abgesagt = termine_aus([e for g in gruppe for e in g.ereignisse])
    archiv = firma / TERMIN_ARCHIV
    archiv_vorhanden = (archiv.is_dir() and not archiv.is_symlink()
                        and any(p.is_file() and not p.is_symlink() for p in archiv.glob("*.md")))
    return termine_soll(firma.name, termine, pfade, namen, heute=heute, archiv_vorhanden=archiv_vorhanden), abgesagt


def _firma_soll(firma: Path, marke: str, gruppe: list[_Gelesen], inhalte: dict[str, NewsletterInhalt],
                medien: list[Medium], wissen: Path, *, heute: date, wochenbild_neu: bool
                ) -> tuple[list[Soll], set[str], int, set[str]]:
    """Soll-Liste, gesperrte Lead-IDs, Zahl uebersprungener Eintraege und abgesagte Termin-uids einer Firma."""
    praefix = f"{FIRMEN}/{firma.name}"
    eintraege, _, alle_kontakte, alle_ereignisse = _sammeln(gruppe)
    pfade, namen, gesperrt, uebersprungen = _steckbrief_pfade(firma, gruppe)

    # Bereichs-Uebersichten: eine je Ordner, der als Haupt- oder Nebenbereich vorkommt, plus eine Gesamtuebersicht.
    haupt: dict[str, int] = {}
    neben: dict[str, int] = {}
    for k, _, _ in eintraege:
        if k.id in pfade:
            haupt[kategorie(k)] = haupt.get(kategorie(k), 0) + 1
            for w in weitere_kategorien(k):
                neben[w] = neben.get(w, 0) + 1
    # Vom Lauf archivierte Steckbriefe haben kein Soll mehr, haengen aber an der Archiv-Uebersicht
    # (`_archiv_text`). Gezaehlt wird der Bestand vor dem Lauf: ein frisch archivierter Steckbrief
    # bekommt seine Uebersicht spaetestens in der naechsten Nacht.
    archiv_ordner = firma / "Kontakte" / ARCHIV
    if archiv_ordner.is_dir() and not archiv_ordner.is_symlink():
        auf_platte = sum(1 for p in archiv_ordner.glob("*.md") if p.is_file() and not p.is_symlink())
        if auf_platte > haupt.get(ARCHIV, 0):
            haupt[ARCHIV] = auf_platte
    reihenfolge = [*KATEGORIEN.values(), OHNE, ARCHIV]
    ordner_liste = [o for o in reihenfolge if o in haupt or o in neben]
    ziel = {o: f"{praefix}/{UEBERSICHTEN}/{uebersicht_stamm(o)}" for o in ordner_liste}
    gesamt = f"{praefix}/{UEBERSICHTEN}/{UEBERSICHT_GESAMT}"

    people = people_index(wissen)
    soll: list[Soll] = []
    for k, schema, ereignisse in eintraege:
        if k.id not in pfade:
            continue
        lead, name = _empfehler(ereignisse)
        empfohlen = f"[[{pfade[lead][:-3]}]]" if lead in pfade else name
        text = steckbrief(k, ereignisse, laden=schema, heute=heute,
                          people=people.link(k.name, k.email_klein), empfohlen_von=empfohlen,
                          uebersicht=ziel[kategorie(k)], weitere=[ziel[w] for w in weitere_kategorien(k)])
        soll.append(Soll(pfade[k.id][len(praefix) + 1:], k.id, text))
    termin_soll, abgesagt = _termine_fuer_firma(firma, gruppe, pfade, namen, heute=heute)
    soll += termin_soll
    for o in ordner_liste:
        soll.append(Soll(f"{UEBERSICHTEN}/{uebersicht_stamm(o)}.md", f"{UEBERSICHT_PRAEFIX}{o}",
                         bereich_uebersicht(schluessel=f"{UEBERSICHT_PRAEFIX}{o}", firma=firma.name, ordner=o,
                                            anzahl=haupt.get(o, 0), neben=neben.get(o, 0), eltern=gesamt)))
    if ordner_liste:
        marken_notiz = f"{praefix}/Marke" if (firma / "Marke.md").is_file() else None
        soll.append(Soll(f"{UEBERSICHTEN}/{UEBERSICHT_GESAMT}.md", f"{UEBERSICHT_PRAEFIX}{UEBERSICHT_GESAMT}",
                         bereiche_uebersicht(schluessel=f"{UEBERSICHT_PRAEFIX}{UEBERSICHT_GESAMT}",
                                             firma=firma.name, eltern=marken_notiz,
                                             bereiche=[(ziel[o], o, haupt.get(o, 0)) for o in ordner_liste])))

    # Newsletter der eigenen Marke, Empfaenger summiert ueber die Laeden.
    versand: dict[str, Versand] = {}
    for g in gruppe:
        for v in g.versand:
            inh = inhalte.get(v.newsletter)
            if inh is None or inh.mandant != marke:
                continue
            alt = versand.get(v.newsletter)
            versand[v.newsletter] = v if alt is None else Versand(
                v.newsletter, min(alt.erster_versand, v.erster_versand), alt.empfaenger + v.empfaenger)
    nl_staemme = eindeutig([(nid, dateistamm(nl_stamm(inhalte[nid], v.erster_versand),
                                             f"Newsletter {_id8(nid)}", MAX_BETREFF), v.erster_versand)
                            for nid, v in versand.items()])
    nl_links: list[tuple[datetime, str, int]] = []
    for nid, v in sorted(versand.items()):
        try:
            rel = relpfad(f"{praefix}/Newsletter", nl_staemme[nid])
        except ValueError:
            log.warning("%s: Newsletter %s hat keinen gueltigen Pfad, uebersprungen", firma.name, _id8(nid))
            uebersprungen += 1
            continue
        soll.append(Soll(rel[len(praefix) + 1:], f"nl:{nid}", newsletter_notiz(inhalte[nid], v)))
        nl_links.append((v.erster_versand, rel[:-3], v.empfaenger))

    # Wochenbild der letzten abgeschlossenen Woche, nur wenn es fehlt.
    jahr, kw = letzte_kw(heute)
    wb_rel = f"Wochenbild/{wb_stamm(jahr, kw)}.md"
    if wochenbild_neu or not (firma / wb_rel).exists():
        von, bis = zeitraum(jahr, kw)
        woche = [(ziel, n) for t, ziel, n in sorted(nl_links) if von <= t < bis]
        eigene_medien = [m for m in medien if m.mandant == marke]
        soll.append(Soll(wb_rel, f"kw:{jahr}-W{kw:02d}",
                         wochenbild(firma.name, jahr, kw, alle_kontakte, alle_ereignisse, woche, eigene_medien)))
    return soll, gesperrt, uebersprungen, abgesagt


def gitignore_pflegen(wissen: Path) -> str:
    """Haelt `companys/*/Kontakte/` per `knowledge/.gitignore` aus Rowboats Versionsgeschichte
    (isomorphic-git `add`/`statusMatrix` beachten .gitignore fuer noch nicht verfolgte Dateien).
    Legt die Datei an, wenn sie fehlt, oder erneuert nur den eigenen Block. Eine fremde Datei (ohne
    eigenen Block) oder ein Symlink wird nie angefasst. Ergebnis: angelegt, aktuell, ergaenzt, fremd."""
    pfad = wissen / ".gitignore"
    block = "\n".join((GI_ANFANG, *GI_ZEILEN, GI_ENDE)) + "\n"
    if pfad.is_symlink():
        return "fremd"
    if not pfad.exists():
        _atomar(pfad, block)
        return "angelegt"
    text = pfad.read_text(encoding="utf-8")
    kopf = GI_ANFANG.split(" (")[0]
    a = text.rfind(kopf)  # der letzte eigene Block zaehlt
    if a < 0:
        return "fremd"
    e = text.find(GI_ENDE, a)
    if e < 0:
        # Endmarke fehlt: nichts abschneiden (dahinter koennen fremde Zeilen stehen), frischen Block anhaengen.
        log.warning(".gitignore: Endmarke des eigenen Blocks fehlt, neuer Block angehaengt")
        _atomar(pfad, text + ("" if text.endswith("\n") or not text else "\n") + block)
        return "ergaenzt"
    e += len(GI_ENDE) + (1 if text[e + len(GI_ENDE):e + len(GI_ENDE) + 1] == "\n" else 0)
    neu = text[:a] + block + text[e:]
    if neu == text:
        return "aktuell"
    _atomar(pfad, neu)
    return "ergaenzt"


def _atomar(pfad: Path, text: str) -> None:
    tmp = pfad.with_name(f".{pfad.name}.gd.tmp")
    tmp.write_bytes(text.encode("utf-8"))
    os.chmod(tmp, 0o644)
    os.replace(tmp, pfad)


def _massenschutz(plan: Plan, ist: list[Ist], gesperrt: set[str]) -> bool:
    """True, wenn zu viele Steckbriefe oder zu viele Termin-Notizen auf einmal ins Archiv bzw. weg (ohne
    gesperrte Kopien) gingen. Beide Bereiche werden getrennt gegen dieselben Schwellen gezaehlt; greift einer,
    steht die ganze Firma (auch die Steckbrief-Archivierung dieser Nacht, wenn nur die Termine zu viele sind)."""
    def ist_termin(rel: str) -> bool:
        return rel.startswith("Termine/")

    def zu_viele(weg: int, aktiv: int) -> bool:
        return weg > MASSEN_MIN and weg > MASSEN_ANTEIL * aktiv

    aktiv_kontakte = sum(1 for i in ist if i.rel.startswith("Kontakte/")
                         and not i.rel.startswith(ARCHIV_ORDNER + "/"))
    aktiv_termine = sum(1 for i in ist if ist_termin(i.rel) and not i.rel.startswith(TERMIN_ARCHIV + "/")
                        and i.rel != TERMIN_UEBERSICHT)
    frei = [i for i in plan.entfernen if i.schluessel not in gesperrt]
    weg_termine = (sum(1 for i, _ in plan.archivieren if ist_termin(i.rel))
                   + sum(1 for i in frei if i.schluessel.startswith(TERMIN_PRAEFIX)))
    weg_kontakte = (sum(1 for i, _ in plan.archivieren if not ist_termin(i.rel))
                    + sum(1 for i in frei if not i.schluessel.startswith((UEBERSICHT_PRAEFIX, TERMIN_PRAEFIX))
                          and i.schluessel != UEBERSICHT_TERMINE))
    return zu_viele(weg_kontakte, aktiv_kontakte) or zu_viele(weg_termine, aktiv_termine)


def lauf(quelle: Quelle, laeden: list[Laden], wissen: Path, *, heute: date, trocken: bool,
         wochenbild_neu: bool, ablage_optionen: dict, gestoert: frozenset[str] = frozenset(),
         massen_ok: frozenset[str] = frozenset()) -> Ergebnis:
    """`gestoert`: Marken, deren Laden-Konfiguration unvollstaendig ist (`ALLE` = jede); diese Firmen
    werden nicht abgeglichen. `massen_ok`: Firmen (Ordnername, ohne Gross-/Kleinschreibung), fuer die der
    Massenarchiv-Schutz in diesem einen Lauf nicht greift (`--massen-ok`)."""
    freigegeben = {f.casefold() for f in massen_ok}
    ergebnis = Ergebnis()
    try:
        marken, gelesen, uebersprungen, inhalte, medien = _lesen(quelle, laeden, heute)
    except DbFehler as e:
        log.error("DB-Fehler vor dem Schreiben (%s), nichts geschrieben", e)
        ergebnis.exit_code = 2
        return ergebnis
    ergebnis.uebersprungen = list(uebersprungen)
    gestoert = frozenset(gestoert) | {l.marke for l in laeden if l.schema in uebersprungen}

    if not trocken:
        try:
            ergebnis.gitignore = gitignore_pflegen(wissen)
        except OSError as e:
            ergebnis.gitignore = "fehler"
            log.warning(".gitignore nicht gepflegt (%s)", type(e).__name__)
        if ergebnis.gitignore == "fremd":
            log.warning(".gitignore im Wissensordner ist fremd: Steckbriefe koennen in Rowboats Versionsgeschichte "
                        "landen. Zeile companys/*/Kontakte/ von Hand ergaenzen.")

    gruppen: dict[str, list[_Gelesen]] = {}
    for g in gelesen:
        gruppen.setdefault(g.laden.marke, []).append(g)

    takt = Takt(int(ablage_optionen.get("paket", 15)))
    plaene: list[tuple[Path, Plan, int, bool]] = []
    for marke in sorted(gruppen):
        schemata = [g.laden.schema for g in gruppen[marke]]
        if ALLE in gestoert or marke in gestoert:
            log.warning("Marke %s: Laden-Konfiguration oder Sichten unvollstaendig, Firma nicht abgeglichen", marke)
            ergebnis.uebersprungen.append(f"Marke {marke}")
            continue
        name = marken.get(marke)
        try:
            if not name:
                raise ValueError("Marke ohne Mandant")
            firma = firmenordner(wissen, name)
        except ValueError as e:
            log.warning("Marke %s: kein Firmenordner (%s), Laeden %s uebersprungen", marke, e, ", ".join(schemata))
            ergebnis.uebersprungen += schemata
            continue
        try:
            if not trocken and firma.is_dir():
                if (n := aufraeumen(firma)):
                    log.info("%s: %d alte Zwischendateien entfernt", firma.name, n)
            soll, gesperrt, n_ueber, abgesagt = _firma_soll(firma, marke, gruppen[marke], inhalte, medien,
                                                            wissen, heute=heute, wochenbild_neu=wochenbild_neu)
            ist = bestand(firma)
            # Termin-Notizen abgesagter Termine und gesperrter Kontakte: Datenschutz-Loeschung (Phase
            # "entfernen", zaehlt nicht beim Massen-Waechter). Nur die Lead-IDs gehen als gesperrte IDs hinein.
            gesperrt |= gesperrte_termin_schluessel((i.schluessel for i in ist), gesperrt, abgesagt)
            plan = planen(soll, ist, gesperrt, _archiv_text(firma))
        except OSError as e:
            log.error("%s: Dateifehler beim Planen (%s), Firma uebersprungen", firma.name, type(e).__name__)
            ergebnis.uebersprungen.append(f"Firma {firma.name}")
            continue
        massen = _massenschutz(plan, ist, gesperrt)
        if massen and firma.name.casefold() in freigegeben:
            log.info("%s: Massen-Waechter fuer diesen Lauf freigegeben", firma.name)
            massen = False
        if massen:
            # Auch wenn nur die Termine zu viele sind, steht die ganze Firma in dieser Nacht (bewusst so):
            # nur gesperrte Kopien werden entfernt, auch Steckbriefe werden dann nicht archiviert.
            log.warning("%s: %d Archivierungen waeren zu viele auf einmal, nur gesperrte Kopien werden entfernt",
                        firma.name, len(plan.archivieren))
            ergebnis.uebersprungen.append(f"Massenarchivierung {firma.name}")
            plan = Plan([], [], [], [i for i in plan.entfernen if i.schluessel in gesperrt], plan.unveraendert)
        plaene.append((firma, plan, n_ueber, massen))

    # Erst alle gesperrten Kopien (ueber alle Firmen), dann der Rest - beides unter derselben Obergrenze.
    # Leere Bereichs-Uebersichten sind Aufraeumen, kein Datenschutz und keine Kontaktdaten: sie kommen
    # zuletzt dran und bekommen nur, was von der Obergrenze uebrig ist.
    zaehler: dict[str, dict[str, int]] = {}
    for phase in ("entfernen", "rest", "uebersichten"):
        for firma, plan, n_ueber, _ in plaene:
            if firma.name not in zaehler and phase != "entfernen":
                continue  # Dateifehler in einer frueheren Phase
            uebersichten = [i for i in plan.entfernen if i.schluessel.startswith(UEBERSICHT_PRAEFIX)]
            if phase == "entfernen":
                teil = replace(plan, schreiben=[], verschieben=[], archivieren=[], unveraendert=0,
                               entfernen=[i for i in plan.entfernen if i not in uebersichten])
            elif phase == "rest":
                teil = replace(plan, entfernen=[])
            else:
                teil = Plan([], [], [], uebersichten, 0)
            try:
                z = ausfuehren(teil, firma, trocken=trocken, takt=takt)
            except OSError as e:
                log.error("%s: Dateifehler beim Schreiben (%s), Firma abgebrochen", firma.name, type(e).__name__)
                ergebnis.uebersprungen.append(f"Firma {firma.name}")
                zaehler.pop(firma.name, None)
                continue
            gesamt = zaehler.setdefault(firma.name, dict.fromkeys(z, 0))
            for k, v in z.items():
                gesamt[k] += v

    for firma, plan, n_ueber, massen in plaene:
        if firma.name not in zaehler:
            continue
        z = zaehler[firma.name]
        z["uebersprungen"] = n_ueber
        ergebnis.je_firma[firma.name] = z
        log.info("%s: %s%s", firma.name, " ".join(f"{k}={v}" for k, v in z.items()), " (trocken)" if trocken else "")
        if n_ueber or z["abweichung"]:
            ergebnis.exit_code = max(ergebnis.exit_code, 1)
    if ergebnis.uebersprungen:
        ergebnis.exit_code = max(ergebnis.exit_code, 1)
    return ergebnis


def _privater_ordner(ordner: Path) -> None:
    ordner.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(ordner, 0o700)


def _stand_schreiben(pfad: Path, ergebnis: Ergebnis, trocken: bool, fehler: str | None = None) -> None:
    daten = {
        "zeit": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ergebnis": "fehler" if fehler else {0: "ok", 1: "teilweise", 2: "db_fehler"}.get(ergebnis.exit_code, "fehler"),
        "exit_code": ergebnis.exit_code,
        "trocken": trocken,
        "je_firma": ergebnis.je_firma,
        "uebersprungen": ergebnis.uebersprungen,
        "gitignore": ergebnis.gitignore,
    }
    if fehler:
        daten["fehler"] = fehler
    _privater_ordner(pfad.parent)
    tmp = pfad.with_name(f".{pfad.name}.tmp")
    tmp.write_text(json.dumps(daten, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, pfad)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Geschaeftsdaten aus sales-claw und Marketing nach Rowboat")
    parser.add_argument("--trocken", action="store_true", help="nur planen und zaehlen, nichts schreiben")
    parser.add_argument("--wochenbild-neu", action="store_true", help="Wochenbild auch neu schreiben, wenn es da ist")
    parser.add_argument("--heute", type=date.fromisoformat, default=None, help="JJJJ-MM-TT (fuer Tests)")
    parser.add_argument("--massen-ok", action="append", default=[], metavar="FIRMA",
                        help="Massenarchiv-Schutz fuer diese Firma (Ordnername) nur in diesem Lauf freigeben; "
                             "mehrfach moeglich")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    wissen = Path(os.environ.get("GESCHAEFTSDATEN_WISSEN", "/home/debian/rowboat-data/knowledge"))
    sales_claw = Path(os.environ.get("GESCHAEFTSDATEN_SALES_CLAW", "/home/debian/sales-claw"))
    container = os.environ.get("GESCHAEFTSDATEN_DB_CONTAINER", "debian-supabase-db-1")
    stand = Path(os.environ.get("GESCHAEFTSDATEN_STAND", "/home/debian/rowboat-geschaeftsdaten/stand.json"))
    heute = args.heute or datetime.now(timezone.utc).astimezone(ZoneInfo("Europe/Berlin")).date()

    if not wissen.is_dir():
        log.error("Wissensordner fehlt")
        return 1
    pruefung = pruefen(sales_claw)
    for w in pruefung.warnungen:
        log.warning("Laden-Konfiguration: %s", w)
    gestoert = pruefung.gestoert | ({ALLE} if pruefung.unklar else set())
    fehler = None
    try:
        ergebnis = lauf(PsqlQuelle(container), pruefung.laeden, wissen, heute=heute, trocken=args.trocken,
                        wochenbild_neu=args.wochenbild_neu, ablage_optionen=ABLAGE_OPTIONEN,
                        gestoert=frozenset(gestoert), massen_ok=frozenset(args.massen_ok))
    except Exception as e:  # noqa: BLE001 - stand.json muss trotzdem entstehen; nur der Typ wird gemeldet
        fehler = type(e).__name__
        log.error("Lauf abgebrochen (%s)", fehler)
        ergebnis = Ergebnis(exit_code=1)
    if pruefung.warnungen:
        ergebnis.uebersprungen += pruefung.warnungen
        ergebnis.exit_code = max(ergebnis.exit_code, 1)
    if not args.trocken:
        try:
            _stand_schreiben(stand, ergebnis, args.trocken, fehler)
        except OSError as e:
            log.error("stand.json nicht geschrieben (%s)", type(e).__name__)
            ergebnis.exit_code = max(ergebnis.exit_code, 1)
    log.info("Ende: exit=%d uebersprungen=%d", ergebnis.exit_code, len(ergebnis.uebersprungen))
    return ergebnis.exit_code


if __name__ == "__main__":
    sys.exit(main())
