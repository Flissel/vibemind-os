# VibeMind-Spaces für Rowboat (Lese-Karten, keine Agenten)

Hier liegt je aktivem Space mit Agent genau eine Karte `<space>.md`. Erzeugt wird sie von
`spaces/rowboat/scripts/agenten_verzeichnis.py` aus `config/space_agent_registry.yml`. Die Beispielsätze
kommen aus `brain/the_brain/data/capabilities.yaml`. Von Hand nichts ändern:

    python spaces/rowboat/scripts/agenten_verzeichnis.py            # neu schreiben
    python spaces/rowboat/scripts/agenten_verzeichnis.py --pruefen  # Exit 1, wenn veraltet

Die App zeigt die Karten in der Ansicht **VibeMind-Spaces** (Seitenleiste, unter „Integrationen“). Die
Ansicht liest nur. Sie hat keinen Knopf zum Ausführen oder Einschalten; ein Klick auf einen Beispielsatz
kopiert ihn in die Zwischenablage. Ausgeführt wird über OpenFang durch das Gehirn; Aufträge gehen über den
VibeMind-Chat.

## Kartenformat

Der YAML-Kopf hat eigene Felder:

| Feld | Inhalt |
|---|---|
| `space` | Kennung des Space |
| `agent` | Agent des Space |
| `titel` | Anzeigename |
| `rolle` | Rolle in einem deutschen Satz |
| `stand` | `läuft` oder `noch nicht angeschlossen` |
| `beispiele` | zwei bis vier Sätze, nur bei `läuft` |
| `geplant` | ein Satz, nur bei `noch nicht angeschlossen` |

`läuft` steht nur bei Spaces mit einem festen Weg, der heute nachweislich geht: Ideen, Bubbles und Desktop
(Lesen). Die Liste steht in `LAUFENDE_SPACES` im Erzeuger. Der Test verlangt dazu ein Ereignis mit `execution`
in der Registry.

**Kein Rowboat-Agent:** Kein Feld des Agenten-Schemas (`name`, `instructions`, `tools`, `provider`, `model`,
`description`). Zielordner ist `WorkDir/vibemind-spaces/`, nicht `WorkDir/agents/`. `FSAgentsRepo` (der
`loadAgent`-Weg) liest nur `WorkDir/agents/**/*.md`. Selbst wenn eine Karte versehentlich dort landete, schlüge
die Schema-Prüfung fehl, weil `name` Pflicht ist. Das belegt `test_loadagent_findet_keine_karte`.

## Tests

- **Erzeuger** (`spaces/rowboat/tests/test_agenten_verzeichnis.py`):
  - Jeder aktive Space mit Agent hat genau eine Karte, und es gibt keine Karte ohne Space.
  - Der Ordner ist aktuell.
  - Es gibt keine Agenten-Felder, Adressen oder Schlüssel.
  - `loadAgent` findet keine Karte.
  - Die Stand-Regeln gelten.
- **Leser und Ansicht** (im Rowboat-Fork, Branch `vibemind`: `apps/x/apps/renderer/src/components/vibemind-spaces/*.test.ts(x)`,
  vitest; nach dem Sync auch unter `spaces/rowboat/rowboat/apps/x/...`):
  - Der Leser prüft den Kopf und verwirft Karten ohne Pflichtfeld.
  - Er nutzt nur die Kanäle `workspace:exists`, `workspace:readdir` und `workspace:readFile`.
  - Alle erzeugten Karten sind lesbar.
  - Die Ansicht hat keinen Knopf außer „Satz kopieren“.

## Woher die Ansicht ihre Daten bekommt

Die Ansicht nutzt die vorhandenen Lese-Kanäle `workspace:exists`, `workspace:readdir` und
`workspace:readFile`. Es gibt keinen neuen Kanal, kein neues Schreibrecht und keine Änderung an Main oder
Server.

- **Lokal** (ohne Zentrale): Der Hauptprozess liest `~/.rowboat/vibemind-spaces/`. `workspace.ts` lässt nur
  Pfade unter dem WorkDir zu, ohne `..`.
- **Fernbetrieb** (PC-App als Client der Zentrale):
  - `invokeIpcChannel` leitet diese Kanäle an `POST <Zentrale>/rpc/<kanal>` weiter (`apps/main/src/ipc.ts`,
    `rpc-forwarder.ts`).
  - Sie stehen schon in der Liste `RPC_CHANNELS` (`apps/server/src/channels.ts`).
  - Die Ansicht liest also den WorkDir **der Zentrale** mit dem Schlüssel des Besitzers, den die App ohnehin
    hat. Eine Kopie am PC unter `~/.rowboat` wäre in diesem Betrieb unsichtbar.

## Ausliefern (macht die Koordination bzw. der Betreiber; hier nichts ausgeführt)

1. **Karten auf die Zentrale:** die `*.md` dieses Ordners (ohne `README.md`) nach
   `<WorkDir der Zentrale>/vibemind-spaces/` kopieren, auf demselben Weg wie beim Umzug von `agents/` und
   `bg-tasks/`.
   - Das ist ein neuer, reiner Leseordner, den kein Dienst ausführt. Ein Neustart der Zentrale ist nicht
     nötig, weil die Ansicht bei jedem Öffnen frisch liest.
   - Bei Änderungen an Registry oder Fähigkeiten: Erzeuger laufen lassen und erneut kopieren. Der
     Wissens-Spiegel gleicht nur `knowledge/` ab; soll er das übernehmen, braucht er `vibemind-spaces/` als
     zweiten Pfad mit Besitzer `pc`.
2. **App am PC neu bauen:** Geändert ist nur der Renderer (`apps/x/apps/renderer`). Die Zentrale
   (rowboat-headless, `apps/server`) bleibt unverändert und muss nicht neu gebaut werden. Neu gebaut werden
   muss die Electron-App am PC, also die Einbettung, die `spaces/rowboat/scripts/start_desktop.py` startet.
   Die Ansicht kommt aus dem Fork (Branch `vibemind`) über `sync_desktop.py`, nie von Hand in die gepinnte
   Kopie `spaces/rowboat/rowboat/apps/x`. Danach `start_desktop.py --install`. Den Bau macht, wer die
   Einbettung sonst baut.
3. **Prüfen:** In der App „VibeMind-Spaces“ öffnen; erwartet sind 13 Karten, davon drei mit „läuft“. Fehlt
   der Ordner auf der Zentrale, meldet die Ansicht „Noch keine VibeMind-Spaces ausgeliefert“.

## Lücken (an die Koordination übergeben)

- **Ohne Eintrag in der Registry:**
  - swedesign (bekommt laut Koordination einen echten Eintrag)
  - thebrain (Darstellungsname des Navigator-Space `brain`)
  - clawport (Electron-Overlay; Quelle hier nicht gefunden)
  - marketing (`spaces/marketing`)
  - orders: Das ist der App-Reiter „Aufträge“ (Schedule, Research, Minibook), kein Space des Gehirns.
- **Ohne Agent:** agentfarm (Captain Cook) und event-management (Event-Bot). Beide bekommen keine Karte.
- **Rechteträger** (`rights_carrier: true`, nur Ausweis und Rechte für feste Aufrufe, kein Agent, der
  Nachrichten bearbeitet): sales und recherche. Beide bekommen keine Karte.
- **Mit Agent, aber ohne festen Weg** (Stand „noch nicht angeschlossen“): coding, research, rowboat,
  minibook, schedule, n8n, learning, video, flowzen, mirofish.
  - learning: Keine Fähigkeit führt dorthin.
  - minibook, schedule, mirofish: ohne `anchor_phrases`.
  - research: Die Sätze sind Ereignis-Kennungen und werden gefiltert.
