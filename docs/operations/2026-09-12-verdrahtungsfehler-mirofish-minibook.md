# Zwei Verdrahtungsfehler: mirofish zeigt auf die Brain, minibook auf drei Ports

**Datum:** 2026-09-12
**Anlass:** Schritt 2 des Plans („dann mit einander verdrahten"). Gefunden beim
Versuch, die verbleibenden Lücken von `mirofish` und `minibook` zu belegen.

Beide Befunde sind gemessen, nicht aus der Konfiguration abgelesen.

## 1. mirofish ruft die Brain-API auf — nicht mirofish

`MIROFISH_BASE_URL` ist **nirgends im Repo gesetzt**. Der Vorgabewert steht in
`brain/the_brain/core/capability_targets.py:1455`:

```python
os.environ.get("MIROFISH_BASE_URL", "http://127.0.0.1:5001")
```

Port 5001 ist aber belegt — von der Brain selbst:

```
docker service ls   ->  vibemind_brain-api  *:5001->5001/tcp
GET http://127.0.0.1:5001/        ->  200  "Tahlamus Production API"
GET http://127.0.0.1:5001/health  ->  200  {"status":"healthy", ...}
```

Das ist kein Zufall der Umgebung: mirofishs eigenes Backend bindet **denselben
Port** (`spaces/mirofish/mirofish/backend/run.py:41`, `FLASK_PORT` mit
Vorgabewert 5001; die compose-Datei mappt `5001:5001`). Zwei Dienste
beanspruchen denselben Port, und der Swarm-Dienst hat ihn.

**Die Folge ist schlimmer als ein toter Link.** Ein toter Link meldet
„nicht erreichbar". Hier antwortet ein *lebender, fremder* Dienst:

```
POST /api/graph/build       -> 404
POST /api/simulation/start  -> 404
POST /api/report/generate   -> 404
```

Alle drei schreibenden mirofish-Operationen laufen also gegen die Brain-API
und bekommen 404. Der `MiroFishExecutor` wirft daraufhin
`raise_for_status()` — der Fehler sieht aus wie ein Problem von mirofish,
kommt aber daher, dass nie mirofish gefragt wurde.

Nebenbefund: **`neo4j-mirofish` läuft** (Container da, Ports auf 7475/7688
umgelegt — offenbar schon einmal einer Kollision ausgewichen), das
*Backend* auf 5001 läuft nicht. Die Datenhaltung steht, der Dienst davor
fehlt.

**Nicht repariert.** Welcher der beiden Dienste umziehen soll, ist eine
Entscheidung des Betreibers, keine Messfrage. Zwei Wege:
`MIROFISH_BASE_URL` explizit auf einen freien Port setzen und mirofishs
`FLASK_PORT` mit, **oder** die Brain-API umziehen. Das Erste ist der
kleinere Eingriff; die Brain-API auf 5001 ist an mehreren Stellen verankert.

## 2. minibook hat drei verschiedene Vorgabewerte

Derselbe Dienst, drei Ports, in drei Modulen:

| Ort | Vorgabewert |
|---|---|
| `spaces/minibook/tools/minibook_tools.py` | `http://127.0.0.1:8800` |
| `voice/python/vibemind_mcp.py:764`, `voice/.env.sanitized:398` | `http://localhost:3480` |
| `coding-engine/src/services/minibook_connector.py:37` | `http://localhost:3456` |

Höchstens einer kann stimmen. Gemessen antwortet **keiner** — 8800, 3480 und
3456 sind alle zu, minibook läuft derzeit nicht. Deshalb lässt sich der
richtige Wert hier auch nicht empirisch bestimmen; ein Rateschritt wäre genau
die stille Annahme, die später niemand mehr hinterfragt.

Die Capabilities des Space (`minibook.discuss`, `.collaborate`) gehen über
`minibook_tools.py`, also über 8800. Der `truth:http_ok`-Validator von
`minibook.status` geht denselben Weg. Die beiden anderen Vorgabewerte gehören
zu Konsumenten außerhalb der Capability-Ebene.

**Zu klären, wenn minibook das nächste Mal läuft:** welcher Port es wirklich
ist, dann die drei Stellen auf eine gemeinsame Quelle ziehen.

## Was das für die Lückenliste heißt

`mirofish` (4 Lücken) und `minibook` (2) lassen sich heute **nicht** schließen,
und der Grund ist nicht der fehlende Validator. Bei mirofish wäre ein
`truth:`-Validator gegen die GET-Routen (`/api/report/{id}` usw.) genau
richtig — nur zeigen sie aufs falsche Ziel, und ein Validator auf einem
falschen Ziel wäre schlimmer als keiner: er belegte zuverlässig etwas anderes.

**Erst die Verdrahtung, dann der Beleg.** In dieser Reihenfolge, nicht
umgekehrt.

## Belege

* `docker service ls` zeigt `vibemind_brain-api *:5001->5001/tcp`.
* `GET /` auf 5001 liefert „Tahlamus Production API", `/health` ist gesund.
* Drei POSTs auf die mirofish-Pfade liefern 404.
* `docker ps` zeigt `neo4j-mirofish` laufend (7475/7688), kein
  mirofish-Backend.
* Ports 8800/3480/3456/3100 per Socket geprüft — alle zu.
* Windows' `Get-NetTCPConnection` zeigt für 5001 **keinen** Lauscher, obwohl
  die Verbindung steht: das ist das bekannte WSL-Mirrored-Verhalten, kein
  Widerspruch. Wer Ports auf dieser Maschine prüft, darf sich darauf nicht
  verlassen — ein echter Verbindungsversuch ist der Beleg.

---

## Nachtrag: die Erreichbarkeit systematisch gemessen

Die beiden Befunde oben fand ich durch Probieren. Das war Glück, also habe ich
danach **jedes** Ziel angesprochen. Werkzeug:
`scripts/space_reachability.py` (6 Tests in `scripts/tests/`).

```
Space        ok   tot  falsch  fehlt   Ursache
bubbles      11     0       0      2
coding        0     6       0      2   coding-engine :8140 laeuft nicht
desktop       0     1       0      0   OpenFang :4200 laeuft nicht
flowzen       3     0       0      0
ideas        31     1       0      0   OpenFang :4200 laeuft nicht
minibook      0     4       0      0   127.0.0.1:8800 antwortet nicht
mirofish      0     0       7      0   404 unter :5001 - dort sitzt die Brain-API
research      0     4       0      0   OpenFang :4200 laeuft nicht
rowboat       0     7       0      0   OpenFang :4200 laeuft nicht
schedule      7     0       0      0
video         8     0       0      0

60 von 94 Capabilities haben ein erreichbares Ziel.
23 sind unerreichbar, weil ein Dienst nicht laeuft - Laufzustand, kein Defekt.
 7 zeigen auf den FALSCHEN Dienst - das ist einer.
 4 haben gar kein Ziel - bewusst entfernt (bubble_noop_op).
```

**Die Zahl 60 ist weniger schlimm als sie klingt, und der Rest ist es mehr.**
Von den 34 nicht erreichbaren gehen **23 auf drei nicht laufende Dienste**
zurück — OpenFang (13 Capabilities in fünf Spaces), coding-engine (6),
minibook (4). Das ist Startzustand, kein Fehler im System. Starten würde sie
alle auf einmal umlegen.

Die **7 falschen** sind dagegen echte Arbeit, und sie sind derselbe eine
Fehler, siebenmal gezählt: mirofishs Portkollision oben.

**Der größte einzelne Hebel ist OpenFang.** Er trägt 13 der 23 — desktop,
ideas (`idea_connect`, der einzige MCP-Pfad dort), minibook (`minibook.status`),
research (alle vier) und rowboat (alle sieben).

**Ich habe ihn NICHT gestartet.** Im WORKBOARD liegt seit dem 11.09. ein
fremder Claim darauf: `cc-plugin-setup-agent` startet ausdrücklich „einen
OpenFang-Daemon aus dem neu gebauten Binary". Zwei Sessions, die denselben
Daemon hochfahren, ist genau der Fall, für den das Board da ist. Wer ihn
startet, sollte die Regeln aus dem Gedächtnis beachten: **nie die Root-`.env`
in die Daemon-Umgebung laden** (jeder Wrapper-Agent scheitert dann am Guard
mit Exit 2), Start wie der Launcher mit PATH-Prepend und `WRAPPER_DIR`.

### Eine Messfalle, die hier teuer gewesen wäre

`Get-NetTCPConnection -LocalPort 5001 -State Listen` meldete **keinen**
Lauscher, während ein Verbindungsversuch problemlos durchging. Das ist das
bekannte WSL-Mirrored-Verhalten: Dienste aus der WSL-VM erscheinen nicht in
der Windows-Portliste, antworten aber über den Loopback. Wer Ports so prüft,
hält laufende Dienste für tot — und hätte hier den mirofish-Befund genau
verkehrt herum gedeutet. Das Werkzeug verbindet deshalb wirklich; ein Test
hält diese Entscheidung fest.
