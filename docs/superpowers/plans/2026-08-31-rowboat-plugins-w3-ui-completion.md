# W3 — UI-Vervollständigung für OpenAI-Plugins

> **Für agentische Ausführung:** REQUIRED SUB-SKILL — `superpowers:subagent-driven-development`
> (empfohlen) oder `superpowers:executing-plans`. Schritte sind Checkboxen.

**Ziel:** Eine zugelassene Komponente eines nur teilweise freigegebenen Plugins lässt
sich installieren und benutzen, und ein Plugin-Tool lässt sich im Workflow-Editor
nicht mehr umbenennen oder als gemockt darstellen.

**Spec:** `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md` (Betriebsvertrag
und Nicht-Behauptungen) sowie der Master-Plan
`docs/superpowers/plans/2026-08-30-rowboat-plugin-full-integration-master.md`.

---

## Der Ertrag, bevor du Ja sagst

**Komponenten-genaue Installation schaltet genau drei ausführbare Komponenten frei.**
Gemessen an der gepinnten `config/openai-plugin-catalog.lock.json`: der Katalog enthält
acht MCP-Komponenten. Drei sind abgelehnte Prozess-MCPs, eine ist `review_required`
(figma), vier sind zugelassen und verfügbar — `cloudflare-api`, `github`, `linear`,
`notion`. Davon liegt heute nur **`linear`** auf einem installierbaren Plugin. Die
anderen drei sind genau das, was dieser Workstream freischaltet.

117 der 118 installierbaren Plugins zeigen zwar einen „Add to workflow"-Knopf, aber fast
alle sind `kind: "app"` — und die weist `resolvePluginProvider` ab, weil es keine
Connector-Bridge gibt. Das ist W2, nicht W3.

Wenn dir drei Komponenten den Aufwand nicht wert sind, ist **Aufgabe 2 allein**
(die Sperre gegen Umbenennen) trotzdem lohnend: sie schließt ein Loch, durch das ein
Aufruf still auf eine andere Operation umgebogen werden kann.

## Was aus diesem Workstream gestrichen ist

**„Credential-Slot im UI binden" entfällt.** Die Credential, die ein Aufruf tatsächlich
benutzt, kommt seit der OpenFang-Kette pro Aufruf aus `OPENFANG_ISSUABLE_CREDENTIALS`,
adressiert über einen Referenznamen aus dem gepinnten Katalog. Ein Slot im UI zu binden
würde daran **nichts** ändern: es würde ein Abzeichen im Dialog umschalten, die
Staleness-Signatur der Laufzeit vergrößern (ein Binden während eines laufenden Aufrufs
bräche ihn mit `execution_state_changed` ab) und müsste zum Execution-Claim passen. Der
Wert käme weiter von OpenFang. Es dupliziert einen bestehenden Mechanismus.

Das Einzige, was ein Slot könnte und OpenFang nicht kann, ist **projektbezogene
Indirektion** — Slot-Name → Referenzname, damit zwei Projekte verschiedene GitHub-Tokens
benutzen. `PluginCredentialSlot` ist dafür bereits geformt, aber niemand liest es, und
liefern hieße den Kernel ändern (`PluginProviderResolutionRequest` müsste Slots tragen).
Das ist ein eigener Entwurf, keine UI-Aufgabe.

Die *wahrheitsgemäße* Hälfte davon — dass der Installationsdialog heute für `linear`
„keine Credentials nötig" behauptet, obwohl der Provider eine oauth-Referenz verlangt —
wird separat gefixt und ist **nicht** Teil dieses Plans.

## Global Constraints

- Neues TypeScript nutzt `unknown` plus Narrowing, nie `any`.
- Der gepinnte Katalog-Digest, die Policy-Version und die Schema-Version bleiben
  unverändert. Eine Änderung an `packages/openai-plugin-runtime/src/import/` verschiebt
  den Digest und ist damit außerhalb dieses Plans.
- Typecheck nur aus dem Paketverzeichnis heraus: `cd spaces/rowboat/rowboat/apps/rowboat`
  und dort `npx tsc --noEmit`. Die Form `npx --prefix <dir> tsc --noEmit` gibt die Hilfe
  aus und endet mit 0, ohne irgendetwas zu prüfen.
- Suite und Typecheck als **getrennte** Kommandos ausführen.
- Basis: 692 Tests grün, 3 übersprungen (zwei davon sind die Opt-in-Live-Gates).
- Jede Aufgabe einzeln committen, `git diff --check` davor.

## Verifizierter Ist-Stand

Nicht aus dem Code gelesen, sondern gemessen:

- Das Tor ist **eine Zeile**: `assertAdmitted` in
  `apps/rowboat/src/application/use-cases/plugins/plugin-service.shared.ts:88` verlangt,
  dass *keine* Komponente eine andere Admission als `admitted` hat. Die
  Verfügbarkeitsprüfung eine Zeile darunter greift bei `github` gar nicht — alle seine
  Komponenten sind `available`, nur ihre Admission ist `review_required`.
- **Add-Tool ist bereits komponentenbezogen** und braucht keine Änderung.
- **Die Repository-Schicht kann Teilinstallationen schon**: `validateAdmissionBatch` und
  `validateProviderBindings` prüfen nur, was man ihnen gibt, gegen den Katalog-Eintrag —
  keine Abdeckung des ganzen Plugins verlangt. Bewiesen durch
  `test/plugins/live-openfang-e2e.test.ts`, das genau eine Installation und **eine**
  Admission-Zeile für githubs MCP-Komponente schreibt und darüber die volle Kette fährt.
  **Also: keine Repository-, Index- oder Laufzeitarbeit in diesem Plan.**
- Ein Plugin-Tool kann **nicht** still gemockt werden: `createTools` verzweigt auf
  `config.pluginBinding` *vor* `config.mockTool`, und ein Test pinnt das bereits. Der
  Mock-Schalter ist trotzdem sichtbar und das „Mocked"-Abzeichen erscheint — eine
  Unwahrheit im UI, kein Ausführungsfehler.
- Die **echte** Lücke ist das Umbenennen: der Tool-Name wird zu `context.operationName`
  und ist damit zugleich der MCP-Tool-Name am entfernten Server, die Eingabe des
  Read/Write-Klassifizierers und der `toolName` in der OpenFang-Freigabe. Wer umbenennt,
  biegt den Aufruf still um — und der Mensch in OpenFang liest den neuen Namen.
- `plugin_installations` ist eindeutig auf `{projectId, pluginName}`, und
  `installIdempotently` macht immer ein `insertOne`. **Es gibt keinen Update-Pfad für
  `providerBindings`.** „Erst A installieren, später B" braucht eine neue
  Repository-Operation — deshalb: die Auswahl wird einmal getroffen.

---

## Aufgabe 1: Komponenten-genaue Installation

**Dateien:**
- Ändern: `apps/rowboat/src/application/use-cases/plugins/plugin-service.shared.ts`
  (`assertAdmitted`, `installationFrom`, `admissionsFrom`)
- Ändern: `apps/rowboat/src/application/use-cases/plugins/install-plugin.use-case.ts`
  (Idempotenz-Fingerabdruck)
- Ändern: `apps/rowboat/src/application/use-cases/plugins/set-plugin-enabled.use-case.ts`
- Ändern: `apps/rowboat/src/interface-adapters/actions/plugin-action-runtime.ts`
  (zweites Tor, Preview-Umschlag)
- Ändern: `apps/rowboat/app/projects/[projectId]/plugins/components/plugin-card.tsx`,
  `plugin-install-dialog.tsx`
- Test: `apps/rowboat/test/plugins/` — neue Fälle bei den bestehenden Installations-Tests

**Interfaces:**
- Produziert: eine Auswahl `componentDigests: readonly string[]`, die durch Preview,
  Idempotenz-Fingerabdruck, Umschlag-Digest und Installation gereicht wird.

- [ ] **Schritt 1: Die fehlschlagenden Tests schreiben.**
  Eine Installation von `github` mit ausschließlich dem Digest der MCP-Komponente gelingt;
  die Installation schreibt genau eine Admission-Zeile und genau ein Provider-Binding;
  eine Auswahl, die eine `review_required`-Komponente enthält, wird abgelehnt; eine leere
  Auswahl wird abgelehnt; ein Digest, der nicht zum Plugin gehört, wird abgelehnt.

- [ ] **Schritt 2: RED festhalten.**
  `npm --prefix spaces/rowboat/rowboat/apps/rowboat run test:plugins -- install`

- [ ] **Schritt 3: Die Auswahl durchreichen.**
  `assertAdmitted` in eine auswahlbezogene Prüfung aufteilen: es zählt nur noch, dass
  *die ausgewählten* Komponenten `admitted` und `available` sind. `installationFrom`
  filtert seine Provider-Bindings auf die Auswahl, `admissionsFrom` seine Zeilen.
  Nichts an Receipts ändern — die Admission-Zeilen sind der dauerhafte Nachweis.

- [ ] **Schritt 4: Wiedereinspielung verhindern.**
  Die Auswahl **muss** in den Idempotenz-Fingerabdruck und in die Digests des signierten
  Preview-Umschlags eingehen. Sonst lässt sich ein Preview für Komponente A benutzen, um
  Komponente B zu installieren. Test dafür schreiben: derselbe Umschlag mit veränderter
  Auswahl wird abgelehnt.

- [ ] **Schritt 5: Das zweite Tor und das Ein-/Ausschalten.**
  Das eigene Tor der Server-Action (`canonicalStatus(current) !== "available"`) und
  `SetPluginEnabledUseCase` lockern — sonst ließe sich eine Teilinstallation nie
  aktivieren oder deaktivieren.

- [ ] **Schritt 6: UI.**
  Aus dem plugin-weiten `canInstall` eine Auswahl pro Komponente machen. Nicht
  zugelassene Komponenten bleiben sichtbar, aber nicht wählbar, mit ihrem Grund.

- [ ] **Schritt 7: GREEN, Typecheck, Commit.**

**Bewusst nicht:** Repository, Indexe, Receipts, Laufzeit. Und kein Nachinstallieren
weiterer Komponenten — dafür fehlt der Update-Pfad, und ihn zu bauen ist ein eigener
Entwurf.

---

## Aufgabe 2: Plugin-Tools gegen Umbenennen und Schein-Mocking sperren

**Dateien:**
- Ändern: `apps/rowboat/app/projects/[projectId]/entities/tool_config.tsx`
- Ändern: `apps/rowboat/app/projects/[projectId]/entities/entity_list.tsx`
- Ändern: die serverseitige Workflow-Validierung (der Ort, an dem ein `WorkflowTool`
  geschrieben wird)
- Test: `apps/rowboat/test/plugins/`

- [ ] **Schritt 1: Den serverseitigen Test zuerst.**
  Ein gespeichertes Plugin-gebundenes Tool, dessen `name` nicht mehr dem aus der Bindung
  abgeleiteten Namen entspricht, wird abgelehnt. Das ist die eigentliche Sperre — das UI
  ist nicht der einzige Schreiber.

- [ ] **Schritt 2: RED festhalten.**

- [ ] **Schritt 3: Die serverseitige Prüfung einbauen.**

- [ ] **Schritt 4: Das UI ehrlich machen.**
  `isReadOnly` um „ist ein Plugin-Tool" erweitern, die Mock-Karte für Plugin-Tools
  ausblenden, das `isLocked` in der Seitenleiste mitziehen. Das „Mocked"-Abzeichen darf
  auf einem Plugin-Tool nicht mehr erscheinen — es war immer eine Unwahrheit, weil die
  Ausführung den Mock ohnehin ignoriert.

- [ ] **Schritt 5: GREEN, Typecheck, Commit.**

---

## Abschluss-Gate

- [ ] Eine Installation von `github` mit nur der MCP-Komponente gelingt, und das
      resultierende Tool führt bis `write_review_required` (ohne Freigabe) beziehungsweise
      bis zur Credential-Grenze (mit Freigabe).
- [ ] Ein Preview-Umschlag lässt sich nicht auf eine andere Auswahl umbiegen.
- [ ] Ein Plugin-gebundenes Tool lässt sich nicht umbenennen, weder über das UI noch
      durch direktes Schreiben des Workflows.
- [ ] Der Katalog-Digest ist unverändert.
- [ ] Im Plan ausgewiesen und im Abschlussbericht wiederholt: freigeschaltet sind
      **drei** ausführbare Komponenten, nicht 118.
