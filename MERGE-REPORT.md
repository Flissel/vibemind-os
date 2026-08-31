# Fork-Zusammenführung — Abschlussbericht

**Worktree:** `C:/Users/User/ClaudeWork/wt-fork-merge`, Branch `claude/fork-reconciliation-v1`
**Basis (`HEAD`, „ours"):** `3f6daa0` — Space-Wiring-Linie (beide Spitzen bereits vereint)
**Hineingemergt (`MERGE_HEAD`, „theirs"):** `f620ae0` — `master`
**Merge-Basis:** `5a58a6d`
**Nicht gepusht. Kein Gitlink in einem Elternrepository angefasst.**

---

## Kurzfassung

Alle 24 offenen Pfade sind aufgelöst. Die 390 vorher gestageten Pfade wurden nicht angefasst.

| Prüfung | Ergebnis |
|---|---|
| Konfliktmarker im Baum (`<<<<<<<` / `=======` / `>>>>>>>`) | **0 / 0 / 0** |
| Capabilities | **123**, keine doppelten Schlüssel, kein `roarboot`-Name |
| `roarboot` im Baum | **634** in 45 Dateien (vorher 658/46) — Klassifikation unten |
| Tests (13 Dateien) | **8 failed, 115 passed** — identisch zur `master`-Baseline |
| Neue Fehlschläge durch diesen Merge | **0** |
| `git diff --check` (Working Tree) | sauber |

**Drei Befunde, die über die reine Konfliktauflösung hinausgehen** — Details in §5:

1. **Git hat in `capability_targets.py` still 343 Zeilen dupliziert** (inkl. einer zweiten,
   abweichenden `resolve_registry_execution_target`-Definition, die die richtige verdeckte).
   Nicht als Konflikt markiert. Gefunden über einen fehlschlagenden Test, behoben.
2. **`test_roarboot_space_contract.py` und `test_rowboat_space_contract.py` sind dieselbe
   Datei**, auf beiden Linien nach der Fork unterschiedlich benannt. Git sah keinen Konflikt
   und behielt beide. Die `roarboot`-Fassung behauptet `"rowboat" not in spaces` — das
   genaue Gegenteil der Namensentscheidung. Entfernt.
3. **`master` hat an seiner Spitze bereits 8 Fehlschläge** in genau diesem Bereich (7 davon
   wegen der unvollständig ausgerollten Captain-Cook-Redefinition). Diese wandern
   unvermeidlich in den Merge, weil der Brief die Captain-Cook-Redefinition übernehmen lässt.

---

## 1. Test-Baselines (vor dem Merge, je Seite gemessen)

Gemessen auf `git archive`-Auszügen beider Spitzen, **nicht** über `git stash`.

> **Umgebungs-Fallstrick, der die erste Messung verfälscht hat:** `vibemind_shared` wird über
> ein editable-`.pth` aus `Desktop/Vibemind_V1/vibemind-os/shared/src` aufgelöst — und dieser
> Checkout steht auf `130518b` (= **HEAD**s Pin). `master` pinnt `fe9c31d`, das erst
> `OpenFangUnavailable` exportiert. Ohne Korrektur bricht `master` mit einem
> `ImportError` schon beim Einsammeln — das ist **kein** `master`-Defekt.
> Alle Zahlen unten mit `PYTHONPATH` auf das jeweils **gepinnte** `shared` gemessen.

| Seite | Dateien | Ergebnis |
|---|---|---|
| **HEAD** (Space-Wiring) | 7 vorhandene + `test_roarboot_space_contract.py` | **43 passed, 0 failed** |
| **MASTER** | 11 + 2 rowboat-Dateien | **115 passed, 8 failed** |
| **Merge-Ergebnis** | dieselben 13 | **115 passed, 8 failed** |

Die 8 Fehlschläge sind **namentlich identisch** mit denen der `master`-Baseline:

```
test_agentfarm_brain_contract.py::test_agentfarm_is_canonical_with_autogen_as_legacy_alias
test_agentfarm_brain_contract.py::test_agentfarm_reserves_its_future_chat_identity_without_execution_scope
test_agentfarm_brain_contract.py::test_agentfarm_llm_role_uses_the_reserved_chat_identity
test_agentfarm_brain_contract.py::test_agentfarm_registry_is_disabled_while_the_versioned_source_has_no_runtime
test_agentfarm_brain_contract.py::test_agentfarm_events_name_real_operations_not_generic_database_tools
test_space_contract.py::test_registry_contract_owns_event_to_space_mapping
test_bridge_map_matches_registry.py::test_bridge_map_has_no_unknown_spaces
test_minibook_space_contract.py::test_minibook_status_uses_only_the_bound_deterministic_mcp_tool_and_independent_truth
```

Ursachen (alle **vorbestehend auf `master`**, siehe §5.3):
* 1–6: Die Captain-Cook-Redefinition landete in `config/space_agent_registry.yml`, wurde aber
  nie in `test_agentfarm_brain_contract.py` / `test_space_contract.py` nachgezogen.
* 7: `agentfarm` hat jetzt `agent: null`, steht aber weiter in der Bridge-Map.
* 8: `minibook.status` trägt in der Registry ein `required_provenance`, das der Test nicht erwartet.

---

## 2. Die Capability-Datei

| | Einträge | eindeutig |
|---|---|---|
| Merge-Basis | 66 | 66 |
| HEAD | 122 | 122 |
| MASTER | 122 | 122 |
| **Ergebnis** | **123** | **123** |

Von Hand geschrieben, nicht gemergt: `master`s 122 als Textbasis (bereits `rowboat`-benannt),
dann genau zwei Eingriffe — `coding_task` durch HEADs Fassung ersetzt und
`coding_task_anthropic` daneben eingesetzt. Dadurch bleibt `master`s Formatierung
und alle Kommentare byteweise erhalten.

**Die Rechnung 122 + 1 = 123 stimmt, die Beschreibung im Brief aber nicht ganz.** Es sind
nicht „122 gemeinsame Capabilities": geteilt sind nur **116**. Dazu kommen 5 Paare, die
dieselbe Capability unter beiden Schreibweisen tragen (`roarboot_*` ↔ `rowboat_*`), und
**`rowboat_status`, das es nur auf `master` gibt** — HEAD hat dafür keine Entsprechung.
Also 116 + 5 + 1 = `master`s 122, plus `coding_task_anthropic` = 123.

Verglichen wurden alle 116 geteilten Einträge feldweise; **11 wichen ab**:

| Capability(s) | Entscheidung | Begründung |
|---|---|---|
| die 8 `agentfarm_*` | **MASTER**: `missing_persistent_agentfarm_runtime` | siehe §5.1 — der Brief nennt hier den falschen String |
| `idea_connect` | **MASTER**: `mcp:brain-ideas:spaces-ideas:idea_connect` + `truth:supabase_edge_ids` | Brief bestätigt. Belegt: `master`s Linie enthält HEADs Zustand (`ee9aec6`) **und** dessen Ablösung (`62ac87e`, 2026-08-04). `world_observer` (bereits gestaget) implementiert `supabase_edge_ids`. |
| `coding_task` | **HEAD**: `openfang:brain-coder-openai`, `coding_provider: openai` | *Im Brief nicht erwähnt.* HEAD `970a51c` (2026-08-24, „route coding through subscription agents") ist deutlich neuer als `master`s `fc19490` (2026-05-13). Entscheidend: `master`s Ziel `openfang:openclaude-coder` zeigt auf **openclaude, das per Brief-Punkt 8 aus `.gitmodules` entfernt bleibt**. `master` zu nehmen hieße, die Capability auf ein gelöschtes Submodul zu routen. |
| `minibook.status` | **MASTER**: `mcp:brain-knowledge:spaces-minibook:minibook_status` + `truth:http_ok` | *Im Brief nicht erwähnt.* `master`s Historie enthält beide Zustände: `b6eee41` (2026-07-30, die `direct:`-Form, = HEADs Stand) und `cadeaea` (2026-08-04, „add status MCP contract"), das sie ersetzt. `master` ist HEADs Zustand also schon durchlaufen. Zusätzlich bestätigt durch `master`s `test_minibook_execution_target.py`, das genau diesen Vertrag prüft. |

Die 5 Umbenennungspaare sind **inhaltlich identisch** bis auf die Schreibweise des
Markennamens in `description` — es geht nichts verloren.

---

## 3. Die 24 Pfade im Einzelnen

### 3.1 Vier Submodul-Gitlinks — jeder Vorfahrenschafts-Anspruch nachgeprüft

| Submodul | gesetzt auf | Herkunft | Beleg |
|---|---|---|---|
| `voice` | `4b6e72e` | MASTER | `merge-base --is-ancestor ded630b 4b6e72e` → **wahr**; HEAD liegt 4 Commits zurück. Brief bestätigt. |
| `spaces/captain_cook` | `5aa409c` | HEAD | `644a5c3` ist Vorfahre von `5aa409c`; `master` liegt 7 Commits zurück. Brief bestätigt. |
| `coding-engine` | `ec95876` | HEAD | `352a858` (master) ist Vorfahre von `ec95876`. Entspricht dem im Brief genannten Ziel. `3c520e7b` existiert im Submodul-Objektspeicher tatsächlich nicht. |
| `openfang` | `7cdfa3c` | HEAD | **⚠ VORLÄUFIG — siehe unten** |

> ### ⚠ `openfang` ist ein Platzhalter-Pin und muss ersetzt werden
> Die beiden openfang-Linien (`7cdfa3c` = HEAD, `8a4904b` = master) sind **divergent** —
> keine ist Vorfahre der anderen. Diese Zusammenführung läuft in openfangs eigenem Repo und
> war ausdrücklich **nicht meine Entscheidung**. Der Gitlink steht nur deshalb auf HEADs
> Wert, damit der Merge abschließbar ist. **Gegen diesen Pin wurde nichts verifiziert.**
> Er ist zu ersetzen, sobald der openfang-Merge steht.

### 3.2 `.gitmodules`

**HEADs Fassung — nachgewiesen bereits die vollständige Vereinigung.** Eintrag für Eintrag
gegen `master` geprüft: alle 13 `master`-Einträge sind enthalten. Die drei Festlegungen des
Briefs sind erfüllt: `openclaude` bleibt entfernt (auch aus dem Index verschwunden),
`spaces/sales` + `spaces/video/laura` sind da, `coding-engine` steht auf der umbenannten URL
`Flissel/coding-engine.git`. `spaces/flowzen/flowzen` und `…/moire_tracker` hatten **beide**
Seiten entfernt. Die Gitlink-Einträge im Index decken sich exakt mit den Pfaden dieser Datei.

### 3.3 Die fünf semantischen Dateien

| Datei | Entscheidung | Begründung |
|---|---|---|
| `brain/the_brain/data/capabilities.yaml` | von Hand, **123** | §2 |
| `brain/the_brain/tests/test_capability_truth_coverage.py` | **MASTER** | Brief bestätigt: Ratsche `>= 67` + Uniqueness statt HEADs `== 122`. Die Uniqueness-Zusicherung ist genau das Netz gegen die Verdopplung aus §2; die Literalform wäre bei 123 sofort gebrochen. Ist-Wert: 32 `truth:`-Validatoren (Ratsche verlangt ≥ 27). |
| `spaces/_navigator/registry.py` | **MASTER, ganze Datei** | ⚠ Nicht nur die Konfliktzeile — siehe §5.4. Aliaskarte `roarboot → rowboat`; azyklisch geprüft: kein Ziel (`agentfarm`, `rowboat`, `bubbles`) ist selbst ein Schlüssel. |
| `config/space_agent_registry.yml` | **MASTER** (alle 4 Hunks) | `rowboat`-Schlüssel; Captain-Cook-Redefinition (`agent: null`, `mcp_servers: [captain-cook]`) laut Brief-Punkt 6. Zur Tool-Aufwertung siehe Korrektur in §5.5. |
| `bridge/config/space_agent_map.yaml` | **MASTER** | Die Datei erklärt sich selbst als Spiegel der Registry; `master`s Werte decken sich zusätzlich mit dem bereits gestageten `llm_config.yml.example` (`brain-video`, `brain-wellness`, `brain-forecaster`). Siehe Korrektur in §5.5. |

### 3.4 Die auf **beiden** Seiten neu angelegten Dateien — **es sind acht, nicht sieben**

Der Auftrag nannte „zwei plus fünf Testdateien", der Brief „neun". Tatsächlich sind es
**2 + 6 = 8** (`spaces/captain_cook` ist der neunte `AA`-Eintrag, aber ein Gitlink).
Für jede beide Fassungen nebeneinandergelegt (`git show :2:` / `:3:`):

| Datei | Entscheidung | Begründung |
|---|---|---|
| `brain/the_brain/core/space_contract.py` | **MASTER** | Echte Obermenge: richtige Aliasrichtung **plus** ein Shim, der Alt-Events `roarboot.*` weiter auf den `rowboat`-Space auflöst. HEAD hat die inverse Aliasrichtung und keinen Shim. |
| `spaces/research/execution_target.py` | **MASTER** | `master` pinnt den Agenten kanonisch auf `brain-researcher` und entfernt sowohl die `RESEARCH_AGENT`-Env-Übersteuerung als auch den `openclaw-visible`-Zweig. Das ist **absichtliche Härtung**, kein Verlust: `master`s Test setzt `RESEARCH_AGENT=hostile-agent` und verlangt, dass es ignoriert wird. HEADs Implementierung würde diesen Test brechen. |
| `…/tests/test_agentfarm_brain_contract.py` | **MASTER** | 11 statt 6 Tests, alle 6 HEAD-Fälle enthalten. HEADs abweichende Zusicherung `assert not source_path.exists()` ist im gemergten Baum **faktisch falsch** — `spaces/agentfarm/{pyproject.toml,agentfarm/__init__.py,runtime-manifest-v1.json}` liegen dort (gestaget, von `master`). |
| `…/tests/test_bubbles_space_contract.py` | **MASTER** | Reine Obermenge, per Zeilendiff belegt: `master` **entfernt keine einzige Zeile** von HEAD und ergänzt 4 Tests (u. a. „failed promote meldet Fehler statt success-förmigem String"). |
| `…/tests/test_space_contract.py` | **MASTER** | Richtige Aliasrichtung plus zusätzliche Idempotenz-Zusicherungen (`rowboat` → `rowboat`). HEADs Fassung kodiert die abgeschaffte Namensrichtung. |
| `…/tests/test_minibook_execution_target.py` | **MASTER** | Gleiche 7 Tests, aber `minibook.status` aus der `direct:`-Schleife herausgenommen und der MCP-/`truth:http_ok`-Vertrag ausformuliert — deckt strikt mehr ab und passt zur Capability-Entscheidung. |
| `…/tests/test_research_execution_contract.py` | **MASTER** | Reine Obermenge (6 → 8), keine entfernte Zeile. |
| `…/tests/test_ideas_execution_contract.py` | **VEREINIGUNG** ⚠ | **Die einzige Datei, bei der `master` keine Obermenge ist.** `master` bringt 6 zusätzliche Truth-Validator-Tests mit, **verliert aber** HEADs registry-gestützte Eigentumsprüfung: HEAD prüft `load_space_contract().event_space_map`, `master` nur das Modul-Literal `EVENT_SPACE_MAP`. Genommen: `master`s Fassung **plus** HEADs Zusicherung in `test_idea_to_project_remains_owned_by_ideas_space`, sodass beide Quellen geprüft werden. Ergebnis: 14 Tests, alle grün. (HEADs unbenutzter `import ast` wurde nicht übernommen.) |

### 3.5 Die sechs gewöhnlichen Konflikte

| Datei | Entscheidung | Begründung |
|---|---|---|
| `configs/agents/brain-orchestrator.yaml` | **MASTER** | Eine Zeile `notes:`. `master`s Wortlaut wird von `master`s Agentfarm-Test wörtlich geprüft (dieser Teiltest ist grün) und passt zum vorhandenen Runtime-Artefakt. |
| `core/capability_targets.py` | **MASTER** (beide Hunks) | (a) `from dataclasses import dataclass` — im gemergten Rumpf bei `@dataclass(frozen=True)` **tatsächlich benutzt**; Weglassen wäre ein `NameError`. (b) Doku-String `mcp:<agent>:<server>:<tool>` — der bereits gemergte Parser und die Fehlermeldung der Datei nennen genau diese 4-teilige Form, HEADs 3-teilige ist veraltet. **Zusätzlich de-dupliziert, siehe §5.2.** |
| `core/capability_validator.py` | **MASTER** (alle 3 Hunks) | Zwei Hunks sind reine Leerzeilen. Der dritte ist inhaltlich: `master` macht `valid = verified is True` (UNVERIFIED ist nie Erfolg), HEAD lässt UNVERIFIED ohne `require_verified` durchgehen. `master`s Form wird von `test_truth_with_ground_truth_disabled_is_not_valid` erzwungen und entspricht dem Projektgrundsatz, Erfolg nur aus unabhängiger Ground-Truth abzuleiten. HEADs beide einschlägigen Tests bleiben unter `master`s Regel grün. |
| `core/plan_executor.py` | **MASTER** (alle 3 Hunks) | `master` ersetzt HEADs hartkodierte `cap_to_event`-Tabelle und den Live-Probe gegen `/api/agents` durch `canonical_space_event_id()` aus der Registry — „Registry ist die einzige Wahrheit" (ABSORB-7) und bewusst fail-closed („ein fehlender Agent darf nie das alte direkte Ziel reaktivieren"). Nachgeprüft: `canonical_space_event_id` und `_canonical_space_event_agent` existieren im gemergten Baum. **HEADs Beitrag bleibt erhalten** — die Datei weicht nach der Auflösung noch in einer Zeile von `master` ab (Kommentar `brain-coder-*` statt `openclaude`), was zur `coding_task`-Entscheidung passt. |
| `core/supabase_ideas_ops.py` | **MASTER** | `master` gibt strukturierte Fehlschläge zurück statt „success-förmiger" Strings (dokumentierter Befund D1, Zyklus 1, 2026-08-01). HEADs Verhalten **ist** der behobene Fehler. Passendes Gegenstück: die 3 Tests, die `master` in `test_bubbles_space_contract.py` ergänzt. |
| `web/routers/introspection.py` | **MASTER** | `import tomllib` — im gemergten Rumpf bei `tomllib.load` / `tomllib.TOMLDecodeError` benutzt; Weglassen wäre ein `NameError`. |

---

## 4. `roarboot` — Zahl und Einordnung

**634 Vorkommen in 45 Dateien** (vor dem Aufräumen: 658 / 46).

> Gezählt über die versionierten Dateien, **ohne diesen Bericht**. Der Bericht selbst
> enthält den Namen 27-mal, weil er ihn bespricht; über den gesamten Commit-Baum
> gezählt sind es daher 661 in 46 Dateien.

> **Der Brief verlangt hier etwas, das sich nicht erfüllen lässt** („nach der Auflösung darf
> `roarboot` in keiner der Konfliktdateien mehr vorkommen"). `master`s eigener Test
> `test_rowboat_canonical_identity.py` verlangt das **Gegenteil**: er prüft ausdrücklich
> `resolve_alias("roarboot") == "rowboat"`, `EVENT_SPACE_MAP["roarboot.query"] == "rowboat"`
> und `get_renderer_id("rowboat") == "roarboot"`. Der String **muss** an den Ingress-Stellen
> stehen bleiben, sonst nimmt das System die alte Schreibweise nicht mehr entgegen.
> Erfüllt ist die Entscheidung in ihrer tragenden Bedeutung: **`roarboot` ist nirgends mehr
> ein kanonischer Name** — kein Space-Key, kein Capability-Key, kein Registry-Key, kein
> emittiertes Event-Präfix.

| Anzahl | Kategorie | Bewertung |
|---:|---|---|
| 329 | `data/space_capabilities/*` (generiertes Inventar) | Generat, auf **beiden** Seiten vorhanden — nicht durch diesen Merge entstanden. Enthält `roarboot.{md,yml}` **und** `rowboat.{md,yml}` als Doppel; beim nächsten Lauf von `scripts/extract_space_capabilities.py` neu zu erzeugen. |
| 136 | `spaces/rowboat/*` (Modulnamen `roarboot_client.py`, `roarboot_tools.py`, `roarboot_workers.py`) | Verzeichnis heißt bereits `rowboat`, die Dateien darin noch nicht. Auf beiden Seiten gleich — eigener Rename-Vorgang, außerhalb dieses Merges. |
| 90 | `skills/roarboot/*` + `skills/INDEX.md` | Altbestand, auf beiden Seiten identisch. |
| 53 | Brain-Laufzeit: Alias / Ingress | **Beabsichtigt.** `space_contract.py` (Alias + Legacy-Event-Shim), `space_routing_head.py` (Ingress-Map mit ausdrücklichem Kommentar „roarboot is never emitted as a Space ID"), `capabilities.yaml` (nur `anchor_phrases` + `(?:roarboot\|rowboat)`-Muster), `_navigator/registry.py` (`aliases`, `renderer_id`, Docstring). |
| 21 | Historische Pläne / Betriebsdokumente | Historie, bewusst unangetastet. |
| 5 | `scripts/extract_space_capabilities.py`, `tests/legacy-brain/*` | Altbestand. |

---

## 5. Befunde — was der Brief nicht traf, und was der Merge selbst verbockt hat

### 5.1 Der Brief nennt beim `agentfarm`-`runtime_blocker` den falschen String

> Brief: „`runtime_blocker` der acht `agentfarm_*`: **masters** Wortlaut
> (`missing_versioned_agentfarm_source`) nehmen. Er gehört zur neueren Captain-Cook-Redefinition."

`missing_versioned_agentfarm_source` ist **HEADs** Wert und der **ältere**.
`master`s Wert ist `missing_persistent_agentfarm_runtime`.

Belegt:
* `508ef8a` (2026-07-30, „fail closed for missing AgentFarm runtime") führt
  `missing_versioned_agentfarm_source` ein.
* `6698e55` (2026-08-05, **„feat(agentfarm): add versioned runtime source artifact"**) ersetzt
  ihn auf `master` durch `missing_persistent_agentfarm_runtime` — logisch zwingend: die
  versionierte Quelle wurde in genau diesem Commit **angelegt**, also ist sie nicht mehr der
  Blocker.
* Das durch diesen Commit hinzugekommene `spaces/agentfarm/runtime-manifest-v1.json` liegt
  **bereits gestaget im Merge-Ergebnis** und sagt selbst
  `"blocker": "missing_persistent_agentfarm_runtime"`.

**Genommen: `master`s tatsächlicher Wert.** Zwei der drei Signale des Briefs (Zuschreibung
„masters", Begründung „neuere Redefinition") zeigen darauf; nur die Klammer nennt den
falschen String. Hätte ich die Klammer wörtlich befolgt, würde der Baum behaupten, eine
Quelle fehle, die er selbst enthält — und `master`s Manifest widersprechen.

### 5.2 ⚠ Git hat 343 Zeilen still dupliziert — ohne Konfliktmarker

In `brain/the_brain/core/capability_targets.py` haben **beide** Linien denselben n8n-/MCP-Block
unabhängig hinzugefügt, an unterschiedlichen Stellen. Git konnte sie nicht zur Deckung
bringen und hat **beide Kopien aneinandergehängt** — als saubere Auto-Auflösung, ohne
Marker, an einer Stelle außerhalb der zwei gemeldeten Konflikt-Hunks.

Folge: `resolve_registry_execution_target`, `_n8n_event_specs`, `_space_registry_path` und
`_redact_evidence` waren **je zweimal** definiert. Die zweite, engere Definition verdeckte in
Python die erste — `resolve_registry_execution_target("rowboat.status")` lieferte `None`
statt des MCP-Ziels.

* **Entdeckt** durch `test_rowboat_space_contract.py::test_rowboat_status_…`, das auf
  `master` grün ist und im Merge rot wurde.
* **Nachgewiesen als Git-Artefakt, nicht als mein Fehler:** `git merge-file` auf den drei
  Blobs reproduziert exakt dieselben 2005 Zeilen mit den doppelten Definitionen.
  HEAD hat 1250 Zeilen mit je einer Definition, `master` 1655 mit je einer.
* **Behoben** durch `master`s Fassung. Zulässig, weil der Unterschied zwischen Merge-Ergebnis
  und `master` **ausschließlich** diese eine Einfügung war (`diff` meldet genau `1508a1509,1851`
  und sonst nichts) — HEADs Inhalt ist in `master`s Datei vollständig enthalten.
* **Gegenprobe über den ganzen Merge:** alle **444** geänderten `.py`-Dateien per AST auf
  doppelte Top-Level-Definitionen geprüft → **0 Treffer**; alle **47** geänderten
  YAML-/JSON-Dateien auf Parse-Fehler und doppelte Schlüssel → **0 Treffer**.

### 5.3 `master` widerspricht an seiner Spitze sich selbst (Captain Cook)

`master` trägt die Captain-Cook-Redefinition in `config/space_agent_registry.yml`
(`agentfarm`: `agent: null`, `enabled: true`, nur noch `agentfarm.deliver` + `agentfarm.status`),
hat sie aber **nie in die zugehörigen Tests nachgezogen**. Deshalb sind auf `master` selbst
schon 7 Tests rot (§1). Da der Brief die Redefinition ausdrücklich übernehmen lässt
(Punkt 6), wandern sie unverändert in den Merge.

**Ich habe das bewusst nicht „repariert".** Die offene Frage — wohin `agentfarm`-Chat-Intents
routen, wenn der Space keinen Chat-Agenten mehr hat — ist eine Produktentscheidung, keine
Merge-Entscheidung. Konkret betroffen: `bridge/config/space_agent_map.yaml` führt weiterhin
`agentfarm: vibemind`, obwohl die Registry `agent: null` sagt; genau daran scheitert
`test_bridge_map_has_no_unknown_spaces` (`['agentfarm']`). Das ließe sich durch Streichen
der Zeile grün bekommen — das wäre aber eine Routing-Entscheidung, die mir nicht zusteht.
**Zur Nacharbeit vorgelegt.**

### 5.4 `spaces/_navigator/registry.py` war nach der Hunk-Auflösung in sich widersprüchlich

Hier hätte die im Brief benannte Ein-Zeilen-Auflösung eine kaputte Datei ergeben. Die beiden
Linien haben **verschiedene, nicht überlappende** Zeilen derselben Struktur geändert:
HEAD benannte den `SPACES`-Schlüssel `rowboat` → `roarboot`, `master` ließ den Schlüssel und
zog stattdessen `event_prefix`, `stream` und `capabilities` auf `rowboat.*`. Git sah darin
**keinen** Konflikt und übernahm beides — Ergebnis: Schlüssel `"roarboot"` mit Präfix
`"rowboat."`, während die aufgelöste Aliaszeile auf `"rowboat"` zeigt, das es als Schlüssel
gar nicht mehr gab. `resolve_alias("roarboot")` wäre ins Leere gelaufen.
**Deshalb die ganze Datei von `master`.** HEAD steuert hier nichts bei außer der
abgeschafften Umbenennungsrichtung.

### 5.5 Zwei kleinere Ungenauigkeiten des Briefs bei den Registry-Dateien

* Brief-Punkt 6 sagt, beide Seiten hätten „**dieselbe** Tool-Aufwertung". Für
  `search`/`query`/`email_draft`/`meeting_brief`/`deck` stimmt das. Für **`status` nicht**:
  HEAD hat weiterhin `tool: fetch`, `master` hat daraus ein echtes
  `rowboat_status` mit `required_provenance: [approval_ref, cost_ref]` und
  `execution: {kind: mcp, server: spaces-rowboat}` gemacht. `master` ist hier strikt reicher —
  passend dazu, dass nur `master` die Capability `rowboat_status` besitzt.
* Zu `bridge/config/space_agent_map.yaml` legt der Brief nur die Benennung fest. Tatsächlich
  weichen **elf von dreizehn** Zuordnungen ab (`desktop`, `ideas`, `bubbles`, `minibook`,
  `agentfarm`, `n8n`, `schedule`, `video`, `flowzen`, `mirofish` …), nicht nur der
  `roarboot`/`rowboat`-Schlüssel. `master`s Werte genommen — gestützt auf den
  Selbstanspruch der Datei (Spiegel der Registry) und auf das bereits gestagete
  `llm_config.yml.example`, das `master`s Agentennamen bestätigt.

### 5.6 Rename-Doppel: `test_roarboot_space_contract.py`

Beide Linien haben **nach** der Merge-Basis (in der Basis existierte keine der beiden Dateien)
ihre eigene Kopie desselben Tests angelegt — HEAD als `test_roarboot_space_contract.py`,
`master` als `test_rowboat_space_contract.py`. Weil die Dateinamen verschieden sind, sah Git
keinen Konflikt und behielt **beide**. Das ist derselbe Fall wie die acht `AA`-Dateien in
§3.4, nur unsichtbar.

HEADs Kopie behauptet wörtlich `assert "rowboat" not in spaces` und
`spaces["roarboot"]["prefixes"] == ["roarboot."]` — das exakte Gegenteil der
Namensentscheidung; sie wäre im gemergten Baum garantiert rot.

**`master`s Kopie deckt alle drei Tests von HEAD ab** (umbenannt und auf die richtige
Schreibweise gedreht) **und ergänzt drei weitere** (MCP-Bindung, eigene `ROWBOAT_URL`-Truth,
unabhängiger HEAD-Request). Es geht nichts verloren.
**HEADs Kopie entfernt** — genau wie `roarboot_*` von `rowboat_*` abgelöst wurde.

### 5.7 Die Nachprüfung zu Brief-Punkt 1 hält nicht — und das ist in Ordnung

> Brief: „Prüfung danach: kein `sys.path.insert` mehr, das auf `llm_client` zielt, und kein
> relativer `llm_client`-Import in diesen Bäumen. Wenn doch einer übrigbleibt, ist das ein
> Befund, den du berichtest."

**Es bleiben sieben.** In `security/pocs/**` und `ops/pocs/**` steht weiterhin
`from llm_client import get_model` / `get_client_sync` (u. a. `red_blue/config.py`,
`red_blue/infra.py`, `red_blue/main.py`, `defense/log_analyzer/tools.py`,
`ops/pocs/git_agents/run.py`), und die `sys.path.insert`-Zeilen in `ops/pocs/git_agents/**`
sind ebenfalls noch da.

**Das ist kein Auflösungsfehler, sondern eine Fehlannahme des Briefs.** `master`s
LLM-Boundary-Arbeit hat den direkten OpenAI-Client durch **genau diese** Importform ersetzt:
`security/llm_client.py`, `ops/llm_client.py`, `business/llm_client.py`, `devops/llm_client.py`
und `system/llm_client.py` existieren im Baum als die Boundary-Module, auf die diese Importe
zeigen. `from llm_client import …` ist hier also das **Ziel** des Umbaus, nicht sein Überrest.
Diese Dateien waren bereits vor meiner Arbeit aufgelöst und wurden nicht angefasst.

### 5.8 Brief-Punkt 2: die Löschung von `ops/pitch_deck_agent.py` steht **nicht**

Der Brief sagt „die Löschung gilt", nennt aber selbst die Ausnahme: „Wenn du beim Hinsehen
merkst, dass diese Datei doch die einzige Kopie ist, ist das ein Befund — dann nicht löschen,
sondern berichten." **Genau das ist der Fall.** Der Konflikt war bereits vor meiner Arbeit
zugunsten von `master` aufgelöst (die Datei liegt gestaget im Index), und das ist richtig:

* Es gibt **keine** zweite Kopie (`business/poc_pitch_deck/mcp_server.py` ist ein anderer POC).
* `master` hat die Datei im Zuge der LLM-Boundary-Arbeit **geändert**, nicht verwaist gelassen.
* `master` hat dafür eigens `ops/tests/test_pitch_deck_agent_llm_boundary.py` **hinzugefügt** —
  ebenfalls bereits gestaget. Ein Löschen des Moduls würde diesen Test brechen.

**Nicht gelöscht. Hiermit berichtet.**

### 5.9 Kleinigkeit

`git diff --cached --check` meldet `scripts/mcp_servers/captain_cook_mcp.py:229: new blank line
at EOF`. Die Datei ist **byteidentisch mit `master`s Fassung** — vorbestehende Formatierung,
bewusst nicht angefasst, um die Auflösung treu zu halten. `git diff --check` auf dem Working
Tree ist sauber.

---

## 6. Vorgelegt zur Entscheidung

1. **`openfang`-Gitlink ist vorläufig** (`7cdfa3c`, HEADs Wert). Zu ersetzen, sobald die
   openfang-Zusammenführung steht. Nicht verifiziert.
2. **Captain-Cook-Ausrollung ist unvollständig** — 7 der 8 Fehlschläge. Entweder die
   Agentfarm-Contract-Tests auf die Redefinition nachziehen, oder die Redefinition
   zurücknehmen. Ich habe `master`s Stand treu übernommen (Brief-Punkt 6), aber nichts
   erfunden.
3. **`agentfarm` in der Bridge-Map**: Streichen würde `test_bridge_map_has_no_unknown_spaces`
   grün machen — das ist eine Routing-Entscheidung, keine Merge-Entscheidung.
4. **`minibook.status` `required_provenance`**: Registry und
   `test_minibook_space_contract.py` widersprechen sich auf `master`. Unberührt gelassen.
5. **Alt-Benennung aufräumen** (eigener Vorgang, nicht Teil dieses Merges):
   `spaces/rowboat/*roarboot*.py`, `skills/roarboot/`, und
   `data/space_capabilities/` neu generieren, um das `roarboot`/`rowboat`-Doppel aufzulösen.
