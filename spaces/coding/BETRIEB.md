# Dienst spaces/coding betreiben

Der Dienst ist ein Prozess mit Standardbibliothek. Er zeigt die Seite des Project Space, liefert den Stand je Stufe und startet den Erzeuger der Coding-Engine, höchstens einen zugleich. Er bindet nur an `127.0.0.1`.

## Start

Arbeitsordner ist die Wurzel von vibemind-os, also der Ordner, in dem `spaces/` liegt. Gestartet wird mit dem Python der Projektumgebung:

```
<python der Projektumgebung> -m spaces.coding.dienst --port 8163
```

- Der Port kommt aus `--port`, sonst aus `CODING_DIENST_PORT`, sonst 8163.
- Bereit ist der Dienst, wenn er auf stderr diese Zeile schreibt: `coding-dienst bereit auf http://127.0.0.1:8163`.
- Beim Start markiert er Aufträge als unterbrochen, die ein früherer Dienst nicht zu Ende gebracht hat.
- Angehalten wird er, indem man den Prozess beendet. Ein laufender Erzeuger ist ein Kindprozess. Wer den Dienst hart beendet, beendet den Erzeuger mit `taskkill /T` (Prozessbaum) mit.
- Die Engine muss erreichbar sein. Das ist `coding-engine` neben `spaces/` (Submodul), sonst der Ordner aus `CODING_ENGINE_PATH`. Ihr Zweig muss die App-Prüfung enthalten (verify_run, install_deps, Reparatur).

## Adresse für die Hülle

- Basis: `http://127.0.0.1:8163/`
- Seite: `GET /`
- Gesundheit: `GET /health` → 200, mit diesem Inhalt:
  `{"status":"ok","service":"coding-dienst","stufen":["annahme","plan","bau","pruefung","abschluss"],"schreiben":<bool>}`
  - Der Weg liest keine Datei.
  - `schreiben` sagt, ob die POST-Wege an sind.
- Lesen:
  - `GET /api/liste`
  - `GET /api/stand?auftrag=<id>`
  - `GET /api/ergebnis?auftrag=<id>`
  - Der Stand enthält je Stufe `wartet`, `laeuft`, `fertig`, `fehlgeschlagen` oder `abgebrochen`.
- Schreiben:
  - `POST /api/auftrag` mit `{"entwurf": {...}}` oder `{"aufgabe": "..."}`; dazu optional `arbeitspaket` und `ohne_npm` (Vorgabe true).
  - `POST /api/abbrechen` mit `{"auftrag": "<id>"}`.
  - Jede POST-Anfrage braucht den Kopf `X-Coding-Schluessel`, `Content-Type: application/json` und keinen `Origin`-Kopf.
- Der `Host`-Kopf muss `127.0.0.1:<port>` oder `localhost:<port>` sein, oder ein Name aus `CODING_DIENST_HOSTS` mit Port (Vorgabe `host.docker.internal`). Andere Hosts bekommen 403.

## Umgebung (nur Namen)

| Name | Pflicht | Wofür |
|---|---|---|
| `OPENFANG_API_KEY` | ja, für Läufe | Zugang zu OpenFang; geht an den Erzeuger weiter |
| `OPENFANG_URL` | nein | Vorgabe `http://127.0.0.1:4200` |
| `VIBEMIND_CODING_AGENT` | nein | Vorgabe `brain-coder` |
| `CODING_DIENST_SCHLUESSEL` | nein | Ohne diesen Wert sind die POST-Wege aus (nur Lesen) |
| `CODING_DIENST_PORT` | nein | Vorgabe 8163 |
| `CODING_DIENST_HOSTS` | nein | Weitere erlaubte Host-Namen, durch Komma getrennt |
| `CODING_DIENST_MAX_AUFRUFE` | nein | Obergrenze der Modellaufrufe je Lauf, Vorgabe 40 (wird `OPENFANG_MAX_CALLS`) |
| `CODING_DIENST_ZEITGRENZE_S` | nein | Höchste Laufzeit eines Erzeugers, Vorgabe 1800 |
| `CODING_DIENST_MIN_FREI_GB` | nein | Freier Speicher, ohne den `npm install` verweigert wird, Vorgabe 8 |
| `CODING_ENGINE_PATH` | nein | Ordner der Engine, Vorgabe `coding-engine` neben `spaces/` |
| `CODING_PIPELINE_DIR` | nein | Ablage der Aufträge, Vorgabe unter `%LOCALAPPDATA%` |
| `CODING_WORKSPACE_ROOT` | nein | Arbeitswurzel der erzeugten Projekte |
| `CODING_PIPELINE_TIMEOUT_S` | nein | Zeitgrenze einer Modellanfrage der Pipeline |

Der Erzeuger bekommt eine saubere Umgebung: die Systemvariablen von Windows, OpenFang und die Grenzen des Auftrags, sonst nichts. Andere Schlüssel aus der Umgebung des Dienstes gehen nicht an ihn weiter.

## Speicher (gemessen am 2026-11-23)

| Teil | Bedarf |
|---|---|
| Dienst im Leerlauf, nach 200 Abfragen | 17 MB privat, Spitze 27 MB Arbeitssatz |
| Erzeuger während eines Laufs (run_generation, Beispiel-Entwurf mit express, 277 s) | Spitze 46 MB privat, alle 2 s gemessen. Kurze node-Prozesse für Tests und Start dauern unter 2 s und fallen durch dieses Raster |
| Ganzer Lauf, Systemsicht | vorher 8,5 GB frei, danach 9,2 GB frei: kein dauerhafter Verbrauch |
| `npm install` | nur mit `ohne_npm=false`. Die Engine verweigert ihn, solange weniger als `CODING_DIENST_MIN_FREI_GB` frei ist (CommitLimit minus CommittedBytes) |

## Was eine geplante Aufgabe prüfen sollte

1. Nach dem Start bis zu 10 s auf `GET /health` = 200 warten. Die erste Antwort kam in der Messung nach 61 ms.
2. Danach genügt `GET /health` als Lebenszeichen. Die Seite fragt alle 2 s `/api/liste` und `/api/stand`.
3. Vor dem Start nichts auf 8163 laufen lassen. Der Dienst startet nicht, wenn der Port belegt ist.
