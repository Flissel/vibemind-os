# Reward-Coverage: das Tagebuch lernt sehen — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Tagebuch schreibt jetzt (live bewiesen) — aber die meisten Episoden tragen `reward=0.0`, weil ihre Capabilities keine Ground-Truth haben. Dieser Plan schließt die Lücke dort, wo sie echt ist, und legt die Fälle offen, wo sie *nicht* schließbar ist.

**Architecture:** Kein neuer Mechanismus. Wir nutzen den bestehenden `truth:`-Validator (`capability_validator` → `world_observer`, unabhängige Supabase-Re-Query) und erweitern ihn nur dort, wo der Weltzustand prüfbar ist. Für Agenten-Freitext (`openfang:`) gibt es keinen Weltzustand — dafür wird ein **explizites, ehrliches Nicht-Wissen** eingeführt statt eines erfundenen Signals.

**Tech Stack:** Python 3.11, pytest, YAML (capabilities.yaml), Supabase REST.

---

## 1. Der Ist-Zustand — ehrlich gerechnet

„27 von 66" ist eine **irreführende Zahl**. Die 39 Caps ohne `truth:`-Validator zerfallen in vier Gruppen, die grundverschieden zu behandeln sind (alles nachgemessen, nicht geschätzt):

| Gruppe | Anzahl | Braucht Ground-Truth? |
|---|---|---|
| **Kein `execution_target`** (`code_search`, `chitchat`, `code_review`, `security_scan`, …) | **10** | **Nein — sie sind tot.** Ohne Target sind sie nicht ausführbar; der GapSentinel (`capability_gap`, L4) feuert genau dafür. Ein Validator auf einer nie laufenden Cap ist sinnlos. |
| **Read-only** (`idea_count`, `bubble_list`, `idea_find`, `bubble_stats`, …) | **11** | **Kaum.** Ein Lesevorgang ändert die Welt nicht — es gibt nichts nachzuprüfen. Ein „truth:"-Check würde nur bestätigen, dass die Zeile existiert, die man gerade gelesen hat. |
| **Schreibend, `supabase:`** | **9** | **Ja — mechanisch machbar.** Genau wie in der letzten Runde (Coverage 22→27). |
| **`openfang:`-Agenten** (`desktop_skill`, `som_execute`, `browser_automation`, …) | **9** | **Ja — aber der Weltzustand ist unbekannt.** Der Agent liefert Freitext. Hier ist der eigentliche Engpass. |

**Ehrlicher Nenner:** Von 66 Caps sind **10 tot** und **11 reine Leser**. Es bleiben **45 handelnde Caps**, von denen **27 Ground-Truth haben = 60%**. Die reale Lücke ist **18**, nicht 39.

## 2. Der Fund, der diesen Plan mit-verursacht hat: vier Attrappen

`core/supabase_ideas_ops.py:1048`:

```python
async def bubble_noop_op(client, params) -> str:
    """bubble_exit / bubble_generate_embeddings / bubble_promote /
    bubble_delete_all — stateless or out-of-scope for the REST path.
    Return a benign ok so plans don't cascade-fail."""
    return "ok (no-op in supabase-direct mode — stateless or deferred)."
```

**Vier Capabilities zeigen darauf und tun nichts:**

| Cap | Was der Nutzer erwartet | Was passiert |
|---|---|---|
| `bubble_delete_all` | „lösche ALLE Bubbles" | **nichts** — meldet Erfolg |
| `bubble_promote` | Bubble → Projekt befördern | **nichts** — meldet Erfolg |
| `bubble_generate_embeddings` | Embeddings neu bauen | **nichts** — meldet Erfolg |
| `bubble_exit` | Bubble verlassen (Navigation) | nichts — hier ist das **legitim** (zustandslos) |

Die ersten drei sind **Lügen**: der Hop meldet `ok=True`, der Nutzer glaubt, es sei passiert, und das Tagebuch lernt „diese Capability funktioniert". Das ist dieselbe Fake-Signal-Klasse, die Phase 0 überall sonst getötet hat — nur eine Ebene tiefer: nicht der *Reward* lügt, sondern die *Operation*.

**Wichtig:** Ein `truth:`-Validator darauf wäre **kein** Coverage-Theater, sondern das Gegenteil — er würde die Lüge **sichtbar machen** (`verified=False` → `reward=-1.0`). Aber die ehrlichere Lösung ist, die Cap gar nicht erst als verfügbar auszugeben.

## 3. Was dieser Plan tut — und was bewusst nicht

**Tut:**
- Die drei lügenden Attrappen **stilllegen** (Task 1) — eine nicht-verfügbare Capability ist ehrlicher als eine, die lügt.
- Ground-Truth für die **6 echten schreibenden supabase-Caps** (Task 2) → Coverage 27 → 33 von 45 handelnden = **73%**.
- Für die **9 `openfang:`-Caps** einen **fail-closed Default-Contract** (Task 3+4): kein erfundenes Signal, sondern ein *deklarierter* Weltzustands-Check pro Agent-Cap — und wo keiner deklarierbar ist, bleibt das Ergebnis **explizit UNVERIFIED** (`reward=0.0`), sichtbar gemacht statt versteckt.
- Die Coverage-Metrik **ehrlich** machen (Task 5): nach *handelnden* Caps rechnen, tote und lesende getrennt ausweisen.

**Tut bewusst NICHT:**
- Keine Validatoren auf **Leser** (11) — es gäbe nichts zu prüfen; das wäre Coverage-Theater.
- Keine Validatoren auf **tote Caps** (10) — sie laufen nie.
- **Kein LLM-Judge.** Für Agent-Freitext wäre ein LLM-Urteil verlockend, aber es ist ein Selbstbericht mit Extraschritten — genau das, was die Doktrin verbietet („ok/reward NUR aus unabhängiger Ground-Truth"). Wo kein Weltzustand prüfbar ist, sagen wir **„weiß ich nicht"**, und das ist die richtige Antwort.

## Global Constraints

- Git **immer** über PowerShell (git-bash crasht). Conventional Commits. Submodul-Branch `feat/mcp-tool-hub`; äußeres Repo `master` (per Projekt-CLAUDE.md ausdrücklich erlaubt).
- TDD zwingend: Test zuerst, **RED beobachten und den Output festhalten**, dann GREEN.
- Regressions-Baseline (aus `vibemind-os/brain/the_brain`, **explizite Dateiliste** — `pytest tests/` über das ganze Verzeichnis hat einen **vorbestehenden** Collection-Crash, der nichts mit dieser Arbeit zu tun hat):
  `python -m pytest tests/test_hop_learning_signal.py tests/test_multihop_kotlin_adapter.py tests/test_multihop_diary_queue.py tests/test_multihop_diary_drain.py tests/test_multihop_ingest_e2e.py tests/test_diary_stats_endpoint.py tests/test_capability_truth_coverage.py tests/test_truth_template_resolves.py tests/test_task_class_clusterer.py tests/test_kotlin_graph.py tests/test_dual_graph.py tests/test_multihop_response_contract.py -q` → aktuell **196 passed**.
- **Niemals ein Signal erfinden.** Ein Validator, der strukturell nie feuern kann, ist schlimmer als keiner — er bläht die Metrik auf. (Das ist letzte Runde real passiert: zwei Validatoren mit einem `{result_title2}`, das die Ops nie lieferten. Gefixt wurden die *Ops*, nicht die Metrik.)
- `capabilities.yaml` ist **hot-reloadbar**, aber im Swarm über eine **immutable Config** gemountet: eine Inhaltsänderung braucht einen **Namensbump** (`brain_capabilities_vN` → `_vN+1`) in `infra/swarm/vibemind-stack.yml`, sonst serviert der Stack weiter den alten Inhalt. **Verifiziere nach dem Deploy im Container**, wie viele `kind: truth:` ankommen — nicht im Repo nachzählen.
- Tests dürfen **nie** in `brain/the_brain/data/` schreiben (`git status --porcelain brain/the_brain/data/` muss sauber bleiben).

---

### Task 1: Die drei lügenden Attrappen stilllegen

**Warum zuerst:** Solange sie Erfolg vortäuschen, vergiften sie Nutzererwartung *und* Tagebuch. Eine Capability, die ehrlich „geht nicht" sagt, ist besser als eine, die lügt — der Planner weicht dann aus, und der GapSentinel meldet die Lücke.

**Files:**
- Modify: `core/supabase_ideas_ops.py` (die `bubble_noop_op`)
- Modify: `data/capabilities.yaml`
- Test: `tests/test_noop_capabilities_are_honest.py` (neu)

**Interfaces:**
- Consumes: `core/capability_gap.py` (L4 GapSentinel) — er feuert bereits, wenn eine Cap **kein** `execution_target` hat (`plan_executor.py`, „no execution target for capability"). Genau dieser Pfad soll greifen.
- Produces: `bubble_exit` behält `bubble.noop` (dort ist der No-op **korrekt** — zustandslose Navigation). Die anderen drei verlieren ihr Target.

- [ ] **Step 1: Failing Test schreiben**

```python
"""Phase 1 — Attrappen sind ehrlich: eine Cap, die nichts tut, darf keinen
Erfolg melden.

bubble_noop_op gab "ok (no-op ...)" zurück, damit Pläne nicht scheitern.
Für bubble_promote / bubble_delete_all / bubble_generate_embeddings ist das
eine LÜGE: der Nutzer glaubt, es sei passiert; das Tagebuch lernt "diese
Capability funktioniert". bubble_exit ist der einzige legitime No-op
(zustandslose Navigation).
"""
import yaml
from pathlib import Path

CAPS = yaml.safe_load(
    (Path(__file__).resolve().parents[1] / "data" / "capabilities.yaml")
    .read_text(encoding="utf-8")
)
BY_NAME = {c["capability"]: c for c in CAPS}

LYING_STUBS = ("bubble_promote", "bubble_delete_all", "bubble_generate_embeddings")


class TestLyingStubsAreDisabled:
    def test_they_have_no_execution_target(self):
        """Ohne Target kann der Planner sie nicht dispatchen und der
        GapSentinel meldet die Lücke — statt Erfolg vorzutäuschen."""
        for name in LYING_STUBS:
            cap = BY_NAME[name]
            assert not cap.get("execution_target"), (
                f"{name} zeigt noch auf {cap.get('execution_target')} — "
                f"es täuscht weiterhin Erfolg vor"
            )

    def test_they_say_why_in_the_description(self):
        for name in LYING_STUBS:
            desc = (BY_NAME[name].get("description") or "").lower()
            assert "not implemented" in desc or "nicht implementiert" in desc, (
                f"{name}: die Beschreibung muss sagen, dass es nicht geht"
            )

    def test_bubble_exit_keeps_its_noop(self):
        """bubble_exit ist der EINZIGE legitime No-op: zustandslose
        Navigation, es gibt nichts zu schreiben."""
        assert BY_NAME["bubble_exit"]["execution_target"] == "supabase:bubble.noop"


class TestNoopOpNoLongerServesWriters:
    def test_docstring_names_only_bubble_exit(self):
        src = (Path(__file__).resolve().parents[1] / "core" /
               "supabase_ideas_ops.py").read_text(encoding="utf-8")
        i = src.index("async def bubble_noop_op")
        doc = src[i:i + 500]
        for name in ("bubble_promote", "bubble_delete_all",
                     "bubble_generate_embeddings"):
            assert name not in doc, (
                f"bubble_noop_op bedient laut Docstring noch {name}"
            )
```

- [ ] **Step 2: RED beobachten**

Run: `cd C:/Users/User/Desktop/Vibemind_V1/vibemind-os/brain/the_brain && python -m pytest tests/test_noop_capabilities_are_honest.py -q`
Expected: 3 FAIL (die drei haben noch `supabase:bubble.noop`; die Beschreibungen sagen nichts; der Docstring nennt sie). Ausgabe festhalten.

- [ ] **Step 3: Umsetzen**
  - In `data/capabilities.yaml` bei `bubble_promote`, `bubble_delete_all`, `bubble_generate_embeddings`: die `execution_target:`-Zeile **entfernen** (nicht auskommentieren — ein leeres Target ist der Zustand, den der GapSentinel erkennt) und in `description:` ergänzen: `NOT IMPLEMENTED (supabase-direct path): the op was a no-op that faked success — disabled 2026-07-14 so the planner routes elsewhere and the gap is reported.`
  - In `core/supabase_ideas_ops.py`: `bubble_noop_op`-Docstring auf **`bubble_exit`** reduzieren und einen Kommentar setzen, warum: *ein No-op darf nur dort stehen, wo es wirklich nichts zu tun gibt; für Schreib-Ops ist ein erfundenes „ok" ein Fake-Signal.*
  - **Nicht** die Funktion löschen — `bubble_exit` braucht sie.

- [ ] **Step 4: GREEN + Regression**

Run: `python -m pytest tests/test_noop_capabilities_are_honest.py -q` → 4 passed
Run: Regressions-Kommando (Global Constraints) → **196 passed** (unverändert)
Run: `python -m pytest tests/test_capability_truth_coverage.py -q` → muss grün bleiben (die Cap-Zahl 66 ändert sich nicht, nur drei Targets fallen weg)

- [ ] **Step 5: Commit**

```powershell
cd C:\Users\User\Desktop\Vibemind_V1\vibemind-os
git branch --show-current   # feat/mcp-tool-hub
git add brain/the_brain/core/supabase_ideas_ops.py brain/the_brain/data/capabilities.yaml brain/the_brain/tests/test_noop_capabilities_are_honest.py
git commit -m "fix(caps): disable three no-op capabilities that faked success"
```

---

### Task 2: Ground-Truth für die echten schreibenden supabase-Caps

**Warum:** Nach Task 1 bleiben **6** schreibende supabase-Caps ohne Ground-Truth (die drei Attrappen sind raus, `component_note_write` behält bewusst seinen blockierenden `rule:`-Validator — siehe letzte Runde). Sie sind mechanisch machbar, exakt wie beim Sprung 22→27.

**Die 6:** `bubble_score`, `component_requirements`, `idea_expand`, `idea_classify`, `idea_format_revert` — plus **einer nach Prüfung**: `component_note_write` bleibt außen vor (rule:+block, bewusst). Die tatsächliche Liste **beim Implementieren neu ermitteln** (Task 1 hat die Menge verändert) mit:

```bash
python -c "
import yaml
caps = yaml.safe_load(open('data/capabilities.yaml', encoding='utf-8'))
def truth(c):
    v=c.get('validator'); return isinstance(v,dict) and str(v.get('kind','')).startswith('truth:')
READ=('list','count','find','stats','get','explain','current','analyze','exit')
for c in caps:
    t=c.get('execution_target') or ''
    if t.startswith('supabase:') and not truth(c) and not any(h in c['capability'] for h in READ):
        print(c['capability'], '->', t, '| validator:', (c.get('validator') or {}).get('kind','KEIN'))
"
```

**Files:**
- Modify: `data/capabilities.yaml`
- Modify: `tests/test_capability_truth_coverage.py` (Ratchet hochziehen)
- Test: `tests/test_truth_template_resolves.py` (erweitern — siehe unten, das ist der Kern)

**Interfaces:**
- Consumes: die bestehenden Validator-Arten. **Vor dem Schreiben** die Vorbilder lesen und 1:1 spiegeln: `bubble_create` (ROW-Muster, `kind: truth:supabase_row`) und `idea_connect` (EDGE-Muster, `kind: truth:supabase_edge`); `idea_move` nutzt `truth:supabase_node_in_bubble`. **Nur bereits implementierte `kind:`-Werte verwenden** (`core/capability_validator.py::_run_truth_validator`, `core/world_observer.py`). Passt für eine Cap keine, **BLOCKED melden** statt eine Art zu erfinden.
- Produces: Coverage 27 → 33.

- [ ] **Step 1: DIE FALLE AUS DER LETZTEN RUNDE ZUERST ENTSCHÄRFEN**

Ein Validator, dessen Postcondition-Platzhalter (`{result_id}`, `{result_title}`, `{result_title2}`) sich aus dem Ergebnis-String der Op **nie** auflösen lässt, feuert **nie** — er liefert dauerhaft UNVERIFIED und bläht nur die Metrik auf. Genau das ist letzte Runde zweimal passiert.

**Also für JEDE der 6 Caps ZUERST prüfen**, was ihre Op wirklich zurückgibt (`core/supabase_ideas_ops.py`), und **erst dann** die Postcondition wählen. Der Templating-Code steht in `core/capability_validator.py::_template_postcondition` (er füllt `{result_title}`/`{result_title2}` aus **gequoteten** Teilstrings des Ergebnisses, in Reihenfolge; `{result_id}` per `id=`-Regex).

Für jede Cap in `tests/test_truth_template_resolves.py` einen Test ergänzen, der **die echte Op** (mit Stub-Client, wie dort bereits vorhanden) aufruft und deren **tatsächlichen Rückgabe-String** durch `_template_postcondition` schickt — Assertion: **kein `{` überlebt**. Liefert die Op die nötigen Angaben nicht, muss die **Op gepatcht** werden (sie soll den Wert quotieren/mitgeben) — **nicht** der Test, und **nicht** die Metrik.

- [ ] **Step 2: RED** — `python -m pytest tests/test_truth_template_resolves.py -q`: die neuen Auflösungs-Tests müssen gegen die heutigen Op-Strings **fehlschlagen**, wo Angaben fehlen. Ausgabe festhalten (sie ist der Beweis, dass die Validatoren sonst tot wären).

- [ ] **Step 3: Umsetzen** — Ops patchen (wo nötig), dann die 6 Validator-Blöcke in `capabilities.yaml` ergänzen, jeweils mit `on_fail: report` und der Kommentarzeile
  `# truth: coverage lift 2026-07-14 — ground truth via world_observer re-query`.

- [ ] **Step 4: Ratchet hochziehen** — in `tests/test_capability_truth_coverage.py`: `MIN_TRUTH_VALIDATORS = 33`, `EXPECTED_NEW` um die 6 Namen ergänzen, Docstring-Trajektorie fortschreiben.

- [ ] **Step 5: GREEN + Regression**

Run: `python -m pytest tests/test_capability_truth_coverage.py tests/test_truth_template_resolves.py -q` → grün
Run: Regressions-Kommando → **196 + neue Tests**
Run: `git status --porcelain brain/the_brain/data/` → leer

- [ ] **Step 6: Commit**

```powershell
cd C:\Users\User\Desktop\Vibemind_V1\vibemind-os
git add brain/the_brain/data/capabilities.yaml brain/the_brain/core/supabase_ideas_ops.py brain/the_brain/tests/test_capability_truth_coverage.py brain/the_brain/tests/test_truth_template_resolves.py
git commit -m "feat(caps): truth coverage 27->33 (real supabase writers, templates verified resolvable)"
```

---

### Task 3: `outcome_contract:` — ein deklarierbarer Weltzustands-Check für Agenten-Caps

**Warum:** Die 9 `openfang:`-Caps liefern **Freitext**. Ein Agent, der sagt „Ich habe die Datei angelegt", ist ein **Selbstbericht** — genau das, was die Doktrin verbietet. Aber viele dieser Caps **hinterlassen eine Spur in der Welt**: eine Datei, eine Supabase-Zeile, ein OpenFang-Agent. Diese Spur ist prüfbar — sie muss nur **deklariert** werden.

Das Schema existiert noch nicht: heute kennt `capabilities.yaml` nur `validator: {kind: truth:...}`, dessen Postcondition sich aus dem **Ergebnis-String** speist. Für Agenten brauchen wir eine Postcondition, die sich aus den **Hop-Parametern** speist (was sollte entstehen?), nicht aus dem, was der Agent behauptet.

**Files:**
- Modify: `core/capability_validator.py` (neue Validator-Art)
- Modify: `core/world_observer.py` (falls ein neuer Check nötig — **erst lesen**, `truth:file_exists` existiert bereits, siehe `coding_task`)
- Modify: `data/capabilities.yaml` (Schema + erste Caps)
- Test: `tests/test_outcome_contract.py` (neu)

**Interfaces:**
- Consumes: `world_observer.observe(...)` und die vorhandenen Check-Arten. **Zuerst inventarisieren**, welche `truth:`-Arten es gibt (`grep -rn "truth:" core/capability_validator.py core/world_observer.py`) — mindestens `supabase_row`, `supabase_edge`, `supabase_node_in_bubble`, `file_exists`.
- Produces: eine Validator-Art, deren Postcondition **aus den Hop-Argumenten** getemplatet wird (`{arg.<name>}`), nicht aus dem Ergebnis. Damit prüft sie, **was passieren sollte**, statt zu glauben, was der Agent sagt.

- [ ] **Step 1: Zuerst ermitteln, was überhaupt prüfbar ist.** Für jede der 9 `openfang:`-Caps beantworten (und im Task-Report festhalten):

| Cap | Agent | Hinterlässt sie eine prüfbare Spur? |
|---|---|---|
| `openfang_agent_create` | fungus-search | **Ja** — ein OpenFang-Agent existiert danach (`GET :4200/api/agents`) |
| `coding_task` | openclaude-coder | **Ja** — hat bereits `truth:file_exists` (Vorbild!) |
| `desktop_skill` | skill-coordinator | ? |
| `browser_automation` | openclaw-visible | ? |
| `rowboat_chat` | rowboat-chat | ? |
| `buergergeld_parse_eingang` | brain-buergergeld | ? |
| `buergergeld_status_abgleich` | brain-buergergeld | ? |
| `som_plan` / `som_resume` / `som_execute` | som-* | **wahrscheinlich nein** (async, Ergebnis kommt per Telegram) |

**Ehrlichkeit ist hier der Punkt:** Wo die Antwort „nein" lautet, bekommt die Cap **keinen** Validator und ihre Hops bleiben `reward=0.0` (UNVERIFIED). Das ist die **richtige** Antwort — nicht ein erfundener Check. Der Task-Report muss diese Caps namentlich als „strukturell nicht verifizierbar" ausweisen.

- [ ] **Step 2: Failing Tests + Implementierung der neuen Validator-Art.** (Der genaue Test-Code hängt vom Ergebnis aus Step 1 ab — deshalb ist Step 1 ein eigener Schritt mit Report. **Erst nach Step 1 weiterspezifizieren.**)

Grundgerüst der Art: `kind: truth:agent_outcome` mit einer Postcondition, die aus **Hop-Argumenten** getemplatet wird, z.B. für `openfang_agent_create`:
```yaml
  validator:
    kind: truth:agent_outcome
    check: openfang_agent_present     # neuer world_observer-Check
    expect: present
    match: "name={arg.agent_name}"
    on_fail: report
```
Der Check fragt `GET :4200/api/agents` und prüft, ob der Agent da ist — **unabhängig davon, was der Agent geantwortet hat**.

- [ ] **Step 3: fail-closed Default.** Für Agent-Caps **ohne** deklarierten `outcome_contract` gilt weiterhin: kein Validator → `contract_pass=None` → `reward=0.0`. **Kein** Fallback auf „ok=True heißt Erfolg" — das wäre der did-not-throw-Fake, den Phase 0 getötet hat. Ein Test muss das festnageln.

- [ ] **Step 4: GREEN + Regression + Commit** (Kommandos wie in Task 2).

---

### Task 4: Die Coverage-Metrik ehrlich machen

**Warum:** „27/66" suggeriert 41% und ist damit **falsch alarmierend**; „33/45 handelnde" ist die Zahl, an der man steuern kann. Ohne diese Trennung optimiert man auf eine Kennzahl, die tote und lesende Caps mitzählt.

**Files:**
- Modify: `tests/test_capability_truth_coverage.py`
- Modify: `web/routers/introspection.py` (`/api/diary/stats` um einen `coverage`-Block)

**Interfaces:**
- Produces: `GET /api/diary/stats` → zusätzlich
  ```json
  "coverage": {"actionable": int, "with_truth": int, "pct": float,
               "read_only": int, "no_target": int, "total": int}
  ```
  Damit ist die Reward-Blindheit **im Betrieb sichtbar**, nicht nur in einem Test.

- [ ] **Step 1: Failing Test** — der Ratchet prüft künftig **`with_truth / actionable`**, nicht mehr `/total`. Die Klassifikation (tot / read-only / handelnd) als kleine, getestete Hilfsfunktion — **nicht** dupliziert im Endpoint.
- [ ] **Step 2–4:** RED → implementieren → GREEN + Regression → Commit.

---

### Task 5: Deploy + Live-Beweis (Controller, nicht Subagent)

**Warum als eigener Task:** Fasst Produktion an. Und `capabilities.yaml` liegt im Swarm hinter einer **immutable Config** — eine Repo-Änderung allein ändert **nichts**.

- [ ] **Step 1: Config-Bump.** In `infra/swarm/vibemind-stack.yml` den Namen `brain_capabilities_vN` → `_vN+1` bumpen — **an ALLEN Verwendungsstellen** (aktuell vier Services!) **und** in der `configs:`-Definition. `grep -n "brain_capabilities_v<alt>"` → muss **0 Treffer** ergeben.
- [ ] **Step 2: Image bauen.** Vorher `docker builder prune -af` (der letzte Build riss den Swarm mit; nach dem Prune lief er durch):
  ```powershell
  docker build -t vibemind-brain-core:latest -f vibemind-os/brain/the_brain/Dockerfile vibemind-os/
  ```
- [ ] **Step 3: Deploy.** `docker stack deploy -c infra/swarm/vibemind-stack.yml vibemind` (**nie** `docker service update` — das räumt den Stack ab). Exit-Code prüfen.
- [ ] **Step 4: Im CONTAINER verifizieren** (nicht im Repo!):
  ```bash
  cid=$(docker ps --filter "name=vibemind_brain-core" -q | head -1)
  docker exec $cid grep -c "kind: truth:" /app/data/capabilities.yaml   # muss 33 sein
  ```
  Kommt weiter die alte Zahl → der Config-Bump hat nicht gegriffen; **hier stoppen**.
- [ ] **Step 5: Der Beweis.** Einen Intent fahren, der eine der NEU abgedeckten Caps trifft, und im Tagebuch prüfen, dass die Episode **`reward=+1.0`** trägt (nicht 0.0):
  ```bash
  curl -s -X POST http://127.0.0.1:5000/api/multihop/execute \
    -H "Content-Type: application/json" -d '{"intent": "<passender Intent>"}'
  curl -s http://127.0.0.1:5000/api/diary/stats     # coverage-Block + queue
  ```
  Und für eine **nicht** abgedeckte Agent-Cap zeigen, dass sie ehrlich `reward=0.0` (UNVERIFIED) liefert — **kein** erfundener Erfolg.
- [ ] **Step 6: Protokoll** `docs/plans/2026-07-<TT>-reward-coverage-live-proof.md` mit den Rohausgaben und einem **ehrlichen** Fazit inkl. der Caps, die strukturell unverifizierbar bleiben. Negativbefunde sind Ergebnisse.

---

## Explizit NICHT in diesem Plan

- **Die 10 toten Caps** (ohne Target). Sie sind ein eigenes Thema: entweder implementieren oder entfernen. Der GapSentinel meldet sie bereits.
- **LLM-Judge für Agenten-Freitext.** Ein Selbstbericht mit Extraschritten. Wo kein Weltzustand prüfbar ist, ist „weiß ich nicht" die richtige Antwort.
- **`VOICE_BRAIN_MULTIHOP=true`.** Würde die Datensammlung um Größenordnungen beschleunigen, ändert aber das Nutzerverhalten — eine Produktentscheidung, kein Coverage-Thema.
- **Phase-3-Bandit.** Bleibt gegated auf Coverage + Trajektorien-Volumen.
