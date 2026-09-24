"""Vorlagen-Arbeiter: holt Terminkarten-Auftraege ab und legt Entwuerfe vor.

Kein Agent, kein Routinelauf: ein fester Arbeiter fragt jede Minute die
Datenbank, und nur wenn ein Auftrag wartet, entsteht genau ein Modellaufruf
(formular_entwurf.entwerfen). Gestartet von marketing-dienste-starten.ps1;
der Gesundheits-Port 8131 ist das Zeichen „laeuft" fuer dieses Skript.

Controller ruling 22 (Task 6, 2026-09-24-terminkarten):
  1) ein_durchlauf faengt JEDE Ausnahme aus entwerfen() oder aus dem
     vorlegen-Datenbankaufruf ab und stellt den Auftrag ueber
     vorlagenauftrag_zurueckstellen() zurueck, mit einem deutschen
     Fehlertext, der den Ausnahmetyp nennt. Ohne das bliebe der Auftrag bei
     jeder unerwarteten Ausnahme (Netzwerkfehler, kaputte CLI, ...) fuer
     immer auf 'in_arbeit' stehen - kein anderer Codepfad greift eine
     'in_arbeit'-Zeile je wieder auf.
  2) Migration 047 (marketing.vorlagenauftraege_wiederaufnehmen) faengt den
     verbleibenden Fall auf, den kein try/except im Prozess selbst decken
     kann: der Arbeiter stirbt hart (Kill, Rechner aus) zwischen
     uebernehmen() und zurueckstellen()/vorlegen(). Eine solche Zeile bleibt
     sonst fuer immer 'in_arbeit' liegen.
  3) Deshalb ruft jeder Durchlauf zuerst vorlagenauftraege_wiederaufnehmen
     auf (liegen gebliebene 'in_arbeit'-Zeilen aelter als 15 Minuten werden
     zurueckgestellt), ERST DANACH vorlagenauftrag_uebernehmen().
"""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from spaces.marketing.claw import formular_entwurf
from spaces.marketing.sync import _db

PORT = 8131
TAKT_S = 60
WIEDERAUFNAHME_ALTER = "15 minutes"
STAND = {"letzter_lauf": None, "letztes_ergebnis": None}


def ein_durchlauf(db=_db, entwerfen=formular_entwurf.entwerfen) -> str:
    lit = db._sql_literal
    # Ruling 22.3: liegen gebliebene 'in_arbeit'-Zeilen zuerst zurueckholen,
    # bevor ueberhaupt ein neuer Auftrag uebernommen wird.
    db.query_via_docker(
        "select marketing.vorlagenauftraege_wiederaufnehmen("
        f"{lit(WIEDERAUFNAHME_ALTER)}::interval)")
    zeilen = db.query_via_docker("select * from marketing.vorlagenauftrag_uebernehmen()")
    if not zeilen:
        return "leer"
    auftrag = zeilen[0]
    fehler = ""
    try:
        gestalt, fehler = entwerfen(auftrag)
        if gestalt is not None:
            r = db.query_one(
                "select marketing.vorlagenauftrag_vorlegen("
                f"{lit(auftrag['id'])}::uuid, {lit(json.dumps(gestalt, ensure_ascii=False))}::jsonb)"
                " as ergebnis")
            ergebnis = (r or {}).get("ergebnis") or {}
            if ergebnis.get("ok"):
                return "vorgelegt"
            fehler = ergebnis.get("grund") or "Die Datenbank hat den Entwurf abgewiesen."
    except Exception as e:  # noqa: BLE001 - Ruling 22.1: nie in_arbeit stehenlassen
        fehler = f"Unerwarteter Fehler beim Entwerfen/Vorlegen ({type(e).__name__}): {e}"
    db.query_one(
        "select marketing.vorlagenauftrag_zurueckstellen("
        f"{lit(auftrag['id'])}::uuid, {lit(fehler)}) as ergebnis")
    return "zurueckgestellt"


class _Gesundheit(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        rumpf = json.dumps(STAND, default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(rumpf)

    def log_message(self, *_):
        pass


def main() -> None:
    threading.Thread(target=HTTPServer(("127.0.0.1", PORT), _Gesundheit).serve_forever,
                     daemon=True).start()
    while True:
        try:
            STAND["letztes_ergebnis"] = ein_durchlauf()
        except Exception as e:  # noqa: BLE001 - ein Fehler darf die Schleife nicht toeten
            STAND["letztes_ergebnis"] = f"fehler: {type(e).__name__}: {e}"[:300]
        STAND["letzter_lauf"] = time.strftime("%Y-%m-%d %H:%M:%S")
        print(STAND, flush=True)
        time.sleep(TAKT_S)


if __name__ == "__main__":
    main()
