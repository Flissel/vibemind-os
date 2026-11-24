"""Aus einem Entwurf ``vibemind.entwurf/1`` die Aufgabendateien der Coding-Engine bauen.

Der Erzeuger der Engine (``run_generation.py``) liest je Epic eine Datei
``<projekt>/tasks/epic-NNN-tasks.json``. Hier wird je Arbeitspaket ein Epic.
Das Format ist am Stand ``3251efa3`` der Engine abgelesen:

- Datei und Kopf: ``mcp_plugins/servers/grpc_host/epic_orchestrator.py``
  (``_load_or_generate_tasks``, ``_dict_to_task``)
- Felder einer Aufgabe: ``epic_task_generator.py`` (``class Task``)
- Aufgabenarten und wer sie ausfuehrt: ``task_executor.py``

Nichts hier startet die Engine oder ein Modell.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from spaces.coding.pipeline import ENTWURF_V1, PipelineFehler, _unter_wurzel, arbeitswurzel

# Felder, die ``_dict_to_task`` der Engine liest. ``load_epic_tasks`` baut die
# Aufgabe sogar mit ``Task(**t)``; ein unbekanntes Feld liesse den Lauf dort abstuerzen.
AUFGABEN_FELDER: frozenset[str] = frozenset(
    {
        "id", "epic_id", "type", "title", "description", "status", "dependencies",
        "estimated_minutes", "actual_minutes", "error_message", "output_files",
        "related_requirements", "related_user_stories", "requires_user_input",
        "user_prompt", "user_response", "checkpoint", "auto_retry", "max_retries",
        "retry_count", "timeout_seconds", "phase", "command", "success_criteria",
        "tested", "user_fix_instructions", "enrichment_context",
    }
)

# Arten, die diese Datei erzeugt, und wer sie in der Engine ausfuehrt
# (``TASK_SKILL_MAPPING`` in ``task_executor.py``). Nur die ersten beiden rufen ein Modell.
ARTEN_MIT_MODELL: frozenset[str] = frozenset({"api_service", "test_unit"})
ARTEN_OHNE_MODELL: Dict[str, str] = {
    "verify_unit": "BashExecutor",    # Tests ausfuehren
    "install_deps": "DepsInstaller",  # npm install, nur wenn package.json Pakete nennt
    "verify_run": "RunCheck",         # App starten, aufrufen, anhalten
}

# Die App, die am Ende steht: ein Befehl startet sie, die Laufpruefung ruft sie auf.
START_BEFEHL = "npm start"
TEST_BEFEHL_APP = "npm test"
SERVER_DATEI = "src/server.js"
APP_TEST = "test/app.test.js"
LAUF_PRUEFUNG = "GET /health -> 200"
APP_EPIC_NAME = "APP Start und Laufpruefung"
SEITE_DATEI = "public/index.html"


def mit_oberflaeche(entwurf: Dict[str, Any]) -> bool:
    """Nennt der Entwurf in ``architektur.tech_stack`` einen Bereich "Oberflaeche", baut die App eine Seite."""
    architektur = entwurf.get("architektur") if isinstance(entwurf.get("architektur"), dict) else {}
    return any(isinstance(w, dict) and str(w.get("bereich") or "").strip().lower() in ("oberfläche", "oberflaeche")
               for w in architektur.get("tech_stack") or [])

# Wie oft eine gescheiterte Pruefung mit ihrer Ausgabe an das Modell zurueckgeht.
REPARATUREN = 2


def _aufgabe(
    kennung: str, epic: str, art: str, titel: str, beschreibung: str, phase: str,
    abhaengig: List[str], anforderungen: List[str], kriterium: str = "",
) -> Dict[str, Any]:
    return {
        "id": kennung,
        "epic_id": epic,
        "type": art,
        "title": titel,
        "description": beschreibung,
        "status": "pending",
        "dependencies": abhaengig,
        "related_requirements": anforderungen,
        "phase": phase,
        "success_criteria": kriterium or None,
    }


def _beschreibung(entwurf: Dict[str, Any], paket: Dict[str, Any]) -> str:
    """Alles, was der Erzeuger fuer dieses Paket wissen muss, in einem Text."""
    nach_id = {str(a.get("id")): a for a in entwurf.get("anforderungen") or [] if isinstance(a, dict)}
    architektur = entwurf.get("architektur") if isinstance(entwurf.get("architektur"), dict) else {}
    bausteine = {str(b.get("id")): b for b in architektur.get("bausteine") or [] if isinstance(b, dict)}
    teile: List[str] = [str(paket.get("beschreibung") or paket.get("titel") or "").strip()]

    zeilen = [
        f"- {kennung}: {nach_id[kennung].get('titel', '')}. {nach_id[kennung].get('beschreibung', '')}".rstrip()
        for kennung in paket.get("anforderungen") or [] if kennung in nach_id
    ]
    if zeilen:
        teile.append("Anforderungen:\n" + "\n".join(zeilen))
    zeilen = [
        f"- {bausteine[k].get('name', k)}: {bausteine[k].get('aufgabe', '')}"
        for k in paket.get("bausteine") or [] if k in bausteine
    ]
    if zeilen:
        teile.append("Bausteine:\n" + "\n".join(zeilen))
    zeilen = [
        f"- {w.get('bereich', '')}: {w.get('wahl', '')}"
        for w in architektur.get("tech_stack") or [] if isinstance(w, dict)
    ]
    if zeilen:
        teile.append("Technik:\n" + "\n".join(zeilen))
    kriterien = [str(x) for x in paket.get("annahmekriterien") or []]
    if kriterien:
        teile.append("Annahmekriterien:\n" + "\n".join(f"- {x}" for x in kriterien))
    return "\n\n".join(t for t in teile if t)


OHNE_NPM_REGEL = (
    "Technik fuer dieses Projekt: reines JavaScript (CommonJS, require/module.exports), "
    "nur Bordmittel von Node.js. Es gibt ein package.json; trage dort keine dependencies ein und "
    "aendere es nicht. Kein TypeScript, kein Bau-Schritt."
)

MIT_PAKETEN_REGEL = (
    "Technik fuer dieses Projekt: JavaScript (CommonJS, require/module.exports) auf Node.js, kein "
    "TypeScript, kein Bau-Schritt. Ein Paket aus npm ist erlaubt, wenn es wirklich noetig ist: dann gib "
    "package.json vollstaendig mit zurueck und trage das Paket unter dependencies mit fester Version ein "
    "(ohne ^ oder ~). Die scripts in package.json bleiben, wie sie sind."
)


AUS_SATZ = "Dieser Entwurf wurde ohne Software-Design aus einem getippten Satz erzeugt."


def entwurf_aus_satz(satz: str, kennung: str, titel: str = "", erstellt_am: str = "") -> Dict[str, Any]:
    """Ein gueltiger Entwurf ``vibemind.entwurf/1`` mit genau einem Arbeitspaket aus einem getippten Satz.

    Gekennzeichnet in ``kontext.annahmen`` (``AUS_SATZ``) und an der ``run_id``
    (``satz-…``). Der Satz steht unveraendert in ``aufgabe``; das eine
    Annahmekriterium ist bewusst allgemein, weil ein Satz kein genaueres hergibt.
    """
    satz = (satz or "").strip()
    if not satz:
        raise PipelineFehler("Pflichtangabe fehlt: aufgabe oder entwurf")
    kurz = (titel or satz.splitlines()[0][:60]).strip()
    kriterium = "Die Aufgabe ist umgesetzt, und mindestens ein Test belegt das beschriebene Verhalten."
    return {
        "format": ENTWURF_V1,
        "run_id": f"satz-{kennung}",
        "titel": kurz,
        "aufgabe": satz,
        "erstellt_am": erstellt_am,
        "kontext": {"zusammenfassung": satz, "zielnutzer": [], "annahmen": [AUS_SATZ], "nicht_ziele": [],
                    "offene_fragen": []},
        "anforderungen": [{"id": "REQ-001", "art": "funktional", "titel": kurz, "beschreibung": satz,
                           "prioritaet": "muss", "annahmekriterien": [kriterium]}],
        "annahmekriterien": [{"id": "AK-001", "anforderung": "REQ-001", "text": kriterium}],
        "architektur": {
            "tech_stack": [{"bereich": "Sprache", "wahl": "JavaScript (CommonJS) auf Node.js",
                            "grund": "laeuft ohne Bau und ohne Abhaengigkeiten"},
                           {"bereich": "Tests", "wahl": "node:test und node:assert", "grund": "Bordmittel"}],
            "bausteine": [{"id": "BST-01", "name": "Loesung", "aufgabe": "setzt die Aufgabe um"}],
            "datenmodell": [],
            "schnittstellen": [],
        },
        "arbeitspakete": [{"id": "AP-01", "titel": kurz, "beschreibung": satz, "anforderungen": ["REQ-001"],
                           "bausteine": ["BST-01"], "haengt_ab_von": [], "annahmekriterien": [kriterium]}],
        "pruefung": {"muss_gedeckt": True, "anforderungen_ohne_paket": [], "bausteine_ohne_paket": [],
                     "offene_fragen": 0},
    }


def testdatei(paket_id: str) -> str:
    """Fester Ort der Testdatei eines Arbeitspakets im Weg ohne npm."""
    return f"test/{paket_id.lower()}.test.js"


def _alle_testdateien(pakete: List[Dict[str, Any]], mit_app: bool) -> List[str]:
    dateien = [testdatei(str(p.get("id") or f"AP-{n:02d}")) for n, p in enumerate(pakete, start=1)]
    return dateien + ([APP_TEST] if mit_app else [])


def _pakete(entwurf: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(entwurf, dict) or entwurf.get("format") != ENTWURF_V1:
        raise PipelineFehler(f"Erwartet wird ein Entwurf im Format {ENTWURF_V1}")
    pakete = [p for p in entwurf.get("arbeitspakete") or [] if isinstance(p, dict)]
    if not pakete:
        raise PipelineFehler("Der Entwurf hat keine Arbeitspakete")
    return pakete


def _app_beschreibung(entwurf: Dict[str, Any], regel: str) -> str:
    """Was die App am Ende kann: alle Anforderungen und Annahmekriterien des Entwurfs."""
    zeilen = [f"- {a.get('id')}: {a.get('titel', '')}. {a.get('beschreibung', '')}".rstrip()
              for a in entwurf.get("anforderungen") or [] if isinstance(a, dict)]
    kriterien = [str(k.get("text") if isinstance(k, dict) else k) for k in entwurf.get("annahmekriterien") or []]
    teile = [str(entwurf.get("aufgabe") or entwurf.get("titel") or "").strip()]
    if zeilen:
        teile.append("Anforderungen der App:\n" + "\n".join(zeilen))
    if kriterien:
        teile.append("Annahmekriterien:\n" + "\n".join(f"- {k}" for k in kriterien))
    teile.append(regel)
    return "\n\n".join(t for t in teile if t)


def aufgabendateien(entwurf: Dict[str, Any], ohne_npm: bool = False, mit_app: bool = True
                    ) -> Dict[str, Dict[str, Any]]:
    """Dateiname -> Inhalt, eine Datei je Arbeitspaket in der Reihenfolge des Entwurfs, dazu die App.

    Je Arbeitspaket: Code, eine Testdatei an festem Ort und ein Pruefschritt, der
    mit ``node --test`` alle bisherigen Testdateien einzeln genannt ausfuehrt.

    ``mit_app`` (Vorgabe): ein letztes Epic baut den Startpunkt ``src/server.js``
    und seinen Test, fuehrt alle Tests mit ``npm test`` aus und prueft mit
    ``npm start``, ob die App startet und ``GET /health`` beantwortet.

    ``ohne_npm``: keine Pakete aus dem Netz; package.json nennt nur scripts.
    Sonst darf der Code Pakete nennen; vor jeder Pruefung installiert ein
    ``install_deps``-Schritt sie (in der Engine nur mit Freigabe und genug Speicher).

    Eine gescheiterte Pruefung geht mit ihrer Ausgabe bis zu ``REPARATUREN`` Mal
    an das Modell zurueck; deshalb traegt jede Pruefung die Annahmekriterien.
    """
    pakete = _pakete(entwurf)
    regel = OHNE_NPM_REGEL if ohne_npm else MIT_PAKETEN_REGEL
    dateien: Dict[str, Dict[str, Any]] = {}
    testdateien: List[str] = []

    def epic_datei(epic: str, name: str, aufgaben: List[Dict[str, Any]]) -> None:
        dateien[f"{epic.lower()}-tasks.json"] = {
            "epic_id": epic,
            "epic_name": name,
            "tasks": aufgaben,
            "total_tasks": len(aufgaben),
            "completed_tasks": 0,
            "failed_tasks": 0,
            "progress_percent": 0.0,
            "run_count": 0,
            "last_run_at": None,
            "created_at": str(entwurf.get("erstellt_am") or ""),
            "estimated_total_minutes": 0,
        }

    def baukasten(epic: str, kopf: str, anforderungen: List[str]):
        aufgaben: List[Dict[str, Any]] = []
        vorher: List[str] = []

        def dazu(art: str, name: str, beschreibung: str, phase: str, kriterium: str = "", **weitere: Any) -> None:
            kennung = f"{epic}-{name}"
            aufgabe = _aufgabe(kennung, epic, art, f"{kopf}: {name}", beschreibung, phase,
                               list(vorher), anforderungen, kriterium)
            aufgabe["max_retries"] = 1 if art in ARTEN_MIT_MODELL else REPARATUREN
            aufgabe.update(weitere)
            aufgaben.append(aufgabe)
            vorher[:] = [kennung]

        return aufgaben, dazu

    for nummer, paket in enumerate(pakete, start=1):
        epic = f"EPIC-{nummer:03d}"
        paket_id = str(paket.get("id") or f"AP-{nummer:02d}")
        titel = str(paket.get("titel") or paket_id).strip()
        anforderungen = [str(x) for x in paket.get("anforderungen") or []]
        kriterien = "; ".join(str(x) for x in paket.get("annahmekriterien") or [])
        text = _beschreibung(entwurf, paket)
        aufgaben, dazu = baukasten(epic, f"{paket_id} {titel}", anforderungen)

        test = testdatei(paket_id)
        testdateien.append(test)
        dazu("api_service", "CODE",
             f"{text}\n\n{regel}\nSchreibe den Quelltext nach src/. Schreibe hier keine Tests.",
             "api", kriterien)
        dazu("test_unit", "TEST",
             f"Schreibe genau eine Testdatei: {test}. Sie belegt die Annahmekriterien dieses Arbeitspakets "
             "und benutzt nur node:test und node:assert. Lade den Quelltext mit require aus ../src/. "
             f"Aendere keine vorhandene Datei.\n\n{text}\n\n{regel}",
             "test", kriterien, output_files=[test])
        if not ohne_npm:
            dazu("install_deps", "DEPS", "Installiere die Pakete, die package.json nennt.", "setup",
                 "Alle genannten Pakete sind installiert")
        # Alle bisherigen Testdateien einzeln genannt: ``node --test test/``
        # behandelt den Ordner in Node 24 als eine Datei und scheitert.
        dazu("verify_unit", "VERIFY-unit", f"Die Tests belegen dieses Arbeitspaket.\n\n{text}",
             "verification", "Alle Tests bestehen", command="node --test " + " ".join(testdateien))
        epic_datei(epic, f"{paket_id} {titel}", aufgaben)

    if mit_app:
        epic = f"EPIC-{len(pakete) + 1:03d}"
        alle = [str(a.get("id")) for a in entwurf.get("anforderungen") or [] if isinstance(a, dict)]
        aufgaben, dazu = baukasten(epic, APP_EPIC_NAME, alle)
        app_text = _app_beschreibung(entwurf, regel)
        seite = mit_oberflaeche(entwurf)
        seite_regel = (f"- GET / liefert {SEITE_DATEI} als text/html; weitere Dateien unter public/ "
                       "(.css, .js) mit passendem Content-Type. Kein Pfad darf aus public/ hinausfuehren "
                       "(.. und absolute Pfade ergeben 404).\n") if seite else ""
        dazu("api_service", "SERVER",
             f"Baue den Startpunkt der App: {SERVER_DATEI}. Ein HTTP-Server, der die Funktionen aus src/ "
             "als JSON-Schnittstelle anbietet: je Anforderung mindestens ein Weg (Route), Eingaben als JSON, "
             "Antworten als JSON, Fehler mit passendem Status (400, 404). Pflicht:\n"
             "- GET /health antwortet 200 mit {\"status\":\"ok\"}.\n"
             "- Der Port kommt aus process.env.PORT (Vorgabe 3000), gelauscht wird auf 127.0.0.1.\n"
             "- Exportiere createServer(); sie lauscht nicht selbst. Gelauscht wird nur, wenn "
             "require.main === module.\n"
             + seite_regel +
             f"- Gestartet wird mit `{START_BEFEHL}` (package.json: node {SERVER_DATEI}). Aendere package.json "
             "nur, wenn du ein Paket brauchst und das erlaubt ist.\n"
             "Nutze die vorhandenen Module unter src/ und aendere sie nicht.\n\n" + app_text,
             "api", "GET /health antwortet 200; jede Anforderung ist ueber die Schnittstelle erreichbar",
             output_files=[SERVER_DATEI])
        if seite:
            dazu("api_service", "UI",
                 f"Baue die Oberflaeche der App: genau eine Datei {SEITE_DATEI} (HTML mit eingebettetem CSS und "
                 "JavaScript, kein Bau-Schritt, keine Bibliothek aus dem Netz). Sie benutzt die JSON-Schnittstelle "
                 f"aus {SERVER_DATEI} mit fetch (Pfade relativ zur Seite, z. B. fetch('/...')): sie zeigt die Daten "
                 "beim Laden an und bietet fuer jede Anforderung eine Bedienung (Formular oder Knopf). Fehler der "
                 "Schnittstelle zeigt sie als Text. Inhalte aus der Schnittstelle kommen per textContent in die "
                 "Seite, nie per innerHTML. Lies die Wege aus der vorhandenen Datei src/server.js; aendere sie "
                 f"nicht.\n\n{app_text}",
                 "frontend", "Die Seite laedt und zeigt die Daten der Schnittstelle", output_files=[SEITE_DATEI])
        testdateien.append(APP_TEST)
        dazu("test_unit", "TEST",
             f"Schreibe genau eine Testdatei: {APP_TEST}. Sie startet createServer() aus ../src/server.js auf "
             "Port 0, ruft GET /health und je Anforderung mindestens einen Weg der Schnittstelle mit fetch auf "
             + ("und prueft, dass GET / mit 200 und Content-Type text/html antwortet, "
                if seite else "") +
             "und schliesst den Server am Ende. Nur node:test, node:assert und fetch. Aendere keine vorhandene "
             f"Datei.\n\n{app_text}",
             "test", "Die Schnittstelle ist mit Tests belegt", output_files=[APP_TEST])
        if not ohne_npm:
            dazu("install_deps", "DEPS", "Installiere die Pakete, die package.json nennt.", "setup",
                 "Alle genannten Pakete sind installiert")
        dazu("verify_unit", "VERIFY-unit", f"Alle Tests des Projekts bestehen.\n\n{app_text}",
             "verification", "Alle Tests bestehen", command=TEST_BEFEHL_APP)
        dazu("verify_run", "VERIFY-run",
             f"Die App startet mit `{START_BEFEHL}` und antwortet. Der Port kommt aus process.env.PORT.\n\n"
             + app_text, "verification",
             f"Die App startet und antwortet.\n{LAUF_PRUEFUNG}" + ("\nGET / -> 200" if seite else ""),
             command=START_BEFEHL)
        epic_datei(epic, APP_EPIC_NAME, aufgaben)
    return dateien


def _kurzname(entwurf: Dict[str, Any]) -> str:
    roh = str(entwurf.get("run_id") or entwurf.get("titel") or "app").lower()
    name = "".join(z if z.isascii() and (z.isalnum() or z == "-") else "-" for z in roh).strip("-")
    while "--" in name:
        name = name.replace("--", "-")
    return (name or "app")[:50]


def paket_json(entwurf: Dict[str, Any], mit_app: bool = True) -> Dict[str, Any]:
    """package.json des erzeugten Projekts, festgelegt vor dem Lauf: ein Befehl startet, einer prueft."""
    pakete = _pakete(entwurf)
    scripts = {"test": "node --test " + " ".join(_alle_testdateien(pakete, mit_app))}
    if mit_app:
        scripts = {"start": f"node {SERVER_DATEI}", **scripts}
    inhalt: Dict[str, Any] = {
        "name": _kurzname(entwurf),
        "version": "0.1.0",
        "private": True,
        "description": str(entwurf.get("titel") or ""),
        "scripts": scripts,
    }
    if mit_app:
        inhalt["main"] = SERVER_DATEI
    return inhalt


def user_stories_md(entwurf: Dict[str, Any], mit_app: bool = True) -> str:
    """Inhalt von ``user_stories/user_stories.md``.

    Die Engine startet ohne diese Datei nicht (``epic_parser.py``,
    ``_validate_project_structure``). Je Epic eine Ueberschrift in der Form, die
    ihr Parser erwartet: ``# EPIC-001: Name``.
    """
    teile: List[str] = []
    for datei in aufgabendateien(entwurf, mit_app=mit_app).values():
        teile.append(f"# {datei['epic_id']}: {datei['epic_name']}\n\n**Status:** planned\n")
    return "\n".join(teile)


def modellaufrufe_mindestens(dateien: Dict[str, Dict[str, Any]]) -> int:
    """Untergrenze der Modellaufrufe: eine je Aufgabe mit Modell, ohne Wiederholung und Reparatur."""
    return sum(1 for d in dateien.values() for a in d["tasks"] if a["type"] in ARTEN_MIT_MODELL)


def modellaufrufe_hoechstens(dateien: Dict[str, Dict[str, Any]]) -> int:
    """Obergrenze: jede Aufgabe mit Modell plus Wiederholungen, jede Pruefung mit ihren Reparaturen."""
    return sum(1 + int(a.get("max_retries") or 0) if a["type"] in ARTEN_MIT_MODELL
               else int(a.get("max_retries") or 0)
               for d in dateien.values() for a in d["tasks"] if a["type"] != "install_deps")


def schreiben(entwurf: Dict[str, Any], projektordner: str, ohne_npm: bool = False, mit_app: bool = True,
              zielordner: Optional[str] = None) -> List[str]:
    """Schreibt Aufgabendateien, ``user_stories.md`` und das package.json des Zielordners; nur unter der Arbeitswurzel.

    Zielordner ist ``<projektordner>/out`` (wie in ``engine_huelle``), wenn nicht anders genannt.
    Ein vorhandenes package.json bleibt unberuehrt.
    """
    wurzel = arbeitswurzel()
    roh = Path(projektordner)
    projekt = roh if roh.is_absolute() else wurzel / roh
    ziel = Path(zielordner) if zielordner else projekt / "out"
    if not _unter_wurzel(projekt, wurzel) or not _unter_wurzel(ziel, wurzel):
        raise PipelineFehler("Der Projektordner muss unter der Arbeitswurzel des Project Space liegen")
    ordner = projekt.resolve() / "tasks"
    ordner.mkdir(parents=True, exist_ok=True)
    geschrieben: List[str] = []
    for name, inhalt in aufgabendateien(entwurf, ohne_npm=ohne_npm, mit_app=mit_app).items():
        datei = ordner / name
        datei.write_text(json.dumps(inhalt, ensure_ascii=False, indent=2), encoding="utf-8")
        geschrieben.append(str(datei))
    stories = projekt.resolve() / "user_stories" / "user_stories.md"
    stories.parent.mkdir(parents=True, exist_ok=True)
    stories.write_text(user_stories_md(entwurf, mit_app=mit_app), encoding="utf-8")
    geschrieben.append(str(stories))
    paket = ziel.resolve() / "package.json"
    if not paket.exists():
        paket.parent.mkdir(parents=True, exist_ok=True)
        # LF und UTF-8 ohne BOM, wie die Engine schreibt.
        paket.write_bytes((json.dumps(paket_json(entwurf, mit_app), ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        geschrieben.append(str(paket))
    return geschrieben
