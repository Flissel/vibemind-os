# W2 Connector-Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** App-Komponenten mit `connector_…`-ID werden über die OpenAI Responses API ausführbar, unter denselben Gates wie MCP (Admission, Release, Credential pro Aufruf, Receipt); `asdk_app_` wird ehrlich verweigert; Prozess-MCP ist dokumentiert gestrichen.

**Architecture:** (1) Der Katalog-Importer pinnt die App-Deklaration inline in die Komponenten-Metadata (wie die mcp-Branch), der Lock wird vom SELBEN Quell-Commit re-synct und `PINNED_PLUGIN_CATALOG_DIGEST` neu gepinnt. (2) Ein neuer `ConnectorBridgeProvider` im Kernel macht pro `invoke()` genau einen Responses-API-Call mit `connector_id` + zwei per `CredentialResolver` aufgelösten Credentials. (3) `provider-resolution.ts` bekommt eine App-Branch; die Registry-Sperre fällt. (4) Live-E2E bis zu OpenAIs 401 + Doku.

**Tech Stack:** TypeScript strict, vitest, zod (bestehend); kein neues Paket. Kernel wird als Build-Artefakt konsumiert: nach JEDER Kernel-Änderung `npm --prefix packages/openai-plugin-runtime run build`, sonst sieht die App sie nicht.

**Spec:** `docs/superpowers/specs/2026-09-03-rowboat-plugins-w2-connector-bridge.md` (im Repo-Root-`docs/`, NICHT unter spaces/). Entscheidungen D1–D7 dort sind bindend.

## Global Constraints

- Arbeitsverzeichnis: `spaces/rowboat/rowboat/` im Worktree `vibemind-os/.worktrees/rowboat-master` (Branch `master` — dieses Repo committet per User-Anordnung direkt auf master; ein Commit pro Task, KEIN Push durch Implementer).
- Kein `any`; `unknown` + narrowing. Fail-closed überall: jeder unklare Zustand ist eine Verweigerung, nie ein Durchwinken.
- Kein Secret-Wert in Logs, Fehlermeldungen, Receipts oder `JSON.stringify`-Ausgaben. Credentials nur als `SecretValue`, enthüllt ausschließlich im Request-Header/-Body an OpenAI.
- Der einzige erlaubte Netzwerkzugriff der Implementer-Tests: keiner. Live-Tests sind opt-in via Env (Task 4) und laufen nicht in der Suite.
- OpenAI wird in Tests NIE echt aufgerufen; `fetch` ist injizierbar.
- Nach Kernel-Änderungen: Kernel-Build + Kernel-Suite (`npm --prefix packages/openai-plugin-runtime run build && cd packages/openai-plugin-runtime && npx vitest run`) UND App-Suite (`cd apps/rowboat && npx vitest run test/plugins`) + `npx tsc --noEmit` (in apps/rowboat). Alles grün vor jedem Commit; `git diff --check` sauber.
- `.app.json`-ID-Grammatik (Schema, unverändert): `^(?:connector|asdk_app|templated_apps)_[a-f0-9]+$`. Nur `connector_` ist aufrufbar.
- Referenznamen-Ableitung (D3): `CONNECTOR_` + `name.toUpperCase().replace(/[^A-Z0-9]+/gu, "_").replace(/^_+|_+$/gu, "")`.
- Modell-Default `"gpt-5.6"`, Env `OPENAI_RESPONSES_MODEL`; Basis-URL-Default `https://api.openai.com`, Env `OPENAI_BASE_URL` (D7).

---

### Task 1: App-Deklaration ins Lock pinnen + Katalog-Re-Sync

**Files:**
- Modify: `packages/openai-plugin-runtime/src/import/component-discovery.ts:329-340` (App-Branch)
- Modify: `packages/openai-plugin-runtime/src/domain/catalog.ts` (Digest-Konstante)
- Regenerate: `spaces/rowboat/rowboat/config/openai-plugin-catalog.lock.json` (via Sync-Skript)
- Test: `packages/openai-plugin-runtime/test/component-discovery.test.ts`

**Interfaces:**
- Consumes: `metadata(relativePath, digest, extra?)`, `recordDigest`, `definedFields` — existieren bereits in `component-discovery.ts` (die mcp-Branch bei `:345-368` benutzt alle drei; exakt deren Muster spiegeln).
- Produces: App-Komponenten-Metadata trägt zusätzlich `appDeclaration: { id: string; category?: string; capabilities?: readonly ("read"|"write")[] }` (nur definierte Felder, via `definedFields`). Neuer Wert von `PINNED_PLUGIN_CATALOG_DIGEST` (ergibt sich aus dem Sync; Task 2–4 importieren die Konstante, nie das Literal).

- [ ] **Step 1: Failing Test.** In `component-discovery.test.ts` den bestehenden App-Expansions-Test finden (suche `"app:"` bzw. den Test, der `.app.json` expandiert; das Fixture `test/fixtures/complete-plugin/.app.json` enthält `review` mit `connector_ab12` und `templated` mit category+capabilities). Neuen Test daneben:

```ts
it("pins the validated app declaration into the component metadata, like the mcp branch", () => {
  // Arrange/Act identisch zum bestehenden App-Expansions-Test dieser Datei
  // (dieselbe discover/expand-Helper-Aufrufform wiederverwenden).
  const review = components.find(candidate => candidate.name === "review")!;
  expect(review.metadata.appDeclaration).toEqual({ id: "connector_ab12" });
  const templated = components.find(candidate => candidate.name === "templated")!;
  expect(templated.metadata.appDeclaration).toEqual({
    id: "templated_apps_ab12",
    category: "Work Tracking & Coordination",
    capabilities: ["read", "write"],
  });
  // Eine invalide Deklaration darf KEINE appDeclaration tragen.
});
```

- [ ] **Step 2: Rot sehen.** `cd packages/openai-plugin-runtime && npx vitest run test/component-discovery.test.ts` → FAIL (`appDeclaration` undefined).

- [ ] **Step 3: Implementierung.** In der App-Branch (`component-discovery.ts:329-340`) das `metadata(...)` um das dritte Argument erweitern — exakt das mcp-Muster:

```ts
metadata(
  relativePath,
  recordDigest(declaration.success ? declaration.data : raw, fileDigest),
  // Die validierte App-Deklaration wandert mit dem Katalog, damit der
  // Connector-Bridge-Provider allein aus dem gepinnten Record konstruierbar
  // ist, ohne den Content-Store zur Aufrufzeit zu lesen. Sie traegt eine
  // opake Connector-ID, nie einen Credential-Wert.
  declaration.success ? { appDeclaration: definedFields(declaration.data) } : {},
),
```

- [ ] **Step 4: Grün sehen.** Gleicher Vitest-Aufruf → PASS. Danach volle Kernel-Suite `npx vitest run` — Tests, die alte Metadata-Shapes exakt pinnen (`catalog-importer.test.ts`, Digest-Snapshots), schlagen jetzt kontrolliert fehl: jede Erwartung auf den NEUEN Shape/Digest anheben (nur dort, wo die Ursache nachweislich die neue `appDeclaration` bzw. daraus folgende Digests sind — jede andere Abweichung ist ein Stopp-Signal, nicht anzupassen).

- [ ] **Step 5: Lock re-syncen.** `npm --prefix packages/openai-plugin-runtime run catalog:sync` (Netzwerk zum gepinnten Quell-Commit ist erlaubt und nötig). Erwartung: `config/openai-plugin-catalog.lock.json` ändert sich; `sourceCommit` bleibt `11c74d6ba24d3a6d48f54a194cd00ef3beea18f9`; Entry-Zahl bleibt 180. Falls das Sync-Skript gegen die alte Digest-Konstante validiert und deswegen abbricht: erst Step 6 (Konstante aus dem frisch geschriebenen Lock übernehmen), dann Sync erneut — die Reihenfolge im Skript entscheidet, beide Reihenfolgen sind zulässig, das Ergebnis zählt.

- [ ] **Step 6: Digest neu pinnen.** Neuen `catalogDigest` aus dem regenerierten Lock lesen und `PINNED_PLUGIN_CATALOG_DIGEST` in `packages/openai-plugin-runtime/src/domain/catalog.ts` darauf setzen. Dann im GANZEN Repo-Teilbaum `spaces/rowboat/rowboat` nach dem ALTEN Literal `11035eb884d88be51337853010fc67502f8f6ced64287382a3bb56d24a8c524e` und seinem Kurzpräfix `11035eb8` greppen: Code/Tests müssen die Konstante importieren statt das Literal zu tragen (Vorkommen in Docs bleiben Task 4 überlassen — Liste der Fundstellen im Report notieren).

- [ ] **Step 7: Reproduzierbarkeits-Gate.** Sync ein ZWEITES Mal laufen lassen; `git diff --stat config/openai-plugin-catalog.lock.json` muss leer sein (byte-identisch). Wenn nicht (z. B. `importedAt`-Zeitstempel im Digest): STOPP, Befund als BLOCKED melden — nicht workarounden.

- [ ] **Step 8: Volle Verifikation.** Kernel: Build + Suite. App: `cd apps/rowboat && npx vitest run test/plugins && npx tsc --noEmit`. App-Tests seeden ihren Katalog aus dem Lock über die Konstante — sie müssen ohne inhaltliche Änderung grün sein (Ausnahme: Tests, die den alten Digest-String literal pinnen → auf Konstante umstellen).

- [ ] **Step 9: Commit.** `git add -A spaces/rowboat/rowboat/packages spaces/rowboat/rowboat/config && git commit -m "feat(plugins): pin app declarations into the catalog lock (new catalog digest)"` — Message nennt den alten und neuen Digest.

### Task 2: ConnectorBridgeProvider im Kernel + Registry-Freigabe

**Files:**
- Create: `packages/openai-plugin-runtime/src/providers/connector-bridge-provider.ts`
- Modify: `packages/openai-plugin-runtime/src/providers/provider-registry.ts:135-137` (Sperre entfernen)
- Modify: `packages/openai-plugin-runtime/src/index.ts` (Export; dort, wo `HttpMcpProvider` exportiert wird)
- Test: `packages/openai-plugin-runtime/test/connector-bridge-provider.test.ts` (neu)
- Test-Update: `packages/openai-plugin-runtime/test/provider-registry.test.ts:441-449` (der Test „represents the unavailable OpenAI connector bridge…" pinnt die alte Sperre — er wird zum Positiv-Test: register+resolve eines Bridge-Providers gelingt)

**Interfaces:**
- Consumes: `PluginProvider`/`ProviderDescriptor`/`ProviderRequest`/`ProviderResult`/`ProviderContext`/`ProviderBinding` aus `providers/provider.ts`; `CredentialResolver`, `SecretValue`, `revealSecretValue`, `createSecretValue` aus `providers/credential-resolver.ts`; `evaluateComponentAdmission` (Import wie in `mcp-http-provider.ts`; dort lokales Muster `admissionReason` bei `:260-267` — 1:1 lokal spiegeln); `NormalizedApp` aus `components/app-normalizer.ts`; `PluginPolicy`.
- Produces (Task 3 verlässt sich hierauf, exakt):

```ts
export interface ConnectorBridgeProviderOptions {
  readonly id: string;                       // muss ^[a-z][a-z0-9.-]*$ wie andere Provider-Ids erfüllen
  readonly binding: ProviderBinding;         // providerKind === "openai-connector-bridge"
  readonly app: NormalizedApp;               // app.connectorId MUSS mit "connector_" beginnen, sonst wirft der Konstruktor "provider_unavailable:not_a_connector"
  readonly parentLicense?: string;
  readonly policy: PluginPolicy;
  readonly credentialResolver: CredentialResolver;
  readonly fetchImpl?: typeof fetch;         // Default: globalThis.fetch
  readonly model?: string;                   // Default "gpt-5.6"
  readonly baseUrl?: string;                 // Default "https://api.openai.com"; nur https:, sonst Konstruktor-Throw "component_invalid:base_url" (http://127.0.0.1|localhost|[::1] erlaubt, wie die OpenFang-URL-Regel)
  readonly timeoutMilliseconds?: number;     // Default 30_000, Bounds wie HttpMcpProvider (>=1, <=300_000)
}
export declare function deriveConnectorReference(appName: string): string; // exportiert; CONNECTOR_-Regel aus den Global Constraints
export declare class ConnectorBridgeProvider implements PluginProvider { constructor(options: ConnectorBridgeProviderOptions); readonly id: string; describe(): ProviderDescriptor; invoke(request: ProviderRequest, context: ProviderContext): Promise<ProviderResult>; }
```

- `invoke()`-Vertrag (Reihenfolge ist Teil des Vertrags, jede Stufe fail-closed):
  1. `describe()`-Kind ist `"openai-connector-bridge"`, `temporaryAdapter: false`.
  2. Lizenz-/Policy-Gate: `evaluateComponentAdmission(parentLicense, {kind:"mcp_http"}, policy)` und `…({kind:"write"}, policy)` — nicht admitted → `{status:"failed", reason:<decision.reason>}` (identisch zum Http-Muster; ein Connector-Call ist ein ferngesteuerter MCP-Call, D2).
  3. `request.operationName` fehlt/leer → `{status:"failed", reason:"provider_failed"}`.
  4. Credentials, in dieser Reihenfolge, BEIDE vor jedem Netzwerk-I/O: `credentialResolver.resolve({kind:"environment", reference:"OPENAI_API_KEY"}, request.projectId, {signal})` und `…resolve({kind:"environment", reference: deriveConnectorReference(app.name)}, …)`. Jeder Fehler → `{status:"failed", reason:"credential_missing"}`.
  5. Genau ein `fetchImpl(`${baseUrl}/v1/responses`, {method:"POST", headers:{"content-type":"application/json", authorization:`Bearer ${revealSecretValue(apiKey)}`}, body, signal})` mit Body:

```ts
{
  model,
  store: false,
  tool_choice: "required",
  max_output_tokens: 1024,
  tools: [{
    type: "mcp",
    server_label: app.name,
    connector_id: app.connectorId,
    authorization: revealSecretValue(connectorToken),
    require_approval: "never",
    allowed_tools: [request.operationName],
  }],
  input: `Call the tool ${request.operationName} exactly once with exactly these arguments, then stop: ${JSON.stringify(request.arguments)}`,
}
```

  6. Timeout über `AbortController`, gekoppelt an `context.signal` (Muster: `awaitWithSignal`/Deadline in `mcp-http-provider.ts`; lokal minimal nachbauen, kein Export-Umbau).
  7. HTTP-Status ≠ 200 → `{status:"failed", reason:"provider_failed"}` — Response-Body NIE in die Reason/Message.
  8. 200: JSON parsen; im `output`-Array das erste Item mit `type === "mcp_call"` und `name === request.operationName` suchen. Item fehlt, oder trägt ein non-null `error` → `provider_failed`. Sonst `{status:"success", output: item.output ?? null}`.

- [ ] **Step 1: Failing Tests.** Neue Datei `test/connector-bridge-provider.test.ts`. Fixtures nach dem Muster von `mcp-providers.test.ts` (dortige `binding(...)`-Helper-Form und `RecordingCredentialResolver`-Klasse kopieren — Kernel-Tests teilen keine Fixture-Dateien). Recording-Fetch:

```ts
class RecordingFetch {
  readonly calls: Array<{ url: string; init: RequestInit }> = [];
  #response: () => Response;
  constructor(response: () => Response) { this.#response = response; }
  readonly impl = (async (url: string | URL, init?: RequestInit) => {
    this.calls.push({ url: String(url), init: init ?? {} });
    return this.#response();
  }) as typeof fetch;
}
const ok = (body: unknown): Response => ({ ok: true, status: 200, json: async () => body } as Response);
```

Testfälle (jeder einzeln, Namen sinngemäß):
1. `deriveConnectorReference("canva") === "CONNECTOR_CANVA"`, `("monday-com") === "CONNECTOR_MONDAY_COM"`.
2. Erfolg: Resolver mit `{OPENAI_API_KEY:"sk-fake", CONNECTOR_CANVA:"tok-fake"}`; Response `ok({output:[{type:"mcp_call", name:"export_design", output:{url:"https://x"}}]})` → `{status:"success", output:{url:"https://x"}}`; genau EIN fetch-Call; dessen Body via `JSON.parse` GEPINNT: `tools[0]` deep-equal auf das Vertrags-Objekt (inkl. `authorization:"tok-fake"`, `allowed_tools:["export_design"]`), `tool_choice:"required"`, `store:false`, `model:"gpt-5.6"`; Header `authorization === "Bearer sk-fake"`; URL `https://api.openai.com/v1/responses`.
3. Resolver-Calls gepinnt: erst `OPENAI_API_KEY`, dann `CONNECTOR_CANVA`, beide `kind:"environment"`, projectId durchgereicht.
4. Fehlender API-Key (Resolver wirft) → `{status:"failed", reason:"credential_missing"}`, fetch-Calls === 0. Dito fehlendes Connector-Token.
5. HTTP 401 (`{ok:false, status:401, json: async () => ({error:{message:"boom"}})}`) → `provider_failed`; `JSON.stringify` des Results enthält weder "boom" noch "sk-fake" noch "tok-fake".
6. 200 ohne passendes `mcp_call`-Item → `provider_failed`; Item mit `error:"tool exploded"` → `provider_failed` (und "exploded" leakt nicht).
7. Policy verweigert Write (`allowWriteCapabilities:false`-artige Policy wie `WRITE_HTTP_POLICY`-Gegenstück in `mcp-providers.test.ts`) → failed mit dem Decision-Reason, fetch-Calls === 0, Resolver-Calls === 0.
8. Konstruktor wirft für `app.connectorId = "asdk_app_ab12"` → Error-Message `"provider_unavailable:not_a_connector"`.
9. Registry: `new ProviderRegistry()`; `register(binding("binding.bridge","openai-connector-bridge",DIGEST), provider)` gelingt jetzt; `resolve` liefert `{status:"available"}`-Facade, deren `invoke` delegiert (der bisherige Sperr-Test `provider-registry.test.ts:441-449` wird auf dieses Verhalten umgeschrieben; Commit-Message nennt das).
10. `describe()` === `{id, kind:"openai-connector-bridge", temporaryAdapter:false}` und Binding-Mismatch (falscher componentDigest im Binding vs. `app.componentDigest`) → Konstruktor-Throw `"provider_invalid:descriptor_mismatch"`.

- [ ] **Step 2: Rot sehen.** `npx vitest run test/connector-bridge-provider.test.ts` → alle FAIL (Modul existiert nicht); `npx vitest run test/provider-registry.test.ts` → der umgeschriebene Sperr-Test FAIL.
- [ ] **Step 3: Implementierung** gemäß Vertrag oben; Registry-Sperre (`provider-registry.ts:135-137`) ersatzlos entfernen (der Kind bleibt in `PROVIDER_KINDS`). Export in `index.ts` neben `HttpMcpProvider` (`ConnectorBridgeProvider`, `deriveConnectorReference`, Typ `ConnectorBridgeProviderOptions`).
- [ ] **Step 4: Grün sehen.** Beide Testdateien, dann volle Kernel-Suite + Build; App-Suite + tsc (Registry-Freigabe darf nichts brechen — `provider-resolution.ts` gated weiterhin auf mcp-http, bis Task 3).
- [ ] **Step 5: Commit.** `git commit -m "feat(plugins): connector-bridge provider for connector_ app components"`.

### Task 3: Resolution-Branch + DI für App-Komponenten

**Files:**
- Modify: `apps/rowboat/src/infrastructure/plugins/provider-resolution.ts` (App-Branch + Doc-Kommentar `:14-22` aktualisieren)
- Modify: `apps/rowboat/di/plugins-container.ts` (Model/BaseUrl-Env in die Dependencies der Resolution durchreichen — exakt dort, wo `resolveOpenFangComposedProvider` die `credentialTimeoutMs` durchreicht)
- Test: `apps/rowboat/test/plugins/provider-resolution.test.ts`

**Interfaces:**
- Consumes: `ConnectorBridgeProvider`, `deriveConnectorReference`, `normalizeApp` (bereits exportiert? prüfen — `components/app-normalizer.ts`; falls nicht in `index.ts`, dort exportieren und Kernel neu bauen), `PINNED`-Konstante aus Task 1, Options-Vertrag aus Task 2.
- Produces: `resolvePluginProvider` akzeptiert zusätzlich `binding.providerKind === "openai-connector-bridge" && component.kind === "app"`; `PluginProviderResolutionDependencies` erhält optionale Felder `responsesModel?: string` und `openAiBaseUrl?: string` (Composition liest `OPENAI_RESPONSES_MODEL`/`OPENAI_BASE_URL` je Aufruf, wie `OPENFANG_URL` frisch aus `process.env`).

- [ ] **Step 1: Failing Tests** in `provider-resolution.test.ts` (bestehende Fixture-Formen der Datei wiederverwenden):
1. App-Komponente (`kind:"app"`, `metadata.appDeclaration:{id:"connector_ab12"}`, `metadata.bindingDigest` passend, Binding `providerKind:"openai-connector-bridge"`) → `{status:"available"}`; der Provider-`describe().kind === "openai-connector-bridge"`.
2. `appDeclaration.id:"asdk_app_ab12"` → UNAVAILABLE (kein Throw).
3. `appDeclaration` fehlt in der Metadata (alter Lock-Stand) → UNAVAILABLE.
4. Binding-`componentDigest` ≠ `metadata.bindingDigest` → UNAVAILABLE.
5. mcp-http-Pfad unverändert (ein bestehender Positiv-Test bleibt grün — nur benennen, nicht neu schreiben).
- [ ] **Step 2: Rot sehen**, gezielter vitest-Lauf.
- [ ] **Step 3: Implementierung.** In `resolvePluginProvider` VOR dem bisherigen `UNAVAILABLE`-Gate:

```ts
if (request.binding.providerKind === "openai-connector-bridge" && request.component.kind === "app") {
  const declaration = request.component.metadata.appDeclaration;
  if (declaration === undefined || declaration === null || typeof declaration !== "object") return UNAVAILABLE;
  if (request.binding.componentDigest !== request.component.metadata.bindingDigest) return UNAVAILABLE;
  try {
    const app = normalizeApp(request.component.name, declaration, request.binding.componentDigest as string);
    if (!app.connectorId.startsWith("connector_")) return UNAVAILABLE; // asdk_app_/templated_apps_: kein oeffentlicher Aufrufpfad (Spec D4)
    const provider = new ConnectorBridgeProvider({
      id: request.binding.id, binding: request.binding, app,
      parentLicense: request.entry.licenseDeclaration,
      policy: dependencies.policy ?? DEFAULT_POLICY,
      credentialResolver: dependencies.credentialResolver,
      ...(dependencies.responsesModel === undefined ? {} : { model: dependencies.responsesModel }),
      ...(dependencies.openAiBaseUrl === undefined ? {} : { baseUrl: dependencies.openAiBaseUrl }),
      ...(dependencies.timeoutMilliseconds === undefined ? {} : { timeoutMilliseconds: dependencies.timeoutMilliseconds }),
    });
    const registry = new ProviderRegistry();
    registry.register(request.binding, provider);
    return registry.resolve(request.binding);
  } catch { return UNAVAILABLE; }
}
```

Der Doc-Kommentar am Dateikopf (`:14-22`) wird auf den neuen Stand umgeschrieben (App via Bridge; Prozess-MCP weiterhin unavailable, Verweis auf Spec D5). In `di/plugins-container.ts` beim Aufbau der Resolution-Dependencies: `responsesModel: (process.env.OPENAI_RESPONSES_MODEL ?? "").trim() || undefined, openAiBaseUrl: (process.env.OPENAI_BASE_URL ?? "").trim() || undefined`.
- [ ] **Step 4: Grün sehen.** App-Suite komplett + tsc; Kernel-Suite unangetastet grün.
- [ ] **Step 5: Commit.** `git commit -m "feat(plugins): resolve connector_ app components through the bridge"`.

### Task 4: Live-E2E (bis 401) + Doku

**Files:**
- Create: `apps/rowboat/test/plugins/live-connector-bridge-e2e.test.ts`
- Modify: `spaces/rowboat/rowboat/docs/openai-plugin-runtime-operations.md`; Repo-Root `E2E-PROOF.md` (Part IV anhängen); `docs/superpowers/plans/2026-08-30-rowboat-plugin-full-integration-master.md` (W2-Zeile)

**Interfaces:**
- Consumes: identisches Live-Harness wie `live-credential-acceptance-e2e.test.ts` (dieselbe Env-Trias + `ROWBOAT_E2E_EVIDENCE`; dieselben `listApprovals/approve/approveWhenRaised`-Helper — kopieren, Live-Tests teilen keine Fixtures) plus zusätzliches Opt-in `ROWBOAT_LIVE_CONNECTOR=1`. Daemon-Seite braucht `OPENFANG_ISSUABLE_CREDENTIALS=OPENAI_API_KEY,CONNECTOR_CANVA` mit offensichtlich fake generierten Werten (`sk-fake-e2e-…`).
- Produces: E2E-Beweis Part IV; Doku-Stand laut Spec-Akzeptanz 4.

- [ ] **Step 1: Test schreiben** (opt-in, skipIf ohne die vier Env-Variablen). Ablauf, alles asserted:
1. Katalog seeden (neuer Digest via Konstante), Projekt einfügen — 1:1 das Muster aus `live-credential-acceptance-e2e.test.ts` Schritte 1–2.
2. canva: App-Komponente finden (`kind:"app"`, admitted, `metadata.appDeclaration.id` beginnt mit `connector_`), `componentDigests:[bindingDigest]`-Install → genau 1 Admission-Row + 1 Binding.
3. `AddPluginToolUseCase` bindet sie; `addition.added === true`.
4. Runtime-Komposition wie Part III; `OPERATION = "export_design"`, `ARGUMENTS = Object.freeze({})` (leer: nichts zu leaken).
5. `approveWhenRaised(30_000)`; invoke → erwartet Throw `provider_failed` (Fake-Key ⇒ OpenAIs 401); Approval wurde ERHOBEN und approved; `action_summary` enthält den componentDigest und keine Argumentwerte.
6. Receipt: `type:"execution"`, `status:"failed"`, `componentKind:"app"`, `output.approvalId === raised.id`.
7. Negativ-Zweig im selben Test: eine asdk-App (`actively`) installieren + Tool-Add versuchen → Add gelingt (kein Kind-Gate), invoke → `provider_unavailable` OHNE Approval (Resolution verweigert vor dem Release? NEIN — Release läuft vor Resolution: prüfen und die tatsächliche Reihenfolge asserten, die der Code zeigt; erwartetes Endresultat ist `provider_unavailable` und KEIN erfolgreicher Provider-Kontakt; die beobachtete Approval-Anzahl wird geloggt und gepinnt wie beobachtet, mit Begründung im Testkommentar).
- [ ] **Step 2: Live ausführen** gegen frischen isolierten Daemon :4273 (Rezept exakt wie Part III, NUR fake Werte; Evidence-Log; Secret-Scan `sk-fake`-Präfix zählt nicht als Secret, trotzdem 0 echte Werte). Grün sehen; Evidence sichern.
- [ ] **Step 3: Doku.** Ops-Doc: neue Sektion „The connector bridge" unter Credentials/Runtime (Referenznamen-Tabelle: `OPENAI_API_KEY` + die 15 `CONNECTOR_*`-Namen der admitted connector-Apps aus der Spec; Modell/BaseUrl-Envs; asdk-Absatz; Prozess-MCP-Kürzung mit Spec-D5-Begründung); alle Vorkommen des ALTEN Katalog-Digests in den Docs auf den neuen heben (Task-1-Report listet die Fundstellen); „Two component kinds still cannot execute"-Absatz umschreiben (jetzt: Prozess-MCP + asdk-Apps). `E2E-PROOF.md`: Part IV nach dem Muster von Part III (Headline, Beweiszeilen, was bewiesen/nicht bewiesen — insbesondere: connector_id-Gültigkeit gegenüber OpenAI bleibt UNbewiesen bis zum echten Call, D6). Master-Plan W2-Zeile: Status + Verweis auf diesen Plan.
- [ ] **Step 4: Volle Verifikation + Commit.** Beide Suiten + tsc + `git diff --check`; `git commit -m "test(plugins): live connector-bridge proof to OpenAI's 401, plus W2 docs"`.

---

## Self-Review (ausgeführt beim Schreiben)

- Spec-Deckung: D1→Task 1, D2→Task 2, D3→Task 2 (+Namen in Task 4 Doku), D4→Task 2 Step 1.8 + Task 3 Test 2 + Task 4 Negativ-Zweig, D5→Task 4 Doku, D6→Task 4, D7→Task 2/3. Akzeptanz 1–4 → Tasks 1/2/4.
- Typkonsistenz: `appDeclaration` (Task 1) wird in Task 3 konsumiert; `ConnectorBridgeProviderOptions`/`deriveConnectorReference` (Task 2) in Task 3/4; Digest nur als Konstante.
- Bekannte Restrisiken, bewusst im Plan: (a) Lock-Reproduzierbarkeit (Task 1 Step 7 ist ein hartes Gate mit BLOCKED-Pfad); (b) Reihenfolge Release↔Resolution im asdk-Negativfall (Task 4 Step 1.7 verlangt Beobachten+Pinnen statt Raten); (c) ob OpenAI hex-`connector_…`-IDs der Plugin-Directory akzeptiert, ist erst mit echtem Call beweisbar — explizit Nicht-Ziel (D6).
