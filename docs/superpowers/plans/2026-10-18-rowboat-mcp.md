# Rowboat per MCP bedienen (Teil 3) – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Agenten (Claude Code, OpenFang `rowboat-chat`, Brain-Capability `rowboat_chat`, openclaw-visible, openclaude-Profile) bedienen Rowboat ueber den bestehenden MCP-Server `rowboat-ui`. Sie chatten mit Rowboat, lesen und schreiben Notizen und oeffnen Ansichten in der eingebetteten Rowboat-Ansicht der Multiverse-App.

**Architecture:**
- **MCP-Backend:** Der MCP-Server `scripts/mcp_server_rowboat_ui.py` wechselt von CDP auf den Einbettungszugang aus Teil 2: `embed.json`, Token, WebSocket, `invoke` auf Rowboats eigene Kanaele. Er braucht dafuer die Multiverse-App nicht mehr, nur ein laufendes Rowboat (`start_desktop.py --embed`).
- **Navigation:** Der Zugang bekommt die Nachricht `open-url`. Sie reicht einen gepruefte `rowboat://open?...`-Link als Push `app:openUrl` an die anderen Einbettungs-Verbindungen weiter.
- **voice:** voice holt bei diesem Push den Roarboot-Space nach vorn. Ist die Ansicht noch nicht geladen, liefert voice den Link beim Start ueber `app:consumePendingDeepLink`.

**Tech Stack:** TypeScript/Vitest (Fork `@x/server`, `apps/main`), Node `node:test` (voice), Python 3 + `mcp` + `websocket-client` + pytest mit `websockets` als Fake-Server (aeusseres Repo).

**Vorgaenger:** Spec/Plan `2026-10-17-rowboat-einbettung*` (Teil 2). Dessen Protokoll ist bindend:
- Client → Rowboat: `hello {v:1, token}` (zuerst), `invoke {id, channel, args}`, `show-app`.
- Rowboat → Client: `welcome {v:1, invokeChannels}`, `result {id, ok, result | error {message, code?}}`, `push {channel, payload}`.
- Close `4401` bei falscher Anmeldung; Grenze 64 MB.

## Repos / Worktrees / Branches

| Repo | Worktree | Branch |
|---|---|---|
| Fork | `C:\Users\User\ClaudeWork\wt-rowboat-embed` | `feat/embed-bridge` (weiter darauf, Stand `b74e2a3f` = origin/vibemind) |
| voice | `C:\Users\User\ClaudeWork\wt-voice-rowboat` | `feat/rowboat-einbettung` (weiter darauf, `38fef7f` ist in `codex/laura-video-interface-embed` gemergt) |
| aeusseres Repo | `C:\Users\User\Desktop\Vibemind_V1` | `codex/vibemind-ops-baseline` |

## Global Constraints

- **Abwaertskompatibilitaet:** Name `rowboat-ui`, Tool-Namen `rowboat_chat_send` und `rowboat_chat_read` und deren Argumente bleiben gleich.
  - `rowboat_chat_send(message, timeout_s=60, settle_s=2.5)`: `settle_s` wird angenommen und ignoriert.
  - `rowboat_chat_read(limit=10, activate_chat=True)`: `activate_chat` wird angenommen und ignoriert.
  - Alle Konsumenten laufen so ohne Konfigurationsaenderung weiter.
- **Token:**
  - Kommt nur aus `<ROWBOAT_WORKDIR | ~/.rowboat>/embed.json`.
  - Nie in Ausgaben, Logs, Fehlermeldungen oder Tool-Ergebnissen.
  - Nie in einer URL.
  - Nur `127.0.0.1`.
- **Navigation:**
  - Nur `rowboat://open?...`-Links; jeder andere Host (`action`, `oauth`, Picker) wird abgewiesen, Fehlercode `embed_bad_url`.
  - Hoechstens 4096 Zeichen.
  - Nur an die anderen Einbettungs-Verbindungen, nie an Rowboats eigenes Fenster, nie zurueck an den Absender.
- **Notizen schreiben:** nur ueber Rowboats eigenen Kanal `workspace:writeFile`; dessen `RelPath`-Pruefung gilt. Kein eigener Dateizugriff am Rowboat vorbei.
- **Rowboat laeuft nicht:** Jedes Tool liefert eine klare Meldung `Rowboat laeuft nicht (start_desktop.py --embed)` statt eines Stacktraces. Kein Tool haengt laenger als sein Timeout.
- **Git:**
  - Fork-Commits mit `ENTIRE_TEST_TTY=0`.
  - Conventional Commits mit Zeile `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
  - Nie `git stash`.
  - Nur eigene Dateien stagen; im aeusseren Repo arbeiten andere Sessions parallel.
  - Kein Push auf geteilte Branches ohne User-OK; Feature-Branches duerfen gepusht werden.

---

### Task 1: Navigation ueber den Einbettungszugang (Fork)

**Files:**
- Modify: `apps/x/apps/server/src/embed-bridge.ts` + `embed-bridge.test.ts`
- Modify: `apps/x/apps/main/src/embed-host.ts`

**Interfaces:**
- **Neue Client-Nachricht:** `{type:'open-url', id:number, url:string}`.
- **Gueltige URL:**
  - beginnt mit `rowboat://open?` oder ist exakt `rowboat://open`;
  - Laenge ≤ 4096.
- **Bei gueltiger URL:** `opts.onOpenUrl(url, sender)` wird aufgerufen und liefert die Zahl der erreichten Verbindungen. Antwort: `{type:'result', id, ok:true, result:{delivered:n}}`.
- **Bei ungueltiger URL:** `{type:'result', id, ok:false, error:{code:'embed_bad_url', message}}`, `onOpenUrl` wird nicht gerufen.
- **Neue Bridge-Methode:** `broadcastExcept(channel, payload, except: EmbedSender): number`. Sie gibt die Zahl der Empfaenger zurueck, Absender nicht mitgezaehlt.
- **`embed-host.ts`:** `onOpenUrl: (url, sender) => live.broadcastExcept('app:openUrl', { url }, sender)`. Ausdruecklich NICHT ueber `mirrorToEmbed` (app:openUrl ist ein Fenster-Kanal) und NICHT an Rowboats Fenster.

- [ ] **Step 1: Failing tests** in `embed-bridge.test.ts`:
  - (a) Gueltige URL erreicht genau die andere angemeldete Verbindung als `push app:openUrl {url}`, nicht den Absender; das Ergebnis meldet `delivered: 1`.
  - (b) `rowboat://action?type=x`, `rowboat://oauth/google/done`, `https://x` und eine URL mit 4097 Zeichen ergeben `embed_bad_url`; `onOpenUrl` wird nicht gerufen.
  - (c) Ohne andere Verbindung: `delivered: 0`.
  - (d) Vor `hello`: Close `4401`.
- [ ] **Step 2:** `cd apps/x/apps/server; npx vitest run src/embed-bridge.test.ts` → FAIL.
- [ ] **Step 3:** Implementieren:
  - Zod-Variante in `ClientMessage`.
  - Behandlung im Message-Handler, im selben Fehlerstil wie `invoke` (try/catch, `fail`).
  - `broadcastExcept`.
  - `onOpenUrl` in `EmbedBridgeOptions`, Pflicht.
  - Verdrahtung in `embed-host.ts`.
- [ ] **Step 4:**
  - `npx vitest run src/embed-bridge.test.ts` und `npm run typecheck` in `apps/server` gruen.
  - `cd apps/x; npm run deps`.
  - `cd apps/main; npx tsc --noEmit -p tsconfig.json` (nie `npx --prefix`).
  - `npm run build` mit `$env:npm_config_script_shell='C:\Program Files\Git\bin\bash.exe'`.
- [ ] **Step 5:** Commit `feat(server): Navigation (open-url) ueber den Einbettungszugang`, Push `feat/embed-bridge`.

### Task 2: voice holt den Roarboot-Space bei Navigation nach vorn

**Files:**
- Modify: `electron-app/rowboat-embed-view.js` + Test
- Modify: `electron-app/rowboat-manager.js` + `overlay-managers.test.js`
- Modify: `electron-app/main.js` (Verdrahtung)

**Interfaces:**
- **`attachEmbedIpc(webContents, client, channels, { onBrowserPane, consumePendingUrl })`:** Fuer den Kanal `app:consumePendingDeepLink` gilt: Liefert `consumePendingUrl()` eine URL, antwortet voice lokal mit `{ url }`, ohne Weiterleitung. Sonst wird wie bisher an Rowboat weitergereicht.
- **`RowboatManager`:**
  - Hoert auf `client.on('push')` fuer den Kanal `app:openUrl` und ruft `onNavigate()` auf (Dependency: holt den Roarboot-Space nach vorn). Eine noch nicht geladene Ansicht wird angelegt bzw. geladen.
  - Merkt sich die URL als `pendingUrl`, wenn die Ansicht beim Push noch nicht verbunden geladen war (kein `_detachIpc`). `consumePendingUrl()` gibt sie genau einmal zurueck.
  - Ist die Ansicht geladen, kommt der Push ohnehin ueber `attachEmbedIpc` an; dann nichts merken.
- **`main.js`:** `onNavigate` → `handleSpaceNavigation({ target_space: 'roarboot' })`. Pruefen, dass das den Space im voice-Renderer wechselt und `show-rowboat` ausloest (Renderer-Handler fuer `space_changed`). Falls nicht, zusaetzlich `rowboatManager.show()` nach dem Ausblenden der anderen Ansichten, wie der `show-rowboat`-Handler es tut. Das Ergebnis kommt in den Report.

- [ ] **Step 1: Failing tests:**
  - (a) Push `app:openUrl` bei nicht geladener Ansicht → `onNavigate` einmal gerufen. Danach liefert `app:consumePendingDeepLink` die URL einmal lokal, beim zweiten Aufruf wird weitergereicht.
  - (b) Push bei geladener Ansicht → `onNavigate` gerufen, nichts gemerkt.
  - (c) Andere Push-Kanaele loesen `onNavigate` nicht aus.
- [ ] **Step 2:** `node --test rowboat-embed-view.test.js overlay-managers.test.js` → FAIL.
- [ ] **Step 3:** Implementieren.
- [ ] **Step 4:** `npm run test:unit` gruen (Basis 172), `node --check main.js`.
- [ ] **Step 5:** Commit `feat(voice): Rowboat-Navigation holt den Roarboot-Space nach vorn`, Push `feat/rowboat-einbettung`.

### Task 3: MCP-Server `rowboat-ui` ueber den Einbettungszugang

**Files:**
- Rewrite: `scripts/mcp_server_rowboat_ui.py`
- Create: `scripts/rowboat_embed_client.py` (Protokoll, ohne MCP; testbar)
- Create: `scripts/tests/test_rowboat_embed_client.py`, `scripts/tests/test_mcp_server_rowboat_ui.py`

**Interfaces (Client `rowboat_embed_client.py`):**
- `read_embed_info(path=None) -> dict`: prueft Felder und lebende PID; sonst `RowboatNotRunning('Rowboat laeuft nicht (start_desktop.py --embed)')`.
- `class EmbedSession` (Kontextmanager):
  - verbindet mit `ws://127.0.0.1:<port>/embed` (websocket-client, ohne Origin-Header);
  - `hello`, wartet auf `welcome` (5 s).
- Methoden:
  - `invoke(channel, args, timeout)` → Ergebnis, sonst `RowboatError(code, message)`. Pushes, die waehrend des Wartens eintreffen, werden in eine Warteschlange gelegt.
  - `open_url(url)`.
  - `wait_push(predicate, timeout)`.
- Kein Token in `repr`/`str`/Exceptions.

**Tools (MCP, `Server("rowboat-ui")`):**
- **`rowboat_chat_send(message, timeout_s=60, settle_s=2.5, session_id=None)`:**
  - ohne `session_id` → neue Sitzung (`sessions:create`), sonst bestehende;
  - `sessions:sendMessage`;
  - wartet, bis der Zug abgeschlossen ist (Push `turns:events`, ggf. `turns:subscribe` fuer Text-Deltas; Abschluss am durablen Abschluss-Ereignis bzw. per `sessions:getTurn`);
  - Ergebnis `{session_id, reply, timed_out}`.
  - Die genauen Request- und Ereignisformen liest der Implementer aus `apps/x/packages/shared/src/ipc.ts` (`sessions:*`, `turns:*`) und `@x/shared/src/turns.ts` im Fork-Worktree.
- **`rowboat_chat_read(limit=10, activate_chat=True, session_id=None)`:** ohne `session_id` die zuletzt aktive Sitzung (`sessions:list`); `[{role:'user'|'assistant', text}]` der letzten `limit` Nachrichten (`sessions:get` / `sessions:getTurn`).
- **`rowboat_chats_list(limit=20)`:** `[{session_id, title, updated}]`.
- **`rowboat_notes_list(folder='knowledge')`:** `workspace:readdir`.
- **`rowboat_note_read(path)`:** `workspace:readFile`, Text.
- **`rowboat_note_write(path, content)`:** `workspace:writeFile`.
- **`rowboat_open(target, path=None, session_id=None)`:**
  - `target` ∈ {`file`, `chat`, `graph`, `home`, `knowledge-view`, `meetings`, `email`, `bg-tasks`, `apps`, `integrations`};
  - baut `rowboat://open?type=...` mit URL-kodierten Parametern (`file` → `path`, `chat` → `runId`) und sendet `open-url`;
  - Ergebnis `{opened: true, delivered}`;
  - bei `delivered == 0` Hinweis „Multiverse-Ansicht ist nicht verbunden – Roarboot einmal oeffnen bzw. voice starten“.

**Tests (pytest, Fake-Rowboat mit `websockets` 12 in einem Thread, tmp `embed.json` mit `os.getpid()`):**
- Anmeldung: Token im `hello`, falsches Token → klare Meldung.
- `embed.json` fehlt oder PID tot → `Rowboat laeuft nicht…`.
- `invoke` liefert das Ergebnis; Fehler mit `code` werden als `RowboatError` gemeldet.
- `chat_send`: Ablauf `sessions:create` → `sendMessage` → Fake pusht Delta- und Abschluss-Ereignisse → Antworttext; Timeout → `timed_out: true`, ohne Haengen.
- `chat_read` mit Fake-Sitzung.
- `rowboat_open` baut kodierte URLs und gibt `delivered` zurueck.
- Kein Token in irgendeinem Tool-Ergebnis oder in Exception-Texten.
- Die Tool-Liste enthaelt weiterhin `rowboat_chat_send` und `rowboat_chat_read` mit den alten Argumenten.

- [ ] Step 1 Failing tests → Step 2 FAIL (`.venv\Scripts\python.exe -m pytest scripts/tests/test_rowboat_embed_client.py scripts/tests/test_mcp_server_rowboat_ui.py -q`) → Step 3 Implementieren → Step 4 gruen → Step 5 Commit `feat(mcp): rowboat-ui bedient Rowboat ueber den Einbettungszugang` (nicht pushen).

### Task 4: Live-Probe und Abnahme (nur mit User)

- [ ] **Start:** Rowboat (`start_desktop.py --embed`) und Multiverse starten (wie Teil 2).
- [ ] **Teil-2-Abnahme 3–7 mit dem User** abschliessen.
- [ ] **Live-Probe direkt gegen `scripts/rowboat_embed_client.py`** (MCP-Neuladen in Claude Code erst nach Session-Neustart):
  - `chat_send` „Antworte nur mit OK“ → Antwort;
  - `chats_list`;
  - `note_read` einer bekannten Notiz;
  - `rowboat_open(file, …)` → die Multiverse-Ansicht springt in den Roarboot-Space und zeigt die Notiz.
- [ ] **Merge (nach OK):**
  - Fork `feat/embed-bridge` → `vibemind` (fast-forward);
  - Sync nach vibemind-os;
  - voice-Merge, Pin, Laufzeit-Update;
  - aeusseres Repo bleibt auf seinem Branch.
- [ ] **Abschluss:** Memory `project_rowboat_ui_cdp.md` aktualisieren (Backend jetzt Einbettungszugang, neue Tools).
