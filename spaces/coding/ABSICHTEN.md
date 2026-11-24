# Project Space (Coding): Absichten für das Verzeichnis des Gehirns

Stand: 23.11.2026. Lieferung der Coding-Sitzung an die Koordination, in der Form von
`config/space_agent_registry.yml`. Eintragen kann nur die Koordination; die Ideen-Sitzung baut das
Routing und nimmt den Block auf. Nichts hier ändert `brain/` oder `config/`.

Der Dienst ist `spaces/coding/dienst.py` (Betrieb: `spaces/coding/BETRIEB.md`), lokal unter
`http://127.0.0.1:8163`. Lesen geht immer; Schreiben nur, wenn der Dienst mit
`CODING_DIENST_SCHLUESSEL` gestartet ist und der Aufrufer ihn im Kopf `X-Coding-Schluessel` mitschickt.

## Vorschlag für den Block `coding`

```yaml
coding:
  agent: brain-coder
  prefixes: [code.]
  keywords: ["code-status|läufe|lauf|entwurf bauen|baue den entwurf|project space"]
  enabled: true
  description: "Project Space: baut Entwürfe zu startbaren Apps und zeigt den Stand der Läufe."
  mcp_servers: []
  events:
    code.status: { tool: "http:GET:http://127.0.0.1:8163/api/stand",    required_params: [] }
    code.list:   { tool: "http:GET:http://127.0.0.1:8163/api/liste",    required_params: [] }
    code.show:   { tool: "http:GET:http://127.0.0.1:8163/api/stand",    required_params: [],
                   then: "http:GET:http://127.0.0.1:8163/api/ergebnis" }   # nur wenn zustand == fertig
    code.dienst: { tool: "http:GET:http://127.0.0.1:8163/health",       required_params: [] }
    code.build:  { tool: "http:POST:http://127.0.0.1:8163/api/auftrag", required_params: [entwurf],
                   requires_confirmation: true, result_validation: external_receipt,
                   headers_from_env: { X-Coding-Schluessel: CODING_DIENST_SCHLUESSEL } }
```

`then` und `headers_from_env` gibt es im Verzeichnis heute nicht. Ohne `then` reicht für `code.show`
auch nur `/api/stand`, der Satz nennt dann keinen Startbefehl. Ohne `headers_from_env` kann der
HTTP-Ausführer des Gehirns (`HttpExecutor` in `capability_targets.py`) keinen Schlüssel mitschicken;
`code.build` antwortet dann mit 401/403, und der Satz lautet „Der Coding-Dienst nimmt gerade keine
Aufträge an.“

## Lesend, fester Aufruf, kein Modell

Die Sätze macht `spaces/coding/absichten.py` (`antwort(absicht, antworten, status)`) aus den
JSON-Antworten; die Tests stehen in `spaces/coding/tests/test_absichten.py`. Die festen Aufrufe:

| Absicht | Beispielsätze | Fester Aufruf | Antwortsatz (Beispiele) |
|---|---|---|---|
| `code.status` | „Wie ist der Code-Status?“ / „Was macht der Project Space gerade?“ / „Läuft noch ein Bau?“ | `http:GET:http://127.0.0.1:8163/api/stand` | „„Leseliste“ läuft gerade: Bau, Paket 3 von 7.“ / „„Leseliste“ ist gescheitert. Prüfung: Paket 1 von 7 „Bücher eintragen“ ist gescheitert. …“ |
| `code.list` | „Welche Läufe gibt es?“ / „Zeig mir die letzten Läufe.“ / „Was wurde zuletzt gebaut?“ | `http:GET:http://127.0.0.1:8163/api/liste` mit `grenze=5` | „Es gibt 9 Läufe, die neuesten: „A“ (fertig), „B“ (läuft).“ |
| `code.show` | „Zeig den letzten Lauf.“ / „Wie ist der letzte Lauf ausgegangen?“ / „Wie starte ich die App?“ | `http:GET:http://127.0.0.1:8163/api/stand`, bei `fertig` danach `http:GET:http://127.0.0.1:8163/api/ergebnis` | „„Leseliste“ ist fertig nach 12 Minuten. Gebaut wurden 7 Pakete mit 3 Dateien. Die unabhängige Abnahme ist bestanden. Gestartet wird die App mit „npm start“ in ihrem Ordner.“ |
| `code.dienst` | „Läuft der Coding-Dienst?“ / „Ist der Project Space erreichbar?“ | `http:GET:http://127.0.0.1:8163/health` | „Der Coding-Dienst läuft und zeigt nur an und nimmt keine Aufträge an.“ |

Gemeinsam für alle vier:

- Kein Lauf vorhanden (404): „Im Project Space gibt es noch keinen Lauf.“
- Keine Verbindung: „Der Dienst des Project Space antwortet nicht.“
- `fehler` im Stand ist schon ein deutscher Satz des Dienstes (Paket, Stufe, Grund); die rohe Meldung
  der Engine steht getrennt in `fehler_grund` und gehört nicht in die Antwort.
- Keine Kennungen im Satz: Titel, Stufe, „Paket i von n“, Dauer, Startbefehl.

## Schreibend: angenommener Auftrag

| Absicht | Beispielsätze | Aufruf | Antwortsatz |
|---|---|---|---|
| `code.build` | „Baue den Entwurf Leseliste.“ / „Bau mir die App aus dem Entwurf Aufgabenliste.“ | `http:POST:http://127.0.0.1:8163/api/auftrag` mit `{"entwurf": <Entwurf>, "ohne_npm": true}` | „„Leseliste mit Seite“ ist angenommen: 7 Pakete, 15 bis 46 Modellaufrufe. Der Bau läuft.“ (`angenommen_satz`) |

- Der Name aus dem Satz wird vorher zu einem ganzen Entwurf im Format `vibemind.entwurf/1`
  aufgelöst (aus dem Ideen-Space, wo Entwürfe entstehen). Das ist ein Lesen ohne Modell. Ohne
  gefundenen Entwurf wird nichts gesendet.
- Der Dienst nimmt nur an und antwortet sofort; der Bau läuft im Hintergrund (Erzeuger der Engine
  über OpenFang, Agent `brain-coder`). Den Fortgang liefern `code.status` und `code.show`.
- Bestätigung vor dem Senden (`requires_confirmation`): Ein Bau kostet Modellaufrufe aus dem
  Tagesbudget von `brain-coder`; die Zahl steht im Antwortsatz.
- Nachprüfung am echten Zustand: `code.status` nach dem Senden, nicht der Text des Agenten.
- Abbrechen ist `POST /api/abbrechen` (`{"auftrag": <id>}`); als Absicht `code.cancel` erst, wenn
  die Koordination es will.

## Was aus dem heutigen Block ersetzt wird

Der heutige Block `coding` zeigt auf einen Dienst, den es so nicht mehr gibt: Die Ziele
`coding-engine:POST:/api/v1/jobs` und die übrigen `coding-engine:`-Wege laufen im Rundgang ins Leere
(„coding-api Name or service not known“). Zu ersetzen:

| heute | ersetzt durch |
|---|---|
| `code.generate` (`coding-engine:POST:/api/v1/jobs`) | `code.build` |
| `code.modify` (`coding-engine:POST:/api/v1/jobs`) | entfällt; eine Änderung ist ein neuer Entwurf oder ein Arbeitspaket (`arbeitspaket` im Auftrag) |
| `code.status` (`coding-engine:GET:/api/v1/jobs/{job_id}/status`) | `code.status` (neues Ziel) |
| `code.show` (`coding-engine:GET:/api/v1/jobs/{job_id}/results`) | `code.show` (neues Ziel) |
| `code.list` (`coding-engine:GET:/api/v1/jobs`) | `code.list` (neues Ziel) |
| `code.cancel` (`coding-engine:POST:/api/v1/jobs/{job_id}/cancel`) | später `POST /api/abbrechen`, s. o. |
| `code.preview.start`, `code.preview.stop` | entfallen; die App startet mit ihrem Startbefehl, `code.show` nennt ihn |
| `mcp_servers: [vibemind-db, filesystem, git, github, …]` | leer: die festen Aufrufe gehen über HTTP, `brain-coder` arbeitet nur im Erzeuger |

## Was die Koordination dafür tun muss

1. Block oben in `config/space_agent_registry.yml` eintragen (nach Entscheidung über `then` und
   `headers_from_env`).
2. Den Dienst dauerhaft starten (geplante Aufgabe, `BETRIEB.md`); für `code.build` mit
   `CODING_DIENST_SCHLUESSEL` und `OPENFANG_API_KEY`, sonst nur lesend.
3. Läuft das Gehirn in einem Container, heißt der Dienst dort `host.docker.internal:8163`; der Dienst
   lässt diesen Namen im Host-Kopf zu (`CODING_DIENST_HOSTS`).
