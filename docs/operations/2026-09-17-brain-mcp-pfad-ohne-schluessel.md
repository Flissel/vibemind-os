# Der Brain-MCP-Pfad ist tot, obwohl `:4200` läuft

Gemessen am 17.09.2026. Adressiert an die Session mit dem Claim
`cc-openfang-4200` — und an jeden, der später die Frage stellt „der Daemon
läuft doch, warum geht es trotzdem nicht".

**Kurzfassung: den Daemon zu starten war nötig, aber nicht hinreichend.**

## Der Befund

`brain-core`, `brain-api` und `brain-loops` haben
`OPENFANG_URL=http://host.docker.internal:4200` gesetzt und
`OPENFANG_API_KEY` **leer** — an den laufenden Containern per `printenv`
nachgemessen, nicht aus der Stack-Datei geschlossen.

Der Code verlangt den Schlüssel aber unbedingt:

```python
# brain/the_brain/core/capability_targets.py, McpExecutor._configuration()
api_key = os.environ.get("OPENFANG_API_KEY", "").strip()
if not api_key:
    raise RuntimeError("OPENFANG_API_KEY is required for OpenFang MCP execution")
```

Dasselbe in `core/openfang_runtime_authority.py`
(`RuntimeAuthorityClient.from_environment`). Beide Stellen liegen auf dem
normalen Ausführungspfad und werfen bedingungslos.

## Was NICHT betroffen ist

Es gibt **zwei** Executoren, und nur einer ist betroffen:

- `OpenFangExecutor` (`capability_targets.py:466`) schickt Agenten-Nachrichten
  an `/api/agents/<id>/message`, liest nur `OPENFANG_URL` und braucht
  **keinen** Schlüssel. Der funktioniert.
- `McpExecutor` (`:852`) ist der MCP-Werkzeugpfad. Der ist tot.

Wer den Unterschied nicht macht, sucht den Fehler an der falschen Stelle.

## Das A/B, im laufenden Container gefahren

Derselbe Container (`vibemind_brain-core`), derselbe Aufruf, nur die
Variable unterschiedlich — gesetzt per `docker exec -e` für genau diesen
einen Aufruf, **der Dienst blieb unverändert**:

```
A  ohne Schluessel:  RuntimeError - OPENFANG_API_KEY is required for OpenFang MCP execution
B  mit Schluessel:   geht durch -> http://host.docker.internal:4200
```

Und aus demselben Container zum Daemon, mit `Authorization: Bearer`:

```
  /api/health -> 200
  /api/agents -> 200
```

**Der Daemon prüft den Wert heute nicht.** `:4200` läuft fail-open (ohne
eigenen `api_key`); jeder nicht-leere Wert genügt, um den Client-Guard zu
passieren. Das ist der Grund, warum diese Reparatur heute billig ist — und
zugleich der Grund, warum sie keine Sicherheit herstellt, sondern nur eine
Blockade löst.

## Was schon getan ist

`OPENFANG_API_KEY` ist in der Root-`.env` des äußeren Repos angelegt
(additiv, 43 Zeichen aus dem kryptographischen Zufallsgenerator, Sicherung
`.env.bak-2026-09-17`, Wert nirgends gedruckt).

## Was noch fehlt — und warum es hier nicht gemacht wurde

Der Stack reicht die Variable nur an `coding-api` und `coding-worker`
(`infra/swarm/vibemind-stack.yml:1524` und `:1585`). Die fünf
brain-Dienste — `brain-api`, `brain-core`, `brain-inference`,
`brain-learner`, `brain-loops` — referenzieren sie überhaupt nicht.

Es fehlt also je eine Zeile nach dem vorhandenen `OPENFANG_URL`, exakt nach
dem Muster, das `coding-api` schon trägt:

```yaml
      - OPENFANG_API_KEY=${OPENFANG_API_KEY:-}
```

**Die Zeilen sind vorbereitet, aber nichts ist ausgebracht.** Eine Änderung
an der Stack-Datei ist inert, bis jemand deployt; beim nächsten Deploy
starten diese fünf Dienste neu und haben den Schlüssel. Der Deploy selbst
gehört nicht in dieses Dokument: er läuft über den Launcher bzw.
`stack-deploy`, niemals über `service update` (das räumt den ganzen Stack
ab), und er ist eine Entscheidung des Betreibers.

## Zur Einordnung: der Erreichbarkeitslauf vom selben Tag

`python scripts/space_reachability.py` gegen den laufenden Stand:

```
22 von 94 Capabilities haben ein erreichbares Ziel.
61 sind unerreichbar, weil ein Dienst nicht laeuft - das ist ein Laufzustand, kein Defekt.
7 zeigen auf den FALSCHEN Dienst - das ist einer.
```

Die sieben sind alle mirofish-Capabilities: sie zeigen auf
`127.0.0.1:5001`, wo die Brain-API sitzt und höflich 404 zurückgibt. Das
Skript nennt das zu Recht den gefährlichsten der drei Zustände — im Log
sieht es wie ein Fehler des Zielsystems aus, ist aber eine falsche
Verdrahtung.

Die Erreichbarkeit *der Ziele* hat der Daemonstart tatsächlich
wiederhergestellt (rowboat 7/7, research 4/4, desktop und minibook je 1).
Was er nicht wiederhergestellt hat, ist die Fähigkeit des Brains,
**hindurch auszuführen** — das ist der Befund oben.

## Zusammenhang

Die größere Frage, in die das gehört —  wer ist eigentlich die Autorität
für Geheimnisse, und was folgt daraus für den `api_key` auf `:4200` — steht
in `docs/superpowers/specs/2026-09-16-geheimnis-autoritaet-design.md`,
Entscheidung D1. Dort ist auch gemessen, dass `:4200` heute aus drei
unabhängigen Gründen keine Credentials ausgeben kann: altes Binary,
fail-open ohne `api_key`, und kein initialisierter Tresor.
