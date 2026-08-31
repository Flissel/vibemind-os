# W4 — Plugin-Zugriff über MCP

> **Für agentische Ausführung:** REQUIRED SUB-SKILL — `superpowers:subagent-driven-development`
> (empfohlen) oder `superpowers:executing-plans`. Schritte sind Checkboxen.

**Ziel:** Ein Agent kann über MCP den Katalog auflisten, ein Plugin installieren, ein Tool
zum Workflow hinzufügen und den Runtime-Modus lesen.

**Spec:** `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md` und der
Master-Plan `docs/superpowers/plans/2026-08-30-rowboat-plugin-full-integration-master.md`.

---

## Was du vorher wissen musst

Der Space-MCP-Server ist **kein SDK-Server**. `vibemind-os/spaces/rowboat/mcp_server.py`
sind 184 Zeilen handgeschriebenes JSON-RPC über stdio: `main()` liest Zeilen von stdin und
schreibt JSON auf stdout. Er hat **keinerlei Authentifizierung** — die Vertrauensgrenze ist,
wer den Prozess starten darf.

Drei Eigenheiten, die den Aufwand bestimmen:

1. **Er verweigert Loopback.** `_rowboat_url` weist `127.0.0.1` und `localhost`
   ausdrücklich zurück. Gegen ein lokal laufendes Rowboat ist er damit unbenutzbar — und
   genau dort steht Rowboat in dieser Umgebung.
2. **Er hält keine Rowboat-Credential.** Er schickt ein unauthentifiziertes `HEAD`.
3. **Vier Verträge pinnen ihn auf genau ein Tool.** `test_openai_plugin_runtime_contract.py`
   behauptet, die Tool-Liste sei exakt `["rowboat_status"]` — und ein zweiter Test
   behauptet, ein Tool namens **`plugin_runtime_mode`** liefere einen Fehler. Das ist
   ausgerechnet das Tool, das dieser Workstream bauen soll. Dazu die
   VibeMind-Seite: `config/space_agent_registry.yml` listet `mcp_tools:
   { spaces-rowboat: [rowboat_status] }`, und zwei Brain-Tests pinnen dasselbe.
   Einer dieser Tests ist außerdem als **R14-Nachweis** gebunden, und der Verifizierer
   vergleicht Testnamen wörtlich.

Auf der Rowboat-Seite fehlen zwei Routen:

- **Add-Tool hat keine REST-Route.** Einziger Einstieg ist eine Server-Action. Der
  Use-Case, der eine Route bräuchte, ist `AddPluginToolUseCase`.
- **Runtime-Modus hat keinen Lesepfad.** Es gibt nur `POST`. Der einzige Leser überhaupt
  ist eine interne Funktion, die `pluginRuntime` aus dem Projekt-Dokument zieht.

## Die Entwurfsentscheidung, die diesen Plan trägt

**Keine Authentifizierung im stdio-Server bauen.** Er bekommt einen **Projekt-API-Key**;
Rowboats `authorizeProject` bindet diese Identität ohnehin fest an genau ein Projekt. Die
zerstörerischen Werkzeuge bleiben hinter OpenFangs bereits existierendem
`required_provenance: [approval_ref, cost_ref]` in der Space-Registry. Das ist das einzige
echte Tor, das es gibt — und es existiert schon.

**Kein Runtime-Modus-*Schreiben* über MCP.** Ein Cutover verlangt `migrationRecordId` und
`parityReceiptId`, die ein Agent nicht beschaffen kann; das Werkzeug könnte nur immer
`cutover_evidence_required` zurückgeben. Lesen ja, schreiben nein.

## Global Constraints

- Neues TypeScript nutzt `unknown` plus Narrowing, nie `any`. Python folgt dem Stil der
  Datei — kein SDK einziehen.
- Der gepinnte Katalog-Digest, die Policy-Version und die Schema-Version bleiben unverändert.
- Typecheck nur aus dem Paketverzeichnis: `cd spaces/rowboat/rowboat/apps/rowboat` und dort
  `npx tsc --noEmit`. `npx --prefix <dir> tsc --noEmit` prüft nichts und endet mit 0.
- Suite und Typecheck als getrennte Kommandos. Basis: 692 grün, 3 übersprungen.
- Jede Aufgabe einzeln committen.
- Vertrags-Tests werden **geändert, nicht gelöscht** — und jede Änderung wird im Commit
  begründet.

---

## Aufgabe 1: Die zwei fehlenden Rowboat-Routen

**Dateien:**
- Neu: `apps/rowboat/app/api/v1/projects/[projectId]/plugins/tools/route.ts`
- Ändern: `apps/rowboat/src/interface-adapters/controllers/` (Controller für Add-Tool)
- Ändern: `apps/rowboat/app/api/v1/projects/[projectId]/plugins/_responses.ts`
  (`ProjectItem` um `pluginRuntime` erweitern)
- Ändern: `apps/x/apps/renderer/src/lib/rowboat-plugin-api.ts` (dessen `.strict()`-Schema
  zieht sonst nicht mit)
- Test: `apps/rowboat/test/plugins/`

- [ ] **Schritt 1: Tests zuerst.** Add-Tool über die Route: mit gültigem Projekt-API-Key
      und `Idempotency-Key` gelingt sie und liefert dieselbe Antwort wie die Server-Action;
      ein Key eines fremden Projekts wird abgewiesen; ein zweiter Aufruf mit demselben
      Idempotency-Key fügt nichts doppelt hinzu.
      Für den Lesepfad: die Projekt-Plugin-Liste enthält `pluginRuntime` mit Modus und
      Revision.

- [ ] **Schritt 2: RED festhalten.**

- [ ] **Schritt 3: Add-Tool-Route bauen**, exakt nach dem Muster der bestehenden
      Plugin-Routen: `authenticate`, `authorizeProject`, `Idempotency-Key`, Fehler über
      `pluginErrorResponse`.

- [ ] **Schritt 4: Runtime-Modus lesbar machen — über die bestehende Liste.**
      `pluginRuntime` an die `ProjectItem`-Antwort hängen, **keine** neue GET-Route für
      `runtime-mode` anlegen. Grund: ein Vertragstest behauptet ausdrücklich, dass die
      Runtime-Modus-Route ein `export const POST` und **kein** `export const GET` hat. Der
      Weg über die Liste umgeht diesen Vertrag, statt ihn zu brechen.

- [ ] **Schritt 5: GREEN, Typecheck, Commit.**

---

## Aufgabe 2: Der MCP-Server bekommt eine Identität und Loopback-Freiheit

**Dateien:**
- Ändern: `vibemind-os/spaces/rowboat/mcp_server.py`
- Ändern: `vibemind-os/openfang/openfang.vibemind.toml.template`
- Ändern: `vibemind-os/brain/the_brain/tests/test_openfang_agent_manifest.py`
- Test: `vibemind-os/spaces/rowboat/tests/test_openai_plugin_runtime_contract.py`

- [ ] **Schritt 1: Tests zuerst.** `rowboat_status` verweigert weiterhin Loopback (das ist
      gepinnt und bleibt es); die neuen Plugin-Werkzeuge dürfen Loopback. Ohne
      `ROWBOAT_API_KEY` liefern die Plugin-Werkzeuge einen ehrlichen Fehler statt eines
      unauthentifizierten Aufrufs.

- [ ] **Schritt 2: Das Loopback-Verbot werkzeugbezogen machen**, nicht global aufheben.

- [ ] **Schritt 3: `ROWBOAT_API_KEY` in die gepinnte Server-Deklaration** in
      `openfang.vibemind.toml.template` aufnehmen und den Manifest-Test mitziehen, der
      diese Deklaration wörtlich prüft.

- [ ] **Schritt 4: GREEN, Commit.**

---

## Aufgabe 3: Die vier Werkzeuge

**Dateien:**
- Ändern: `vibemind-os/spaces/rowboat/mcp_server.py`
- Ändern: `vibemind-os/spaces/rowboat/tests/test_openai_plugin_runtime_contract.py`
- Ändern: `vibemind-os/config/space_agent_registry.yml`
- Ändern: `vibemind-os/brain/the_brain/tests/test_rowboat_mcp_server.py`,
  `test_rowboat_space_contract.py`
- Ändern: `apps/rowboat/src/application/services/plugin-runtime-completion.ts`
  (R14-Nachweis zeigt auf einen umbenannten Test)

- [ ] **Schritt 1: Die Vertrags-Tests umschreiben, mit Begründung im Commit.**
      Aus „die Liste ist exakt `[rowboat_status]`" wird „die Liste **enthält**
      `rowboat_status` und die vier Plugin-Werkzeuge". Der Test, der behauptet
      `plugin_runtime_mode` sei ein Fehler, beschreibt jetzt das Gegenteil — er wird
      umgeschrieben, nicht gelöscht, und der R14-Nachweis in
      `plugin-runtime-completion.ts` zieht auf den neuen Namen mit.

- [ ] **Schritt 2: RED festhalten** (`python -m pytest` auf die beiden Vertragsdateien).

- [ ] **Schritt 3: Dispatch-Tabelle statt Einzelnamensprüfung.** Die hartkodierte Prüfung
      auf `rowboat_status` durch eine Abbildung Name → Handler ersetzen. Erwartete Fehler
      weiterhin als `ToolError` werfen, damit sie als `isError` erscheinen und nicht als
      JSON-RPC-Fehler.

- [ ] **Schritt 4: Die vier Werkzeuge als dünne REST-Hüllen.**
      `plugin_catalog_list`, `plugin_install`, `plugin_tool_add`, `plugin_runtime_mode` —
      jeweils gegen die Routen aus Aufgabe 1 beziehungsweise die bestehenden, mit dem
      Projekt-API-Key im `Authorization`-Kopf. Kein Werkzeug erfindet Logik; jedes gibt die
      Antwort der Route weiter.

- [ ] **Schritt 5: Die Space-Registry mitziehen** — `mcp_tools` um die vier erweitern, und
      die zerstörerischen (`plugin_install`, `plugin_tool_add`) unter dasselbe
      `required_provenance` stellen, das `rowboat.status` schon trägt.

- [ ] **Schritt 6: GREEN über beide Testbäume, Commit.**

---

## Abschluss-Gate

- [ ] Ein Agent listet über MCP den Katalog, installiert ein Plugin, fügt ein Tool hinzu
      und liest den Runtime-Modus — nachgewiesen an einem echten Lauf, nicht an Unit-Tests.
- [ ] Ohne `ROWBOAT_API_KEY` schlagen die Plugin-Werkzeuge ehrlich fehl und rufen nichts
      unauthentifiziert auf.
- [ ] `rowboat_status` verhält sich unverändert, Loopback-Verbot inklusive.
- [ ] Jeder geänderte Vertrags-Test trägt seine Begründung im Commit; keiner wurde gelöscht.
- [ ] Der R14-Nachweis zeigt auf einen Test, den es gibt.

## Bewusst nicht in diesem Plan

- Den Server auf das MCP-SDK umschreiben.
- Authentifizierung im Server selbst.
- Ein Werkzeug, das den Runtime-Modus **schreibt** — es könnte nur
  `cutover_evidence_required` zurückgeben.
- Irgendetwas an der Migrations-Oberfläche.
- `deterministic_gateway.py` — das ist bewusst ein Ein-Werkzeug-Pfad; die neuen Werkzeuge
  laufen über den gewöhnlichen MCP-Weg.
