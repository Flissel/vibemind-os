# vibemind-os Fork-Zusammenführung — Plan

> **Für agentische Ausführung:** Dieser Plan ist KEIN TDD-Plan. Er beschreibt eine
> Zusammenführung zweier divergierter Git-Linien. Jeder Schritt endet mit einer
> Prüfung, die bestehen muss, bevor der nächste beginnt. Bei einem fehlgeschlagenen
> Gate wird nicht weitergemacht — es wird berichtet.

**Ziel:** Die beiden Linien von `vibemind-os` so vereinen, dass nichts von beiden
Seiten verlorengeht, und danach die Gitlinks des äußeren Repos auf das Ergebnis
setzen — damit gebaute Arbeit den Deploy überhaupt erreicht.

**Erhoben am:** 2026-08-31, ausschließlich lesend (`git log`, `git diff`,
`read-tree` in einen temporären Index, `merge-file` pro Blob). Kein Checkout,
kein Merge, kein Fetch.

---

## Korrekturen an der Ausgangsannahme

Drei Dinge, die vorher anders geglaubt wurden und die den Plan verändern:

1. **`e75ccda` ist überholt.** Die Space-Wiring-Linie steht auf **`9c83b40`**
   (2026-08-31 05:57), erreichbar über `origin/feat/mcp-tool-hub` und
   `origin/merge/space-wiring-2026-08-25`. `e75ccda` liegt vier Commits davor.
   **Nicht gegen `e75ccda` planen:** dessen `coding-engine`-Pin `3c520e7b`
   existiert nirgends; `9c83b40` hat ihn bereits auf `ec958764` repariert.
2. **Das äußere Repo hat selbst zwei Linien.** `master` = `d1c98c9` pinnt
   `9c83b40`; `codex/vibemind-ops-baseline` = `29dee37` (der ausgecheckte
   Branch, nicht gepusht) pinnt `f60456a`. Basis `01021ab`, 750 Commits
   auseinander. Das ist ein **zweiter, unabhängiger Fork**.
3. **`f60456a` und `e457dfc` fehlen lokal.** Sie liegen auf origin
   (`merge/space-wiring-plus-subscription-2026-08-30`); der lokale Tracking-Ref
   ist auf `e75ccda` stehengeblieben. Vor allem anderen fetchen.

Merge-Basis der Submodul-Linien: **`5a58a6d`** (2026-08-01). 443 Commits nur auf
master, 97 nur auf der anderen Seite.

---

## Der Befund, der alles bestimmt

**Beide Seiten haben dieselben 55 Capabilities unabhängig voneinander gebaut.**
54 davon namensgleich; beide Seiten landen auf exakt 122. Der einzige
Unterschied: master schreibt `rowboat_*`, die andere Seite `roarboot_*` — und
die andere Seite hat zusätzlich `coding_task_anthropic`.

`git cherry` meldet 0 patch-äquivalente Commits, weil die Umgebung sich
unterscheidet. Das Ergebnis ist trotzdem dasselbe. **Ein Text-Merge verdoppelt
jede dieser 55 Capabilities** und läuft in die Uniqueness-Assertion, die master
in `test_capability_truth_coverage.py` hinzugefügt hat.

Von 373 nicht auflösbaren Pfaden sind:

| Art | Anzahl | Charakter |
|---|---|---|
| rename/modify | 293 | mechanisch — der `security/`+`ops/`-Umbau, alles `R100` |
| modify/delete | 54 | überwiegend mechanisch (Scan-Artefakte, `*.backup`); zwei echte Entscheidungen |
| modify/modify | 17 | davon **2 auto-mergebar**, ~5 echt semantisch |
| add/add | 9 | Testmodule, `space_contract.py`, `captain_cook`-Gitlink |

Die fünf, die von Hand müssen: `capabilities.yaml`,
`config/space_agent_registry.yml`, `spaces/_navigator/registry.py`,
`bridge/config/space_agent_map.yaml`, `test_capability_truth_coverage.py`.

---

## Entscheidung, die vor Schritt 1 fällt

**`roarboot` oder `rowboat`?** Diese eine Benennung treibt `capabilities.yaml`,
`space_agent_registry.yml`, `_navigator/registry.py`, `space_agent_map.yaml` und
die gepinnte `brain_capabilities_v10.yaml`. `spaces/_navigator/registry.py`
enthält auf beiden Seiten die *entgegengesetzte* Alias-Abbildung — beide
zusammen ergäben eine zirkuläre.

- **`rowboat`** (masters Richtung) ist die konsistentere: 267 gegen 186
  Datei-Treffer, und sie entspricht dem tatsächlichen Verzeichnis
  `spaces/rowboat`.
- **Kosten:** die ausgerollte `brain_capabilities_v10.yaml` ist `roarboot`-keyed.
  Ein Rename erzwingt vor dem Deploy einen **v11**-Bump im äußeren Repo.

Ohne diese Entscheidung nicht anfangen.

---

## Koordinations-Gate

**Alle vier Spitzen haben sich innerhalb einer Stunde bewegt** (Stand 06:40):
äußeres ops-baseline 06:40, vibemind-os master 06:33, äußeres master 06:22,
Space-Wiring-Linie 05:57. Der Submodul-Checkout steht auf einem fremden Branch
mit rund 30 uncommitteten Änderungen. Das WORKBOARD führt mindestens drei
gleichzeitige Claims.

**Vor Schritt 1 klären, welche Sessions noch laufen.** Eine Zusammenführung, die
gegen bewegliche Ziele arbeitet, ist verlorene Arbeit.

---

## Richtung

**Basis ist die Space-Wiring-Linie `9c83b40`; `master` wird hineingemergt.**

Gründe: beide äußeren Linien pinnen diese Linie bereits; der laufende Stack
hängt an Code, den es nur dort gibt (`coding_task_anthropic`,
`_FOREIGN_PROVIDER_SELECTION`, `coding_provider` — je **0 Treffer** auf master);
`9c83b40` trägt den reparierten `coding-engine`-Pin; und sie ist die kleinere
Seite (97 gegen 443), master kommt also als der additive Teil.

---

## Schritte

### Schritt 1: Fetchen und Stand festschreiben

Alle Remotes fetchen. Festhalten, welche Commits Basis und Spitzen sind, und
entscheiden, ob `f60456a` (der F4-Ground-Truth-Pfad, +2 Commits) vor oder nach
der Hauptzusammenführung einfließt.

**Gate:** `f60456a` und `e457dfc` sind lokal auflösbar. Der Submodul-Checkout
wurde nicht bewegt.

### Schritt 2: `openfang` in seinem eigenen Repo mergen

Der einzige echt divergierte Gitlink: master pinnt `8a4904b3`, die andere Seite
`7cdfa3c6`, Merge-Basis `39dd2de`, 328 gegen 27 Commits. **Überlappung: 5
Dateien** — `.gitignore`, `agents/brain-video/agent.toml`,
`crates/openfang-runtime/src/drivers/claude_code.rs`, `openfang.vibemind.toml`
und dessen Template.

`claude_code.rs` ist die einzige, die zählt: Subscription-Wrapper-Härtung gegen
masters Runtime-Admission-Arbeit. Von Hand mergen, nicht dem Werkzeug
überlassen. Die Laura-MCP-Registrierung existiert auf beiden Seiten doppelt
(`22bd44f`/`5eb6a34` gegen masters `d0cf260`/`b93e439`) — nur einmal behalten.

**Nicht vergessen:** der Credential-Endpoint aus
`claude/openfang-credential-issuance-v1` gehört ebenfalls in diese
Zusammenführung.

**Gate:** `cargo test -p openfang-api` grün; Binary neu gebaut (`release-fast`,
thin LTO — das Standard-Release-Profil mit fat LTO wird nicht fertig); und die
Bauzeit ist **jünger als der letzte Commit** (dokumentierte Falle: das Exe lief
einmal 3,5 Stunden hinter dem Code her).

### Schritt 3: Benennung vereinheitlichen — vor dem Merge

Auf der gewählten Seite die Umbenennung durchziehen, damit der Merge nicht zwei
Namensräume gegeneinander auflösen muss.

**Gate:** `spaces/_navigator/registry.py` enthält genau eine Richtung, keine
zirkuläre Abbildung.

### Schritt 4: `master` in die Space-Wiring-Linie mergen

Rename-Erkennung die 293 Umbenennungen auflösen lassen. **Danach prüfen**, dass
masters acht neue `*_openfang_llm_boundary.py`-Testdateien unter den *neuen*
Pfaden (`ops/pocs/`, `security/pocs/`) gelandet sind und nicht verwaist unter
den alten — Verzeichnis-Rename-Erkennung ist eine Heuristik und genau hier geht
sie fehl. `ops/poc_site_verifier/*` ausdrücklich einzeln ansehen: acht Dateien,
die master geändert und die andere Seite gelöscht (nicht umbenannt) hat.

Löschungen sind Entscheidungen, keine Konflikte: der `openclaude`-Gitlink bleibt
entfernt, `.scan_history/*.json` und `*.backup`/`agent_new.py` bleiben gelöscht.
Nicht wiederbeleben, nur weil master sie angefasst hat.

**Gate:** keine ungelösten Pfade außer den fünf, die Schritt 5 behandelt.

### Schritt 5: Die fünf semantischen Dateien von Hand

- **`capabilities.yaml`** — als Vereinigung von 122 + 1 schreiben, nicht mergen
  lassen. Ziel: die 122 gemeinsamen Capabilities unter der gewählten Benennung,
  plus `coding_task_anthropic`. Masters `runtime_blocker`-Wortlaut für die acht
  agentfarm-Capabilities übernehmen (er spiegelt die neuere Captain-Cook-
  Entscheidung). Die eine `idea_connect`-Divergenz (execution_target und
  `truth:`) bewusst entscheiden.
- **`test_capability_truth_coverage.py`** — masters Form behalten
  (`>= 67` plus Uniqueness), nicht die `== 122`-Literalform der anderen Seite.
- **`config/space_agent_registry.yml`** — beide Seiten haben dieselbe
  Tool-Aufwertung (`fetch` → `search_knowledge`/`query_knowledge`/`draft_email`);
  nur der Space-Schlüssel unterscheidet sich. Masters Captain-Cook-Redefinition
  (`agent: null`, tool-only, `mcp_servers: [captain-cook]`) übernehmen.
- **`spaces/_navigator/registry.py`** und **`bridge/config/space_agent_map.yaml`**
  — der Benennungsentscheidung folgen.

**Gate:** Capability-Zählung und Uniqueness-Test grün. Keine doppelte
Capability.

### Schritt 6: Gitlinks einzeln setzen

Merge-Werkzeuge behandeln Gitlinks schlecht — jeden einzeln, mit Begründung:

| Submodul | Ziel | Grund |
|---|---|---|
| `voice` | `4b6e72ed` | andere Seite ist Vorfahre |
| `shared` | `fe9c31d2` | andere Seite unverändert |
| `la-fungus-search` | `22011ea9` | andere Seite unverändert |
| `spaces/captain_cook` | `5aa409c9` | master ist Vorfahre |
| `coding-engine` | `ec958764` | masters `352a858e` gegen diesen vergleichen (offen) |
| `spaces/sales`, `spaces/video/laura` | hinzufügen | nur eine Seite |
| `openclaude` | entfernen | Entscheidung der anderen Seite |
| `openfang` | Ergebnis aus Schritt 2 | — |

**Gate:** jeder Gitlink zeigt auf einen Commit, der im jeweiligen Remote
existiert.

### Schritt 7: Äußeres Repo

Deutlich kleiner: 29 nicht aufgelöste Pfade, davon nur `WORKBOARD.md` und ein
Kommentar-Hunk in `infra/swarm/vibemind-stack.yml` echt konfliktbehaftet.
Danach neu pinnen, `brain_capabilities_v11` erzeugen (falls umbenannt wurde) und
das brain-core-Image neu bauen.

**Gate:** Stack-YAML parst; `coding-openclaude` ist verschwunden; v11 stimmt mit
der `capabilities.yaml` des Submoduls überein.

### Schritt 8: Beweisen, dass es läuft

- `/api/capabilities`: beide Coding-Lanes lösen auf `brain-coder-openai` und
  `brain-coder-anthropic` auf
- ein echter Multihop durch den Container, mit **frischem** Fixture-Dateinamen
  (dokumentierte Falle: der Agent beantwortet wiederholte Prompts aus dem
  Gesprächsgedächtnis mit einem veralteten Marker)
- ein Rowboat-Plugin-Aufruf, der bis `write_review_required` kommt — damit
  belegt ist, dass die Plugin-Kette den Merge überlebt hat

---

## Was ausdrücklich nicht versucht wird

- **`e75ccda` als Basis.** Überholt, und sein `coding-engine`-Pin existiert
  nirgends.
- **Die beiden äußeren Linien im selben Zug mergen.** 750 Commits auseinander,
  eigener Fork mit eigener Basis. Vermischt man das, weiß man bei einem Fehler
  nicht mehr, woher er kam.
- **`capabilities.yaml`, `space_agent_registry.yml` oder `_navigator/registry.py`
  automatisch mergen lassen.** Alle drei erzeugen plausibel aussehenden, falschen
  Inhalt.
- **Den `openfang`-Gitlink als Textkonflikt behandeln.** Entweder im
  openfang-Repo mergen oder gar nicht.
- **Den `security/`+`ops/`-Umbau als lesbaren Diff prüfen.** ~25.000 Zeilen reine
  Bewegung; als Rename-Metadaten behandeln und stichprobenartig die
  MCP-Pfadverweise prüfen.

---

## Offen

- Ob `f60456a` über `9c83b40` hinaus mit master kollidiert — die Objekte lagen
  nicht lokal vor.
- Ob `coding-engine` `352a858e` (master) und `ec958764` divergieren.
- Ob die Verzeichnis-Rename-Erkennung masters acht Boundary-Testdateien richtig
  platziert — das zeigt erst ein echter Merge.
