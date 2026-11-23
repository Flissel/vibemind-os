# Desktop-Space: Was die App anzeigt

Stand: 23.11.2026, Entwurf der Desktop-Sitzung. Zur Vorlage bei Felix; für die Hülle als
Anzeige-Beschreibung (was, aus welcher Quelle, in welchem Zustand).

> **Nachtrag 23.11. (Felix):** Der Space ist kein Ziel der Navigation mehr. Er arbeitet als
> Augen im Hintergrund (`desktop_blick`, Nachprüfung `truth:desktop_sicht`), siehe
> `UEBERARBEITUNG.md` Abschnitt 10. Die Antwort trägt weiter `space: "desktop"` für die
> Standzeile; ob die App springt, regelt die Hülle. Die Teile unten gelten für eine laufende
> Desktop-Aufgabe.

## Wozu der Space da ist

Der Nutzer tippt eine Aufgabe ("öffne den Editor und schreibe ..."). `brain-desktop` führt
sie am echten Bildschirm aus. Der Space zeigt dabei drei Dinge: **das Bild**, **die Aufgabe**
und **die Schritte**. Wer zusieht, weiß jederzeit, was der Agent gerade tut und warum, und
kann ihn anhalten.

## Die Teile der Anzeige

| # | Teil | Zeigt | Quelle | Takt |
|---|---|---|---|---|
| 1 | Bild je Monitor | den echten Bildschirm, ein Feld je Monitor | `GET /api/desktop/monitors`, dann `GET /api/desktop/screenshot?monitor=N` (N ab 1) | 5 Bilder je Sekunde, wie heute |
| 2 | Aufgabenzeile | Text der Aufgabe, Zustand, Laufzeit, Marke "trocken" | `GET /api/desktop/task` → `task.aufgabe`, `task.zustand`, `task.begonnen`, `task.trocken` | 1 mal je Sekunde, solange der Space offen ist |
| 3 | Schrittliste | je Schritt: Nummer, Art, Begründung, gelungen oder nicht, Dauer | `task.schritte[]` → `nr`, `art`, `angaben.warum`, `ok`, `meldung`, `dauer_ms` | mit Teil 2 |
| 4 | Klickmarke im Bild | Ring an der Stelle des letzten Klicks | letzter Schritt mit `art: click` → `angaben.x`, `angaben.y`, minus `geometry.left/top` des Monitors | mit Teil 2 |
| 5 | Ergebnis | ein Satz zum Ausgang | `task.ergebnis` | wenn `zustand` nicht mehr `laeuft` ist |
| 6 | Anhalten | ein Knopf "Anhalten" | legt die Datei `STOP` in den Aufgaben-Ordner (siehe unten); `stopped: true` in der Antwort von `/api/desktop/task` | sofort |

Alle Adressen am eigenen Rechner: `http://127.0.0.1:8007`. Die App benutzt heute
`localhost:8007`; das kostet nach der Messung vom 20.11. bis zu 2 s je Aufruf.

## Zustände

| Zustand | Bedeutung | Anzeige |
|---|---|---|
| Dienst nicht erreichbar | `/api/health/health` antwortet nicht | "Desktop-Dienst startet" bzw. "nicht erreichbar", kein Bild |
| keine Aufgabe | `task: null` oder letzte Aufgabe beendet | Bild, Aufgabenzeile leer oder letztes Ergebnis grau |
| `laeuft` | Aufgabe offen | Aufgabenzeile hervorgehoben, Schrittliste wächst, Knopf "Anhalten" aktiv |
| `laeuft` + `trocken: false` | der Agent steuert Maus und Tastatur | dauerhaft sichtbares Banner "Der Agent steuert gerade Maus und Tastatur", bis der Zustand wechselt |
| Schritt mit `ok: null` | dieser Schritt wird gerade ausgeführt | Schritt in der Liste als "läuft" markiert |
| `laeuft` + `trocken: true` | Schritte werden gebucht, aber nicht ausgeführt | deutliche Marke "Trockenlauf: es wird nichts geklickt" |
| `fertig` | Agent meldet Erfolg | Ergebnis grün |
| `fehlgeschlagen` | Agent oder ein Schritt meldet Fehler | Ergebnis rot, der fehlgeschlagene Schritt markiert |
| `abgebrochen` | vom Nutzer oder vom Agenten beendet | Ergebnis grau |
| `abgelaufen` | nach 10 Minuten nicht abgeschlossen | Ergebnis gelb |
| angehalten | `stopped: true` | Banner "Angehalten. Es wird kein Schritt ausgeführt." mit Knopf "Freigeben" (löscht `STOP`) |

## Abnahmepunkt 6 (höchstens 2 s)

Der erste Schritt jeder Aufgabe ist der feste Aufruf `desktop_task_start` (ohne Modell,
gemessen unter 5 ms). Danach steht die Aufgabe mit `zustand: laeuft` im Aufgabenbuch.
Fragt die App einmal je Sekunde ab, ist nach höchstens rund 1 s sichtbar, dass die Aufgabe
läuft. Die Schritte des Agenten kommen später (ein Modell-Lauf dauert 7 bis 50 s).

## Der Aufgaben-Ordner

`%LOCALAPPDATA%\VibeMind\desktop-aufgaben\` (abweichend über `VIBEMIND_DESKTOP_AUFGABEN_DIR`).
Je Aufgabe eine Datei `dt-JJJJMMTT-HHMMSS-xxxxxx.json`:

```json
{
  "id": "dt-20261123-073400-f485a7",
  "space": "desktop",
  "aufgabe": "Text der Aufgabe",
  "zustand": "laeuft",
  "trocken": false,
  "begonnen": 1795415640.1,
  "beendet": null,
  "schritte": [
    {"nr": 1, "zeit": 1795415652.4, "art": "click", "angaben": {"x": 412, "y": 88, "warum": "Startknopf"},
     "ok": true, "echt": true, "meldung": "ausgeführt", "dauer_ms": 410}
  ],
  "ergebnis": null
}
```

Jeder Schritt wird vor der Ausführung gebucht (`ok: null`, `meldung: läuft`) und danach
ergänzt. `echt: true` heißt: Der Schritt ging wirklich an Maus oder Tastatur.
Getippter Text steht nie im Aufgabenbuch, nur seine Länge (`angaben.zeichen`).

Für den Ereignisweg der App (Felder Space, Auftrag, Stufe, Zustand, Ergebnis-Verweis):
Space = `desktop`, Auftrag = `id`, Stufe = Nummer und Art des letzten Schritts,
Zustand = `zustand`, Ergebnis-Verweis = `GET /api/desktop/task`.

## Was die App heute zeigt und was daran nicht stimmt

1. Sie fragt `/api/desktop/monitors` ab, bekommt 404 und nimmt einen Monitor an. Der Rechner
   hat zwei. Im Dienst behoben (noch nicht ausgerollt).
2. Sie fragt `screenshot?monitor=1` ab; der Dienst beachtet die Angabe nicht und liefert immer
   den Hauptmonitor, höchstens einmal je Sekunde neu. Im Dienst behoben (jetzt 5 je Sekunde).
3. Ein Klick auf das Bild wird an `/api/automation/click` weitergereicht. Dieser Teil des
   Dienstes meldet sich als nicht in Betrieb (`/api/automation/status`: `available: false`);
   mit einem echten Klick wurde das nicht geprüft. **Vorschlag:**
   die Weitergabe von Klicks aus dem Bild ausbauen. Ein versehentlicher Klick im Space soll
   nicht am echten Bildschirm klicken. Entscheidung bei Felix.
4. Aufgabe, Schritte, Ergebnis und Anhalten gibt es in der App noch nicht.
