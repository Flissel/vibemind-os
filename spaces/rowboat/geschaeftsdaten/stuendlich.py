"""Stuendlicher Lauf: Post-Notizen und Termin-Notizen nach Rowboat (CLI).

Der Lauf legt an und aktualisiert, er loescht, archiviert und verschiebt nie. Was der naechtliche Lauf
(`lauf.py`) wegraeumen wuerde (abgesagte Termine, gesperrte Kontakte, verschwundene Termine), bleibt bis zur
Nacht liegen; bekommt ein Termin einen neuen Pfad, entsteht die Notiz neu und die alte bleibt. Gelesen wird
nur, was gebraucht wird: je Laden Kontakte und Ereignisse, dazu einmal die offene Post (nur mit Stichtag).
Alle Lesungen liegen vor dem ersten Schreiben; ein `DbFehler` endet mit Exit 2 ohne Schreiben.

Die gemeinsame Sperre (`sperre.py`) kommt zuerst: laeuft der naechtliche Lauf, setzt die Stunde aus (Exit 0).
Es gibt keine `stand.json` (die gehoert dem naechtlichen Lauf); das Ergebnis steht nur im Protokoll, und zwar
mit Zaehlungen, Firmen-Namen und Fehlertypen - nie Namen, Adressen oder Betreffe.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

_HIER = Path(__file__).resolve().parent
for _pfad in (_HIER.parents[2], _HIER.parent / "scripts", _HIER):  # Repo-Wurzel, wissens_spiegel, Module
    if str(_pfad) not in sys.path:
        sys.path.insert(0, str(_pfad))

from ablage import Plan, ausfuehren, bestand, planen  # noqa: E402
from laeden import Laden, firmenordner, pruefen  # noqa: E402
from lauf import ALLE, Ergebnis, _laden_lesen, _steckbrief_pfade, _termine_fuer_firma  # noqa: E402
from post import post_notizen, post_schreiben, stichtag_lesen  # noqa: E402
from quelle import DbFehler, PostMail, PsqlQuelle, Quelle  # noqa: E402
from sperre import SPERRE_STANDARD, sperre  # noqa: E402
from termine import gesperrte_termin_schluessel  # noqa: E402

log = logging.getLogger("geschaeftsdaten")
BERLIN = ZoneInfo("Europe/Berlin")
POST_LADEN = "sales"  # die Post der sales-ui gehoert zum Laden `sales`


def stunde(quelle: Quelle, laeden: list[Laden], wissen: Path, *, jetzt: datetime, stichtag: date | None,
           trocken: bool, gestoert: frozenset[str]) -> Ergebnis:
    """Ein stuendlicher Durchlauf. `gestoert`: wie bei `lauf.lauf` (`ALLE` = jede Marke)."""
    ergebnis = Ergebnis()
    heute = jetzt.astimezone(BERLIN).date()
    try:
        gelesen, uebersprungen = _laden_lesen(quelle, laeden, mit_versand=False)
        marken = quelle.marken()
        mails: list[PostMail] = quelle.post_offen(stichtag) if stichtag is not None else []
    except DbFehler as e:
        log.error("DB-Fehler vor dem Schreiben (%s), nichts geschrieben", e)
        ergebnis.exit_code = 2
        return ergebnis
    ergebnis.uebersprungen = list(uebersprungen)
    gestoert = frozenset(gestoert) | {l.marke for l in laeden if l.schema in uebersprungen}

    gruppen: dict[str, list] = {}
    for g in gelesen:
        gruppen.setdefault(g.laden.marke, []).append(g)

    steckbriefe: dict[str, str] = {}  # email_klein -> Steckbrief-Pfad ab knowledge/ (nur Laden `sales`)
    plaene: list[tuple[Path, Plan, int]] = []
    for marke in sorted(gruppen):
        schemata = [g.laden.schema for g in gruppen[marke]]
        if ALLE in gestoert or marke in gestoert:
            log.warning("Marke %s: Laden-Konfiguration oder Sichten unvollstaendig, Firma nicht angefasst", marke)
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
            pfade, namen, gesperrt, n_ueber = _steckbrief_pfade(firma, gruppen[marke])
            soll, abgesagt = _termine_fuer_firma(firma, gruppen[marke], pfade, namen, heute=heute)
            ist = [i for i in bestand(firma) if i.rel.startswith("Termine/")]
            gesperrt |= gesperrte_termin_schluessel((i.schluessel for i in ist), gesperrt, abgesagt)
            plan = planen(soll, ist, gesperrt, lambda _ist: "")
        except OSError as e:
            log.error("%s: Dateifehler beim Planen (%s), Firma uebersprungen", firma.name, type(e).__name__)
            ergebnis.uebersprungen.append(f"Firma {firma.name}")
            continue
        # Nur anlegen und aktualisieren: Umzuege werden zu neuen Notizen, die alten bleiben bis zur Nacht.
        nur_schreiben = Plan(plan.schreiben + [s for _, s in plan.verschieben], [], [], [], plan.unveraendert)
        plaene.append((firma, nur_schreiben, n_ueber))
        for g in gruppen[marke]:
            if g.laden.schema != POST_LADEN:
                continue
            for k in g.kontakte:
                if k.email_klein and not k.gesperrt and k.id in pfade:
                    steckbriefe.setdefault(k.email_klein, pfade[k.id])

    for firma, plan, n_ueber in plaene:
        try:
            z = ausfuehren(plan, firma, trocken=trocken)
        except OSError as e:
            log.error("%s: Dateifehler beim Schreiben (%s), Firma abgebrochen", firma.name, type(e).__name__)
            ergebnis.uebersprungen.append(f"Firma {firma.name}")
            continue
        z["uebersprungen"] = n_ueber
        ergebnis.je_firma[firma.name] = z
        log.info("%s: %s%s", firma.name, " ".join(f"{k}={v}" for k, v in z.items()), " (trocken)" if trocken else "")
        if n_ueber or z["abweichung"]:
            ergebnis.exit_code = max(ergebnis.exit_code, 1)

    notizen = post_notizen(mails, steckbriefe, jetzt=jetzt, stichtag=stichtag,
                           company_da=(wissen / "Company.md").is_file())
    try:
        pz = post_schreiben(wissen, notizen, trocken=trocken)
        log.info("Post: %s%s", " ".join(f"{k}={v}" for k, v in pz.items()), " (trocken)" if trocken else "")
    except OSError as e:
        log.error("Post nicht geschrieben (%s)", type(e).__name__)
        ergebnis.uebersprungen.append("Post")
    if ergebnis.uebersprungen:
        ergebnis.exit_code = max(ergebnis.exit_code, 1)
    return ergebnis


def _jetzt(wert: datetime | None) -> datetime:
    if wert is None:
        return datetime.now(timezone.utc).astimezone(BERLIN)
    return wert if wert.tzinfo else wert.replace(tzinfo=BERLIN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Post und Termine aus sales-claw nach Rowboat (stuendlich)")
    parser.add_argument("--trocken", action="store_true", help="nur planen und zaehlen, nichts schreiben")
    parser.add_argument("--jetzt", type=datetime.fromisoformat, default=None, help="ISO-Zeitpunkt (fuer Tests)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    # Die Sperre kommt zuerst: ohne sie wird weder konfiguriert noch gelesen.
    sperrdatei = Path(os.environ.get("GESCHAEFTSDATEN_SPERRE", SPERRE_STANDARD))
    with sperre(sperrdatei, warten_s=0) as frei:
        if not frei:
            log.info("Geschaeftsdaten-Lauf aktiv, Stunde ausgesetzt")
            return 0
        wissen = Path(os.environ.get("GESCHAEFTSDATEN_WISSEN", "/home/debian/rowboat-data/knowledge"))
        sales_claw = Path(os.environ.get("GESCHAEFTSDATEN_SALES_CLAW", "/home/debian/sales-claw"))
        container = os.environ.get("GESCHAEFTSDATEN_DB_CONTAINER", "debian-supabase-db-1")
        if not wissen.is_dir():
            log.error("Wissensordner fehlt")
            return 1
        pruefung = pruefen(sales_claw)
        for w in pruefung.warnungen:
            log.warning("Laden-Konfiguration: %s", w)
        gestoert = pruefung.gestoert | ({ALLE} if pruefung.unklar else set())
        stichtag = stichtag_lesen(sales_claw / ".env")
        try:
            ergebnis = stunde(PsqlQuelle(container), pruefung.laeden, wissen, jetzt=_jetzt(args.jetzt),
                              stichtag=stichtag, trocken=args.trocken, gestoert=frozenset(gestoert))
        except Exception as e:  # noqa: BLE001 - nur der Typ wird gemeldet
            log.error("Stunde abgebrochen (%s)", type(e).__name__)
            return 1
        if pruefung.warnungen:
            ergebnis.exit_code = max(ergebnis.exit_code, 1)
        log.info("Ende: exit=%d uebersprungen=%d", ergebnis.exit_code, len(ergebnis.uebersprungen))
        return ergebnis.exit_code


if __name__ == "__main__":
    sys.exit(main())
