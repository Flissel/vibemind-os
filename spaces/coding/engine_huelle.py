"""Huelle um die Coding-Engine: ein Entwurf wird ein Auftrag, der Stand kommt aus ihren Dateien.

Die Pipeline (``pipeline.py``) fuehrt das Auftragsbuch. Fuer einen Entwurf im
Format ``vibemind.entwurf/1`` schreibt diese Huelle die Aufgabendateien der
Engine. Stufe, Paket, geschriebene Dateien und Pruefausgang liest sie bei jeder
Abfrage aus dem, was die Engine auf die Platte schreibt:

- ``<projekt>/tasks/epic-NNN-tasks.json``  Stand je Aufgabe, ``output_files``
- ``<projekt>/out/.generation_running``    der Erzeuger laeuft
- ``<projekt>/out/.generation_status.json`` der Erzeuger ist fertig

Hier startet nichts. Wer den Erzeuger startet, entscheidet der Aufrufer der
Pipeline ueber genau eine Stelle (``auftrag_starten(..., anstoss=...)``).
"""

from __future__ import annotations

import copy
import json
import logging
import re
import shutil
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from spaces.coding import engine_aufgaben
from spaces.coding.pipeline import (
    ABGEBROCHEN, AGENT, FEHLGESCHLAGEN, FERTIG, LAEUFT, SPACE, STUFEN, UNGEPRUEFT, WARTET, PipelineFehler,
    _unter_wurzel, arbeitswurzel,
)

logger = logging.getLogger(__name__)

STATUS_DATEI = ".generation_status.json"
LAUF_DATEI = ".generation_running"


def auftrag_anlegen(
    entwurf: Dict[str, Any], auftrag_id: str, titel: Optional[str] = None,
    arbeitspaket: Optional[str] = None, ohne_npm: bool = True, herkunft: str = "entwurf",
    projektordner: Optional[Path] = None, bestehend: Optional[Path] = None, pruefer: Optional[str] = None,
) -> Dict[str, Any]:
    """Schreibt die Aufgabendateien unter die Arbeitswurzel und liefert den Auftrag (noch nicht gespeichert).

    ``bestehend``: ein Ordner unter der Arbeitswurzel, dessen Inhalt der Ausgangsstand des
    Zielordners ist (Aenderung an vorhandenem Code). Er wird vor den Aufgabendateien nach
    ``<projekt>/out`` kopiert; ein vorhandenes package.json bleibt dabei stehen, und es kommt
    kein App-Paket dazu.
    """
    entwurf = copy.deepcopy(entwurf)
    if arbeitspaket is not None:
        pakete = [p for p in entwurf.get("arbeitspakete") or [] if isinstance(p, dict) and p.get("id") == arbeitspaket]
        if not pakete:
            vorhanden = ", ".join(str(p.get("id")) for p in entwurf.get("arbeitspakete") or [] if isinstance(p, dict))
            raise PipelineFehler(f"Arbeitspaket {arbeitspaket} ist unbekannt; vorhanden: {vorhanden}")
        entwurf["arbeitspakete"] = pakete

    # Ein ganzer Entwurf endet in einer App, die mit einem Befehl startet; ein einzelnes Paket nicht.
    # Eine Aenderung an vorhandenem Code bekommt kein App-Paket.
    mit_app = arbeitspaket is None and bestehend is None
    dateien = engine_aufgaben.aufgabendateien(entwurf, ohne_npm=ohne_npm, mit_app=mit_app)
    projekt = Path(projektordner) if projektordner else arbeitswurzel() / auftrag_id
    if bestehend is not None:
        ziel = projekt / "out"
        if ziel.exists() and any(ziel.iterdir()):
            raise PipelineFehler("Der Zielordner des Auftrags ist nicht leer")
        shutil.copytree(bestehend, ziel, dirs_exist_ok=True, ignore=shutil.ignore_patterns("node_modules"))
    # prueft die Arbeitswurzel selbst und legt package.json im Zielordner an
    engine_aufgaben.schreiben(entwurf, str(projekt), ohne_npm=ohne_npm, mit_app=mit_app)

    pruefung = entwurf.get("pruefung") if isinstance(entwurf.get("pruefung"), dict) else {}
    offen = pruefung.get("offene_fragen")
    jetzt = time.time()
    return {
        "id": auftrag_id,
        "space": SPACE,
        "agent": AGENT,
        "art": "engine",
        "herkunft": herkunft,
        "aus_satz": engine_aufgaben.AUS_SATZ in ((entwurf.get("kontext") or {}).get("annahmen") or []),
        "titel": (titel or str(entwurf.get("titel") or "") or auftrag_id).strip(),
        "aufgabe": str(entwurf.get("aufgabe") or ""),
        "run_id": str(entwurf.get("run_id") or ""),
        "arbeitspaket": arbeitspaket,
        "entwurf": entwurf,
        "ohne_npm": ohne_npm,
        "projektordner": str(projekt),
        "bestehend": str(bestehend) if bestehend is not None else None,
        "zielordner": str(projekt / "out"),
        "pakete": [
            {"paket": inhalt["epic_name"].split(" ", 1)[0], "titel": inhalt["epic_name"].split(" ", 1)[-1],
             "epic": inhalt["epic_id"]}
            for inhalt in dateien.values()
        ],
        "modellaufrufe_erwartet": engine_aufgaben.modellaufrufe_mindestens(dateien),
        "modellaufrufe_hoechstens": engine_aufgaben.modellaufrufe_hoechstens(dateien),
        "startbefehl": engine_aufgaben.START_BEFEHL if mit_app else None,
        # Name des Pruefers der Aufgabe (siehe abnahme.py); ohne ihn endet ein Bau hoechstens "ungeprueft".
        "pruefer": pruefer,
        # "fertig" meldet nie die Engine selbst, sondern erst die Abnahme nach ihrem Abschluss.
        "abnahme_noetig": True,
        "hinweise": [f"Der Entwurf hat {offen} offene Frage(n)"] if isinstance(offen, int) and offen > 0 else [],
        "angestossen": False,
        "stufe": "plan",
        "zustand": WARTET,
        "angelegt": jetzt,
        "ergebnis": None,
    }


# ── Lesen, was die Engine geschrieben hat ──────────────────────────────────


def _json(pfad: Path) -> Any:
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _zusammen(zustaende: List[str]) -> str:
    """Ein Zustand fuer mehrere Teile."""
    if not zustaende or all(z == WARTET for z in zustaende):
        return WARTET
    if any(z == FEHLGESCHLAGEN for z in zustaende):
        return FEHLGESCHLAGEN
    if all(z == FERTIG for z in zustaende):
        return FERTIG
    return LAEUFT


def _aus_aufgaben(aufgaben: List[Dict[str, Any]]) -> str:
    """Zustand einer Gruppe von Engine-Aufgaben (pending, running, completed, failed, skipped)."""
    stand = [str(a.get("status") or "pending") for a in aufgaben]
    if not stand:
        return FERTIG  # eine Stufe ohne Aufgaben haelt nichts auf
    if any(s in ("failed", "skipped") for s in stand):
        return FEHLGESCHLAGEN
    if all(s == "completed" for s in stand):
        return FERTIG
    if any(s in ("running", "completed") for s in stand):
        return LAEUFT
    return WARTET


def _pakete(auftrag: Dict[str, Any], uebersprungen: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    projekt = Path(auftrag["projektordner"])
    lage: List[Dict[str, Any]] = []
    for eintrag in auftrag.get("pakete") or []:
        inhalt = _json(projekt / "tasks" / f"{eintrag['epic'].lower()}-tasks.json")
        aufgaben = [a for a in (inhalt or {}).get("tasks") or [] if isinstance(a, dict)]
        pruef = [a for a in aufgaben if str(a.get("type") or "").startswith("verify")]
        bau = [a for a in aufgaben if a not in pruef]
        bau_zustand = _aus_aufgaben(bau) if aufgaben else WARTET
        pruef_zustand = _aus_aufgaben(pruef) if aufgaben else WARTET
        if bau_zustand in (WARTET, LAEUFT) and pruef_zustand == FERTIG and not pruef:
            pruef_zustand = WARTET  # ohne Pruefaufgaben wartet die Pruefung auf den Bau
        if eintrag["epic"] in (uebersprungen or []):
            # Die Engine hat die Kette vorher beendet: dieses Paket wurde nie begonnen.
            bau_zustand = pruef_zustand = ABGEBROCHEN
        gescheitert = next((a for a in aufgaben if a.get("status") in ("failed", "skipped")), None)
        # ``output_files`` kann ein geplantes Ziel nennen. Gemeldet ist nur, was eine
        # erledigte Aufgabe nennt; gezeigt wird nur, was davon auf der Platte liegt.
        gemeldet = sorted({str(f) for a in aufgaben if a.get("status") == "completed"
                           for f in a.get("output_files") or []})
        aus = Path(auftrag["zielordner"])
        lage.append({
            "gemeldet": gemeldet,
            "paket": eintrag["paket"],
            "titel": eintrag["titel"],
            "name": anzeigename(eintrag),
            "zustand": ABGEBROCHEN if bau_zustand == ABGEBROCHEN else _zusammen([bau_zustand, pruef_zustand]),
            "bau": bau_zustand,
            "pruefung": pruef_zustand,
            # Ein Pfad, der aus dem Zielordner hinausfuehrt, wird nie auf der Platte nachgesehen.
            "dateien": [f for f in gemeldet if _unter_wurzel(aus / f, aus) and (aus / f).is_file()],
            "pruefungen": [
                {"befehl": str(a.get("command") or ""), "bestanden": a.get("status") == "completed",
                 "ausgefuehrt": a.get("status") in ("completed", "failed")} for a in pruef
            ],
            "fehler": str(gescheitert.get("error_message") or "") if gescheitert else None,
        })
    return lage


APP_NAME = "Die App starten und prüfen"

# Bekannte Meldungen der Engine und ihr Satz fuer Menschen. Unbekannte bleiben in ``fehler_grund``.
GRUENDE = (
    (re.compile(r"openfang unreachable|connection attempts failed", re.I),
     "Der Modelldienst war nicht erreichbar."),
    (re.compile(r"tagesgrenze|daily limit|paused|pausiert|quota", re.I),
     "Der Modelldienst nimmt gerade keine Aufträge an (Tagesgrenze oder Pause)."),
    (re.compile(r"OPENFANG_MAX_CALLS|call limit", re.I),
     "Die Obergrenze der Modellaufrufe für diesen Lauf war erreicht."),
    (re.compile(r"no file written|contained no file block", re.I),
     "Das Modell hat keine Datei geliefert."),
    (re.compile(r"timed out", re.I), "Ein Schritt hat seine Zeitgrenze überschritten."),
    (re.compile(r"Command failed \(exit (\d+)\)"), "Ein Prüfbefehl ist mit Fehlercode {0} beendet worden."),
    (re.compile(r"did not answer on port|before it answered on port", re.I),
     "Die App hat nach dem Start nicht geantwortet."),
    (re.compile(r"installing is off", re.I),
     "Das Projekt braucht Pakete aus npm, aber Installieren ist für diesen Auftrag aus."),
    (re.compile(r"Not enough free memory for npm install", re.I),
     "Für das Installieren der Pakete war zu wenig Speicher frei."),
)


def anzeigename(paket: Dict[str, Any]) -> str:
    """Name eines Pakets fuer Menschen: der Titel ohne Kennung; das App-Paket heisst nach seinem Zweck."""
    if paket.get("paket") == "APP":
        return APP_NAME
    titel = str(paket.get("titel") or "").strip()
    kennung = str(paket.get("paket") or "")
    return titel[len(kennung):].strip() if kennung and titel.startswith(kennung) else titel or kennung


def grund_satz(meldung: Optional[str]) -> Optional[str]:
    """Die Meldung der Engine als deutscher Satz, wenn sie bekannt ist."""
    for muster, satz in GRUENDE:
        treffer = muster.search(meldung or "")
        if treffer:
            return satz.format(*treffer.groups())
    return None


def fehlersatz(pakete: List[Dict[str, Any]]) -> Optional[str]:
    """Ein deutscher Satz: welches Paket in welcher Stufe scheiterte, warum, und was danach nicht mehr lief."""
    nr, paket = next(((i, p) for i, p in enumerate(pakete, start=1) if p["zustand"] == FEHLGESCHLAGEN),
                     (0, None))
    if paket is None:
        return None
    stufe = "Bau" if paket["bau"] == FEHLGESCHLAGEN else "Prüfung"
    satz = f"{stufe}: Paket {nr} von {len(pakete)} „{anzeigename(paket)}“ ist gescheitert."
    grund = grund_satz(paket.get("fehler"))
    if grund:
        satz += f" {grund}"
    nicht = sum(1 for p in pakete if p["zustand"] == ABGEBROCHEN)
    if nicht:
        satz += (" Das übrige Paket wurde nicht mehr begonnen." if nicht == 1
                 else f" Die übrigen {nicht} Pakete wurden nicht mehr begonnen.")
    return satz


NOTIZ_FERTIG = ("Selbstauskunft der Engine: alle Pakete gebaut, ihre eigenen Prüfungen bestanden. "
                "Das ist kein Stand; den setzt die Abnahme.")
NOTIZ_GESCHEITERT = "Selbstauskunft der Engine: nicht alles gebaut oder ihre eigenen Prüfungen nicht bestanden."
ALT_SATZ = "Der Auftrag stammt aus der Zeit vor der Abnahme; geprüft hat nur die Engine selbst"
ABNAHME_LAEUFT = "Die Engine ist fertig; die unabhängige Abnahme läuft."


def _abnahme_sicht(auftrag: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    abnahme = auftrag.get("_abnahme")
    if auftrag.get("_abnahme_offen"):
        return {"zustand": LAEUFT, "satz": ABNAHME_LAEUFT, "gruende": [], "pruefer": auftrag.get("pruefer")}
    if not abnahme:
        return None
    return {k: abnahme.get(k) for k in ("zustand", "satz", "gruende", "pruefer", "allgemein", "pruefer_ausgang",
                                         "dauer_s")}


def auffrischen(auftrag: Dict[str, Any]) -> Dict[str, Any]:
    """Setzt ``zustand``, ``stufe`` und ``fehler`` des Auftrags aus den Dateien der Engine (ohne zu speichern)."""
    ziel = Path(auftrag["zielordner"])
    schluss = _json(ziel / STATUS_DATEI)
    uebersprungen = [str(e) for e in (schluss or {}).get("skipped_epics") or []] if isinstance(schluss, dict) else []
    pakete = _pakete(auftrag, uebersprungen)
    begonnen = [p for p in pakete if p["zustand"] != ABGEBROCHEN]
    bau = _zusammen([p["bau"] for p in begonnen])
    pruefung = _zusammen([p["pruefung"] for p in begonnen])

    if isinstance(schluss, dict):
        engine_fertig = schluss.get("status") == "completed" and bau == pruefung == FERTIG
        abschluss = FERTIG if engine_fertig else FEHLGESCHLAGEN
    else:
        abschluss = WARTET

    # Die Selbstauskunft der Engine ist nur eine Notiz. Den Stand setzt die Abnahme.
    abnahme = auftrag.get("abnahme") if isinstance(auftrag.get("abnahme"), dict) else None
    notiz = None
    abnahme_offen = False
    if abschluss == FERTIG:
        notiz = NOTIZ_FERTIG
        if abnahme and abnahme.get("zustand") in (FERTIG, UNGEPRUEFT, FEHLGESCHLAGEN):
            abschluss = abnahme["zustand"]
        elif auftrag.get("abnahme_noetig"):
            abschluss, abnahme_offen = LAEUFT, True
        else:
            abschluss = UNGEPRUEFT  # Auftrag aus der Zeit vor der Abnahme
            abnahme = {"zustand": UNGEPRUEFT, "satz": ALT_SATZ + ".", "gruende": [ALT_SATZ]}
    elif abschluss == FEHLGESCHLAGEN:
        notiz = NOTIZ_GESCHEITERT
    stufen = {"annahme": FERTIG, "plan": FERTIG if pakete else WARTET, "bau": bau, "pruefung": pruefung,
              "abschluss": abschluss}

    if auftrag.get("zustand") == ABGEBROCHEN:
        zustand = ABGEBROCHEN
    elif isinstance(schluss, dict):
        zustand = abschluss
    elif (ziel / LAUF_DATEI).exists() or LAEUFT in (bau, pruefung) or FERTIG in (bau, pruefung):
        # Auch ein gescheitertes Paket laesst den Auftrag laufen, bis der Erzeuger seinen Abschluss schreibt.
        zustand = LAEUFT
    elif FEHLGESCHLAGEN in (bau, pruefung):
        zustand = LAEUFT
    else:
        zustand = WARTET

    # Der Dienst, der den Erzeuger startet, hat festgehalten, dass er ohne Abschluss endete.
    unterbrochen = auftrag.get("unterbrochen") if abschluss == WARTET and zustand != ABGEBROCHEN else None
    if unterbrochen:
        zustand = FEHLGESCHLAGEN

    auftrag["zustand"] = zustand
    auftrag["stufe"] = next((name for name in STUFEN if stufen[name] != FERTIG), STUFEN[-1])
    grund = next((p["fehler"] for p in pakete if p["fehler"]), None)
    if zustand != FEHLGESCHLAGEN:
        auftrag["fehler"] = auftrag["fehler_grund"] = None
    elif unterbrochen:
        auftrag["fehler"], auftrag["fehler_grund"] = unterbrochen, None
    elif abnahme and abnahme.get("zustand") == FEHLGESCHLAGEN and abschluss == FEHLGESCHLAGEN:
        auftrag["fehler"] = abnahme.get("satz")
        auftrag["fehler_grund"] = "; ".join(abnahme.get("gruende") or []) or None
    else:
        auftrag["fehler"], auftrag["fehler_grund"] = fehlersatz(pakete), grund
    auftrag["_abnahme"] = abnahme
    auftrag["_abnahme_offen"] = abnahme_offen and zustand == LAEUFT
    auftrag["_notiz_engine"] = notiz
    auftrag["_stufen"] = stufen
    auftrag["_pakete"] = pakete
    auftrag["_dauer_s"] = schluss.get("elapsed_seconds") if isinstance(schluss, dict) else None
    return auftrag


def sicht(auftrag: Dict[str, Any]) -> Dict[str, Any]:
    """Was Liste und Stand fuer einen Auftrag der Engine zeigen."""
    if "_stufen" not in auftrag:
        auffrischen(auftrag)
    pakete = auftrag["_pakete"]
    return {
        "auftrag": auftrag["id"],
        "titel": auftrag["titel"],
        "herkunft": auftrag["herkunft"],
        "art": "engine",
        "stufe": auftrag["stufe"],
        "zustand": auftrag["zustand"],
        "angelegt": auftrag["angelegt"],
        # Laufzeit des Erzeugers laut seiner Abschlussdatei; None, solange er laeuft.
        "dauer_s": auftrag.get("_dauer_s"),
        "fehler": auftrag.get("fehler"),
        # Die Meldung der Engine zum Fehler, unuebersetzt; ``fehler`` ist der Satz fuer Menschen.
        "fehler_grund": auftrag.get("fehler_grund"),
        "hinweise": auftrag.get("hinweise") or [],
        # Das Urteil der Abnahme (fertig, ungeprueft, fehlgeschlagen; laeuft, solange sie prueft).
        "abnahme": _abnahme_sicht(auftrag),
        # Was die Engine ueber sich selbst sagt; nie der Stand.
        "notiz_engine": auftrag.get("_notiz_engine"),
        "pruefer": auftrag.get("pruefer"),
        "run_id": auftrag.get("run_id") or None,
        "arbeitspaket": auftrag.get("arbeitspaket"),
        "aktuelles_paket": next((p["paket"] for p in pakete if p["zustand"] != FERTIG), None),
        # Kurzform fuer die Reiterleiste der App: Stufe, Paket i von n, Zustand.
        "kurz": {
            "stufe": auftrag["stufe"],
            "zustand": auftrag["zustand"],
            "paket_nr": next((i for i, p in enumerate(pakete, start=1) if p["zustand"] != FERTIG), len(pakete)),
            "pakete_gesamt": len(pakete),
        },
        "angestossen": bool(auftrag.get("angestossen")),
        "modellaufrufe_erwartet": auftrag.get("modellaufrufe_erwartet"),
        "modellaufrufe_hoechstens": auftrag.get("modellaufrufe_hoechstens"),
        "startbefehl": auftrag.get("startbefehl"),
        "stufen": [
            {"stufe": name, "zustand": auftrag["_stufen"][name], "dauer_s": None, "fehler": None} for name in STUFEN
        ],
        "pakete": pakete,
    }


def ergebnis(auftrag: Dict[str, Any]) -> Dict[str, Any]:
    """Ergebnis eines fertigen Auftrags: was auf der Platte liegt und wie die Pruefung ausging."""
    if "_pakete" not in auftrag:
        auffrischen(auftrag)
    pakete = auftrag["_pakete"]
    ziel = Path(auftrag["zielordner"])
    dateien = sorted({f for p in pakete for f in p["gemeldet"]})
    if any(not _unter_wurzel(ziel / f, ziel) for f in dateien):
        raise PipelineFehler("Die Engine meldet eine Datei ausserhalb des Zielordners")
    fehlend = [f for f in dateien if not (ziel / f).is_file()]
    if fehlend:
        raise PipelineFehler(f"Gemeldete Datei fehlt auf der Platte: {fehlend[0]}")
    pruefungen = [x for p in pakete for x in p["pruefungen"]]
    schluss = _json(ziel / STATUS_DATEI) or {}
    abnahme = _abnahme_sicht(auftrag) or {}
    return {
        "auftrag": auftrag["id"],
        "titel": auftrag["titel"],
        "zielordner": str(ziel),
        "dateien": dateien,
        # Bestanden heisst: die Abnahme ist gruen, samt Pruefer der Aufgabe. Die eigenen
        # Pruefungen der Engine stehen nur in der Notiz.
        "pruefung": {
            "bestanden": abnahme.get("zustand") == FERTIG,
            "zustand": abnahme.get("zustand"),
            "satz": abnahme.get("satz"),
            "befehl": pruefungen[-1]["befehl"] if pruefungen else "",
        },
        "abnahme": abnahme or None,
        "notiz_engine": auftrag.get("_notiz_engine"),
        # Mit diesem Befehl startet die App im Zielordner (None: kein ganzer Entwurf, keine App).
        "start": auftrag.get("startbefehl"),
        "pakete": [{k: p[k] for k in ("paket", "titel", "zustand", "dateien", "pruefungen")} for p in pakete],
        "dauer_s": schluss.get("elapsed_seconds"),
        "bericht": f"{len(pakete)} Arbeitspaket(e), {len(dateien)} Datei(en) geschrieben.",
    }
