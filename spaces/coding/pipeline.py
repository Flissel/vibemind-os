"""Pipeline des Project Space: ein Auftrag laeuft in fuenf Stufen ueber ``brain-coder``.

Stufen: annahme -> plan -> bau -> pruefung -> abschluss.
``annahme`` und ``abschluss`` sind feste Schritte ohne Modell; ``plan``, ``bau`` und
``pruefung`` sind je ein Lauf des Agenten. Der Agentenaufruf wird hineingereicht;
ohne ihn legt ``auftrag_starten`` nur an (Zustand ``wartet``).

Die fuenf oeffentlichen Werkzeuge (``auftrag_starten``, ``stand``, ``ergebnis``,
``liste``, ``abbrechen``) sind als ``direct:spaces.coding.pipeline:<name>`` gedacht.
Der Stand liegt als eine JSON-Datei je Auftrag im Auftragsordner; die Anzeige liest
nur diesen Stand, nie den Text des Agenten.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

SPACE = "coding"
AGENT = "brain-coder"

STUFEN: tuple[str, ...] = ("annahme", "plan", "bau", "pruefung", "abschluss")
STUFEN_MIT_MODELL: tuple[str, ...] = ("plan", "bau", "pruefung")

WARTET = "wartet"
LAEUFT = "laeuft"
FERTIG = "fertig"
FEHLGESCHLAGEN = "fehlgeschlagen"
ABGEBROCHEN = "abgebrochen"
ENDZUSTAENDE: tuple[str, ...] = (FERTIG, FEHLGESCHLAGEN, ABGEBROCHEN)

AgentAufruf = Callable[[str, str], str]
Melder = Callable[[Dict[str, Any]], None]

_SPERRE = threading.RLock()


class PipelineFehler(Exception):
    """Fehler, den der Aufrufer als klare Meldung bekommt."""


class StufenFehler(Exception):
    """Eine Stufe ist gescheitert; der Text ist der Grund in einem Satz."""


# ── Ablage ─────────────────────────────────────────────────────────────────


def auftragsordner() -> Path:
    """Ordner mit einer JSON-Datei je Auftrag.

    ``CODING_PIPELINE_DIR``, sonst ``%LOCALAPPDATA%/VibeMind/coding_auftraege``
    (ohne ``LOCALAPPDATA``: im Benutzerordner). Der Stand liegt nie im Repo.
    """
    roh = os.environ.get("CODING_PIPELINE_DIR")
    if roh:
        ordner = Path(roh)
    else:
        lokal = os.environ.get("LOCALAPPDATA")
        basis = Path(lokal) if lokal else Path.home() / ".local" / "share"
        ordner = basis / "VibeMind" / "coding_auftraege"
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner


def arbeitswurzel() -> Path:
    """Einzige Wurzel, unter der Zielordner liegen duerfen (``CODING_WORKSPACE_ROOT``)."""
    roh = os.environ.get("CODING_WORKSPACE_ROOT")
    wurzel = Path(roh) if roh else auftragsordner() / "arbeit"
    wurzel.mkdir(parents=True, exist_ok=True)
    return wurzel.resolve()


def _unter_wurzel(pfad: Path, wurzel: Path) -> bool:
    """True, wenn der aufgeloeste Pfad (Symlinks gefolgt) unter der Wurzel liegt."""
    try:
        return pfad.resolve().is_relative_to(wurzel.resolve())
    except (OSError, ValueError):
        return False


def _zielordner(angabe: Optional[str], auftrag_id: str) -> Path:
    """Der Zielordner eines Auftrags; immer unter der Arbeitswurzel.

    Die Angabe kann aus einem getippten Satz stammen. Relative Angaben gelten ab
    der Wurzel; alles, was aufgeloest ausserhalb liegt (absolut, ``..``, UNC,
    Symlink nach draussen), wird abgelehnt.
    """
    wurzel = arbeitswurzel()
    if not angabe or not str(angabe).strip():
        return wurzel / auftrag_id
    roh = Path(str(angabe).strip())
    ziel = roh if roh.is_absolute() else wurzel / roh
    if not _unter_wurzel(ziel, wurzel):
        raise PipelineFehler("Der Zielordner muss unter der Arbeitswurzel des Project Space liegen")
    return ziel.resolve()


AUSGELASSEN = {".git", "node_modules", "__pycache__"}


def _bestehender_ordner(angabe: Optional[str]) -> Optional[Path]:
    """Ein vorhandener Ordner unter der Arbeitswurzel als Ausgangsstand, sonst ``None``.

    Wie beim Zielordner: relative Angaben gelten ab der Wurzel, alles ausserhalb wird abgelehnt.
    """
    if angabe is None or not str(angabe).strip():
        return None
    wurzel = arbeitswurzel()
    roh = Path(str(angabe).strip())
    ordner = roh if roh.is_absolute() else wurzel / roh
    if not _unter_wurzel(ordner, wurzel) or ordner.resolve() == wurzel:
        raise PipelineFehler("Der bestehende Ordner muss unter der Arbeitswurzel des Project Space liegen")
    if not ordner.is_dir():
        raise PipelineFehler(f"Den bestehenden Ordner gibt es nicht: {roh}")
    return ordner.resolve()


def _bestandstext(ordner: Path, grenze: int = 40) -> str:
    """Satz fuer den Entwurf: welche Dateien es schon gibt (aendern statt neu anlegen)."""
    dateien = sorted(
        p.relative_to(ordner).as_posix() for p in ordner.rglob("*")
        if p.is_file() and not AUSGELASSEN.intersection(p.relative_to(ordner).parts)
    )
    liste = ", ".join(dateien[:grenze]) + (f" und {len(dateien) - grenze} weitere" if len(dateien) > grenze else "")
    return ("Das Projekt besteht schon; der Zielordner enthaelt diese Dateien: " + (liste or "keine")
            + ". Aendere die vorhandenen Dateien, statt sie neu anzulegen, und erhalte ihr bisheriges Verhalten.")


def _pfad(auftrag_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9-]{6,64}", auftrag_id):
        raise PipelineFehler(f"ungueltige Auftragskennung: {auftrag_id!r}")
    return auftragsordner() / f"{auftrag_id}.json"


def _speichern(auftrag: Dict[str, Any]) -> None:
    auftrag["geaendert"] = time.time()
    ziel = _pfad(auftrag["id"])
    vorlaeufig = ziel.with_suffix(".tmp")
    with _SPERRE:
        # Felder mit "_" sind bei jedem Lesen neu berechnet und gehoeren nicht in die Datei.
        fest = {k: v for k, v in auftrag.items() if not k.startswith("_")}
        vorlaeufig.write_text(json.dumps(fest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(vorlaeufig, ziel)


def _auffrischen(auftrag: Dict[str, Any]) -> Dict[str, Any]:
    """Auftraege der Engine tragen keinen eigenen Stand: er wird aus ihren Dateien gelesen."""
    if auftrag.get("art") == "engine":
        from spaces.coding import engine_huelle

        engine_huelle.auffrischen(auftrag)
    return auftrag


def _laden(auftrag_id: str) -> Dict[str, Any]:
    pfad = _pfad(auftrag_id)
    if not pfad.exists():
        raise PipelineFehler(f"Auftrag {auftrag_id} gibt es nicht")
    with _SPERRE:
        return _auffrischen(json.loads(pfad.read_text(encoding="utf-8")))


def _alle() -> List[Dict[str, Any]]:
    auftraege: List[Dict[str, Any]] = []
    for pfad in auftragsordner().glob("*.json"):
        try:
            auftraege.append(_auffrischen(json.loads(pfad.read_text(encoding="utf-8"))))
        except (OSError, ValueError) as fehler:
            logger.warning("Auftragsdatei %s nicht lesbar: %s", pfad.name, fehler)
    auftraege.sort(key=lambda a: a.get("angelegt", 0), reverse=True)
    return auftraege


def _waehle(auftrag_id: Optional[str], nur: Optional[tuple[str, ...]] = None) -> Dict[str, Any]:
    """Der genannte Auftrag, sonst der neueste (wahlweise nur in bestimmten Zustaenden)."""
    if auftrag_id:
        return _laden(str(auftrag_id))
    for auftrag in _alle():
        if nur is None or auftrag.get("zustand") in nur:
            return auftrag
    raise PipelineFehler("Es gibt keinen passenden Auftrag")


# ── Agent ──────────────────────────────────────────────────────────────────


def openfang_aufruf(agent: str, nachricht: str) -> str:
    """Ein Lauf des Agenten ueber OpenFang; gibt den Antworttext zurueck."""
    basis = os.environ.get("OPENFANG_URL", "http://127.0.0.1:4200").rstrip("/")
    kopf = {
        "Authorization": f"Bearer {os.environ.get('OPENFANG_API_KEY', '')}",
        "Content-Type": "application/json",
    }
    dauer = float(os.environ.get("CODING_PIPELINE_TIMEOUT_S", "900"))

    def _hole(url: str, daten: Optional[bytes], grenze: float) -> Any:
        anfrage = urllib.request.Request(url, data=daten, headers=kopf, method="POST" if daten else "GET")
        try:
            with urllib.request.urlopen(anfrage, timeout=grenze) as antwort:  # noqa: S310 - feste lokale Adresse
                return json.loads(antwort.read().decode("utf-8"))
        except urllib.error.HTTPError as fehler:
            raise StufenFehler(f"OpenFang antwortet mit {fehler.code}") from fehler
        except (urllib.error.URLError, TimeoutError, ValueError) as fehler:
            raise StufenFehler(f"OpenFang nicht erreichbar oder Antwort unlesbar: {fehler}") from fehler

    agenten = _hole(f"{basis}/api/agents", None, 5.0)
    if isinstance(agenten, dict):
        agenten = agenten.get("agents") or []
    kennung = next((a.get("id") for a in agenten if (a.get("name") or "").lower() == agent.lower()), None)
    if not kennung:
        raise StufenFehler(f"Agent {agent} fehlt in OpenFang")
    antwort = _hole(
        f"{basis}/api/agents/{kennung}/message",
        json.dumps({"message": nachricht}).encode("utf-8"),
        dauer,
    )
    if isinstance(antwort, dict):
        for feld in ("response", "message", "content", "text", "result"):
            wert = antwort.get(feld)
            if isinstance(wert, str) and wert.strip():
                return wert
        return json.dumps(antwort, ensure_ascii=False)
    return str(antwort)


def _json_aus(text: str) -> Dict[str, Any]:
    """Das erste JSON-Objekt aus der Antwort des Agenten."""
    start = text.find("{")
    while start != -1:
        tiefe = 0
        for stelle in range(start, len(text)):
            if text[stelle] == "{":
                tiefe += 1
            elif text[stelle] == "}":
                tiefe -= 1
                if tiefe == 0:
                    try:
                        wert = json.loads(text[start : stelle + 1])
                    except ValueError:
                        break
                    if isinstance(wert, dict):
                        return wert
                    break
        start = text.find("{", start + 1)
    raise StufenFehler("Der Agent hat kein lesbares JSON geliefert")


# ── Eingabe ────────────────────────────────────────────────────────────────


ENTWURF_V1 = "vibemind.entwurf/1"


def _als_entwurf(entwurf: Any) -> Optional[Dict[str, Any]]:
    """Der Entwurf als Mapping, wenn er das Format ``vibemind.entwurf/…`` traegt; sonst ``None``."""
    if isinstance(entwurf, str) and entwurf.lstrip().startswith("{"):
        try:
            entwurf = json.loads(entwurf)
        except ValueError:
            return None
    if not isinstance(entwurf, dict) or not str(entwurf.get("format") or "").startswith("vibemind.entwurf/"):
        return None
    if entwurf["format"] != ENTWURF_V1:
        raise PipelineFehler(f"Unbekanntes Entwurfsformat {entwurf['format']}; bekannt ist {ENTWURF_V1}")
    return entwurf


def _eingabe(aufgabe: Optional[str], entwurf: Any, arbeitspaket: Optional[str] = None) -> Dict[str, Any]:
    """Vereinheitlicht getippte Aufgabe und Entwurf.

    Das Entwurfsformat ist vorlaeufig (Abstimmung mit Software-Design steht aus):
    Text oder Mapping mit ``titel``, ``aufgabe``, ``anforderungen``, ``annahmekriterien``.
    """
    if isinstance(entwurf, str) and entwurf.strip():
        try:
            gelesen = json.loads(entwurf)
        except ValueError:
            gelesen = None
        entwurf = gelesen if isinstance(gelesen, dict) else {"aufgabe": entwurf.strip()}
    if isinstance(entwurf, dict):
        text = str(entwurf.get("aufgabe") or entwurf.get("beschreibung") or aufgabe or "").strip()
        if not text:
            raise PipelineFehler("Der Entwurf nennt keine Aufgabe")
        return {
            "herkunft": "entwurf",
            "titel": str(entwurf.get("titel") or "").strip(),
            "aufgabe": text,
            "anforderungen": [str(x) for x in (entwurf.get("anforderungen") or [])],
            "annahmekriterien": [str(x) for x in (entwurf.get("annahmekriterien") or [])],
        }
    text = (aufgabe or "").strip()
    if not text:
        raise PipelineFehler("Pflichtangabe fehlt: aufgabe oder entwurf")
    return {"herkunft": "getippt", "titel": "", "aufgabe": text, "anforderungen": [], "annahmekriterien": []}


def _auftragstext(auftrag: Dict[str, Any]) -> str:
    teile = [f"Aufgabe: {auftrag['aufgabe']}", f"Zielordner: {auftrag['zielordner']}"]
    if auftrag.get("kontext"):
        teile.append("Kontext:\n" + "\n".join(f"- {x}" for x in auftrag["kontext"]))
    if auftrag["anforderungen"]:
        teile.append("Anforderungen:\n" + "\n".join(f"- {x}" for x in auftrag["anforderungen"]))
    if auftrag["annahmekriterien"]:
        teile.append("Annahmekriterien:\n" + "\n".join(f"- {x}" for x in auftrag["annahmekriterien"]))
    return "\n\n".join(teile)


# ── Stufen ─────────────────────────────────────────────────────────────────


def _stufe_plan(auftrag: Dict[str, Any], agent: AgentAufruf) -> Dict[str, Any]:
    antwort = _json_aus(
        agent(
            AGENT,
            "Stufe PLAN eines Coding-Auftrags. Schreibe noch keinen Code.\n\n"
            f"{_auftragstext(auftrag)}\n\n"
            'Antworte nur mit JSON: {"schritte": ["..."], "dateien": ["relativer/pfad"]}',
        )
    )
    schritte = [str(x) for x in (antwort.get("schritte") or [])]
    if not schritte:
        raise StufenFehler("Der Plan nennt keine Schritte")
    return {"schritte": schritte, "dateien": [str(x) for x in (antwort.get("dateien") or [])]}


def _stufe_bau(auftrag: Dict[str, Any], agent: AgentAufruf) -> Dict[str, Any]:
    plan = auftrag["stufen"]["plan"]["bericht"]
    antwort = _json_aus(
        agent(
            AGENT,
            "Stufe BAU eines Coding-Auftrags. Schreibe den Code in den Zielordner, nirgends sonst.\n\n"
            f"{_auftragstext(auftrag)}\n\n"
            "Plan:\n" + "\n".join(f"- {x}" for x in plan["schritte"]) + "\n\n"
            'Antworte nur mit JSON: {"dateien": ["absoluter/pfad jeder geschriebenen Datei"]}',
        )
    )
    dateien = [str(x) for x in (antwort.get("dateien") or [])]
    if not dateien:
        raise StufenFehler("Der Bau nennt keine geschriebene Datei")
    return {"dateien": dateien}


def _stufe_pruefung(auftrag: Dict[str, Any], agent: AgentAufruf) -> Dict[str, Any]:
    dateien: List[str] = auftrag["stufen"]["bau"]["bericht"]["dateien"]
    # Am Zustand pruefen, nicht am Text: jede gemeldete Datei muss es geben.
    ziel = Path(auftrag["zielordner"])
    fremd = [d for d in dateien if not _unter_wurzel(Path(d), ziel)]
    if fremd:
        raise StufenFehler("Der Bau meldet eine Datei ausserhalb des Zielordners")
    fehlend = [d for d in dateien if not Path(d).is_file()]
    if fehlend:
        raise StufenFehler(f"Gemeldete Datei fehlt auf der Platte: {fehlend[0]}")
    antwort = _json_aus(
        agent(
            AGENT,
            "Stufe PRUEFUNG eines Coding-Auftrags. Fuehre Tests oder einen Probelauf aus; aendere nur, "
            "was fuer einen bestandenen Lauf noetig ist.\n\n"
            f"{_auftragstext(auftrag)}\n\n"
            "Dateien:\n" + "\n".join(f"- {d}" for d in dateien) + "\n\n"
            'Antworte nur mit JSON: {"bestanden": true, "befehl": "...", "ausgabe": "letzte Zeilen"}',
        )
    )
    bericht = {
        "bestanden": antwort.get("bestanden") is True,
        "befehl": str(antwort.get("befehl") or ""),
        "ausgabe": str(antwort.get("ausgabe") or "")[-2000:],
    }
    if not bericht["bestanden"]:
        raise StufenFehler("Die Pruefung ist nicht bestanden: " + (bericht["ausgabe"][-200:] or "ohne Ausgabe"))
    return bericht


def _stufe_abschluss(auftrag: Dict[str, Any]) -> Dict[str, Any]:
    pruefung = auftrag["stufen"]["pruefung"]["bericht"]
    dateien = auftrag["stufen"]["bau"]["bericht"]["dateien"]
    ergebnis = {
        "zielordner": auftrag["zielordner"],
        "dateien": dateien,
        "pruefung": {"bestanden": pruefung["bestanden"], "befehl": pruefung["befehl"]},
        "bericht": f"{len(dateien)} Datei(en) geschrieben, Pruefung bestanden.",
    }
    auftrag["ergebnis"] = ergebnis
    return ergebnis


def _melde(melder: Optional[Melder], auftrag: Dict[str, Any], stufe: str, zustand: str) -> None:
    """Felder des Ereigniswegs der App (Auftrag Abschnitt 8a)."""
    if melder is None:
        return
    try:
        melder(
            {
                "space": SPACE,
                "auftrag": auftrag["id"],
                "stufe": stufe,
                "zustand": zustand,
                "ergebnis_verweis": auftrag["id"],
            }
        )
    except Exception as fehler:  # noqa: BLE001 - ein Melder darf den Lauf nie stoppen
        logger.warning("Ereignis fuer Auftrag %s nicht gemeldet: %s", auftrag["id"], fehler)


def ausfuehren(auftrag_id: str, agent: AgentAufruf, melder: Optional[Melder] = None) -> Dict[str, Any]:
    """Fuehrt die Stufen ``plan`` bis ``abschluss`` nacheinander aus (blockierend).

    Der Agentenaufruf wird immer uebergeben. ``openfang_aufruf`` ist absichtlich
    nirgends als Vorgabe verdrahtet, solange nicht entschieden ist, wohin und mit
    welchen Rechten ``brain-coder`` schreibt.
    """
    aufruf = agent
    for stufe in STUFEN[1:]:
        auftrag = _laden(auftrag_id)
        if auftrag["zustand"] in ENDZUSTAENDE:
            return auftrag
        eintrag = auftrag["stufen"][stufe]
        eintrag.update(zustand=LAEUFT, beginn=time.time())
        auftrag.update(stufe=stufe, zustand=LAEUFT)
        _speichern(auftrag)
        _melde(melder, auftrag, stufe, LAEUFT)
        try:
            if stufe == "plan":
                bericht = _stufe_plan(auftrag, aufruf)
            elif stufe == "bau":
                bericht = _stufe_bau(auftrag, aufruf)
            elif stufe == "pruefung":
                bericht = _stufe_pruefung(auftrag, aufruf)
            else:
                bericht = _stufe_abschluss(auftrag)
            fehler_text: Optional[str] = None
        except StufenFehler as fehler:
            bericht, fehler_text = None, str(fehler)
        except Exception as fehler:  # noqa: BLE001 - jeder Fehler endet als sichtbarer Zustand
            logger.exception("Stufe %s von Auftrag %s abgestuerzt", stufe, auftrag_id)
            bericht, fehler_text = None, f"unerwarteter Fehler: {fehler}"

        # Ein Abbruch waehrend des Laufs gewinnt gegen das Ergebnis der Stufe.
        abgebrochen = _laden(auftrag_id)["zustand"] == ABGEBROCHEN
        eintrag["ende"] = time.time()
        eintrag["dauer_s"] = round(eintrag["ende"] - eintrag["beginn"], 2)
        if abgebrochen:
            eintrag["zustand"] = ABGEBROCHEN
            auftrag["zustand"] = ABGEBROCHEN
        elif fehler_text is not None:
            eintrag.update(zustand=FEHLGESCHLAGEN, fehler=fehler_text)
            auftrag.update(zustand=FEHLGESCHLAGEN, fehler=fehler_text)
        else:
            eintrag.update(zustand=FERTIG, bericht=bericht)
            if stufe == STUFEN[-1]:
                auftrag["zustand"] = FERTIG
        _speichern(auftrag)
        _melde(melder, auftrag, stufe, eintrag["zustand"])
        if auftrag["zustand"] in ENDZUSTAENDE:
            return auftrag
    return _laden(auftrag_id)


# ── Werkzeuge ──────────────────────────────────────────────────────────────


def _sicht(auftrag: Dict[str, Any]) -> Dict[str, Any]:
    """Was Liste und Stand zeigen."""
    if auftrag.get("art") == "engine":
        from spaces.coding import engine_huelle

        return engine_huelle.sicht(auftrag)
    return {
        "auftrag": auftrag["id"],
        "titel": auftrag["titel"],
        "herkunft": auftrag["herkunft"],
        "stufe": auftrag["stufe"],
        "zustand": auftrag["zustand"],
        "angelegt": auftrag["angelegt"],
        "fehler": auftrag.get("fehler"),
        "hinweise": auftrag.get("hinweise") or [],
        "run_id": auftrag.get("run_id") or None,
        "arbeitspaket": auftrag.get("arbeitspaket"),
        "stufen": [
            {
                "stufe": name,
                "zustand": auftrag["stufen"][name]["zustand"],
                "dauer_s": auftrag["stufen"][name].get("dauer_s"),
                "fehler": auftrag["stufen"][name].get("fehler"),
            }
            for name in STUFEN
        ],
    }


def auftrag_starten(
    aufgabe: Optional[str] = None,
    entwurf: Any = None,
    zielordner: Optional[str] = None,
    titel: Optional[str] = None,
    arbeitspaket: Optional[str] = None,
    ohne_npm: bool = True,
    bestehend: Optional[str] = None,
    *,
    anstoss: Optional[Callable[[Dict[str, Any]], bool]] = None,
    agent: Optional[AgentAufruf] = None,
    melder: Optional[Melder] = None,
    im_hintergrund: bool = True,
) -> Dict[str, Any]:
    """Stufe ``annahme``: prueft die Eingabe und legt den Auftrag an.

    Ohne ``agent`` bleibt der Auftrag nach der Annahme auf ``wartet`` stehen; es
    laeuft kein Agent. Mit ``agent`` laufen die uebrigen Stufen.
    """
    auftrag_id = f"c-{time.strftime('%Y%m%d')}-{uuid.uuid4().hex[:8]}"
    quelle = _bestehender_ordner(bestehend)
    roh = _als_entwurf(entwurf)
    herkunft = "entwurf"
    if roh is None and agent is None:
        # Ein getippter Satz ohne Entwurf wird ein Entwurf mit einem Arbeitspaket.
        from spaces.coding import engine_aufgaben

        getippt = _eingabe(aufgabe, entwurf, arbeitspaket)
        herkunft = getippt["herkunft"]
        satz = getippt["aufgabe"]
        if quelle is not None:
            satz += "\n\n" + _bestandstext(quelle)
        roh = engine_aufgaben.entwurf_aus_satz(
            satz, auftrag_id, titel or getippt["titel"] or getippt["aufgabe"].splitlines()[0][:60],
            time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()))
        arbeitspaket = None
    elif quelle is not None and roh is None:
        raise PipelineFehler("bestehend gibt es nur fuer Auftraege an die Coding-Engine")
    if roh is not None:
        # Ein Entwurf geht an die Coding-Engine: Aufgabendateien schreiben, dann
        # den Erzeuger ueber genau eine Stelle anstossen. Ohne ``anstoss`` bleibt
        # der Auftrag auf ``wartet``; es startet nichts.
        from spaces.coding import engine_huelle

        neu = engine_huelle.auftrag_anlegen(roh, auftrag_id, titel=titel, arbeitspaket=arbeitspaket,
                                            ohne_npm=ohne_npm, herkunft=herkunft,
                                            projektordner=_zielordner(zielordner, auftrag_id),
                                            bestehend=quelle)
        _speichern(neu)
        _melde(melder, neu, "annahme", FERTIG)
        neu["angestossen"] = bool(anstoss(dict(neu))) if anstoss else False
        _speichern(neu)
        return _sicht(_auffrischen(neu))
    eingabe = _eingabe(aufgabe, entwurf, arbeitspaket)
    jetzt = time.time()
    if not zielordner and eingabe.get("run_id"):
        # Alle Pakete eines Entwurfs bauen in denselben Projektordner.
        zielordner = "entwurf-" + re.sub(r"[^A-Za-z0-9_-]", "_", eingabe["run_id"])[:64]
    ziel = _zielordner(zielordner, auftrag_id)
    ziel.mkdir(parents=True, exist_ok=True)
    auftrag: Dict[str, Any] = {
        "id": auftrag_id,
        "space": SPACE,
        "agent": AGENT,
        **eingabe,
        "titel": (titel or eingabe["titel"] or eingabe["aufgabe"][:60]).strip(),
        "zielordner": str(ziel.resolve()),
        "stufe": "annahme",
        "zustand": LAEUFT if agent else WARTET,
        "angelegt": jetzt,
        "ergebnis": None,
        "stufen": {name: {"zustand": WARTET} for name in STUFEN},
    }
    auftrag["stufen"]["annahme"] = {"zustand": FERTIG, "beginn": jetzt, "ende": jetzt, "dauer_s": 0.0}
    _speichern(auftrag)
    _melde(melder, auftrag, "annahme", FERTIG)
    if agent is None:
        return _sicht(auftrag)
    if im_hintergrund:
        threading.Thread(
            target=ausfuehren, args=(auftrag_id, agent, melder), name=f"coding-{auftrag_id}", daemon=True
        ).start()
        return _sicht(auftrag)
    return _sicht(ausfuehren(auftrag_id, agent, melder))


def stand(auftrag: Optional[str] = None) -> Dict[str, Any]:
    """Stand des genannten, sonst des neuesten Auftrags."""
    return _sicht(_waehle(auftrag))


def ergebnis(auftrag: Optional[str] = None) -> Dict[str, Any]:
    """Ergebnis des genannten, sonst des neuesten abgeschlossenen Auftrags."""
    gewaehlt = _waehle(auftrag, nur=(FERTIG,) if not auftrag else None)
    if gewaehlt["zustand"] != FERTIG:
        raise PipelineFehler(f"Auftrag {gewaehlt['id']} ist {gewaehlt['zustand']}, es gibt noch kein Ergebnis")
    if gewaehlt.get("art") == "engine":
        from spaces.coding import engine_huelle

        return engine_huelle.ergebnis(gewaehlt)
    return {"auftrag": gewaehlt["id"], "titel": gewaehlt["titel"], **gewaehlt["ergebnis"]}


def liste(grenze: int = 20) -> Dict[str, Any]:
    """Die neuesten Auftraege, neueste zuerst."""
    auftraege = _alle()
    return {"anzahl": len(auftraege), "auftraege": [_sicht(a) for a in auftraege[: max(1, int(grenze))]]}


UNTERBROCHEN = "unterbrochen: der Dienst wurde waehrend des Laufs neu gestartet"


def unterbrochene_schliessen() -> Dict[str, Any]:
    """Setzt jeden Auftrag im Zustand ``laeuft`` auf ``fehlgeschlagen`` (Grund: unterbrochen).

    Ein Lauf lebt nur im Prozess, der ihn gestartet hat. Nach dessen Neustart
    bliebe der Auftrag fuer immer auf ``laeuft``. Nur der Prozess, dem die Laeufe
    gehoeren, ruft das auf, und nur einmal beim Start - nie ein lesender Aufrufer,
    sonst beendet er fremde laufende Auftraege. Wartende Auftraege bleiben.
    """
    geschlossen: List[str] = []
    with _SPERRE:
        for auftrag in _alle():
            if auftrag.get("zustand") != LAEUFT or auftrag.get("art") == "engine":
                continue
            eintrag = auftrag["stufen"].get(auftrag.get("stufe", ""), {})
            if eintrag.get("zustand") == LAEUFT:
                eintrag.update(zustand=FEHLGESCHLAGEN, fehler=UNTERBROCHEN, ende=time.time())
            auftrag.update(zustand=FEHLGESCHLAGEN, fehler=UNTERBROCHEN)
            _speichern(auftrag)
            geschlossen.append(auftrag["id"])
    return {"anzahl": len(geschlossen), "auftraege": geschlossen}


def als_unterbrochen_markieren(auftrag_id: str, grund: str) -> Dict[str, Any]:
    """Haelt fest, dass der Erzeuger eines Auftrags der Engine ohne Abschluss geendet hat.

    Nur wer den Prozess des Erzeugers kennt, ruft das auf. Der Auftrag gilt dann als
    ``fehlgeschlagen`` mit diesem Grund, solange die Engine keinen Abschluss geschrieben hat.
    """
    with _SPERRE:
        auftrag = _laden(auftrag_id)
        auftrag["unterbrochen"] = f"unterbrochen: {grund}"
        _speichern(auftrag)
    return _sicht(_auffrischen(auftrag))


def abbrechen(auftrag: Optional[str] = None) -> Dict[str, Any]:
    """Bricht den genannten, sonst den neuesten laufenden oder wartenden Auftrag ab.

    Ein laufender Agentenlauf wird nicht unterbrochen; nach ihm startet keine Stufe mehr.
    """
    with _SPERRE:
        gewaehlt = _waehle(auftrag, nur=(LAEUFT, WARTET) if not auftrag else None)
        if gewaehlt["zustand"] in ENDZUSTAENDE:
            raise PipelineFehler(f"Auftrag {gewaehlt['id']} ist schon {gewaehlt['zustand']}")
        gewaehlt["zustand"] = ABGEBROCHEN
        _speichern(gewaehlt)
    return _sicht(gewaehlt)
