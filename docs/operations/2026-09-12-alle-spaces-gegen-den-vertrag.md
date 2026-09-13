# Alle Spaces gegen den Vertrag — Bilanz

**Datum:** 2026-09-12
**Verträge:** `spaces/_contract/*.contract.yaml` (11 Stück)
**Werkzeug:** `space_cli intake --contract <datei> --target .`

Die Registry führt 13 Spaces. Elf haben jetzt einen schlanken Vertrag nach
ADR-0004; zwei haben keinen, und das ist der Befund, nicht das Versäumnis.
`writes` ist überall am Code abgelesen, nie am Namen geraten.

## Bilanz

| Space     | Capabilities | schreibend | mit `truth:` | Lücken |
|-----------|--------------|-----------|--------------|--------|
| bubbles   | 13           | 7         | 5            | 2 |
| coding    | 8            | 3         | 0            | 5 |
| desktop   | 1            | 1         | 0            | 1 |
| flowzen   | 3            | 1         | 0            | 1 |
| ideas     | 32           | 24        | 22           | 2 |
| minibook  | 4            | 2         | 0            | 2 |
| mirofish  | 7            | 4         | 0            | 4 |
| research  | 4            | 1         | 0            | 1 |
| rowboat   | 7            | 0         | 0            | **0** |
| schedule  | 7            | 5         | 0            | 5 |
| video     | 8            | 6         | 0            | 6 |
| **Summe** | **94**       | **54**    | **27**       | **29** |

Die 29 Lücken sind von zwei Arten: **25** schreibende Capabilities ohne
unabhängigen `truth:`-Validator und **4** ohne Ausführungsziel
(`bubble_delete_all`, `bubble_generate_embeddings`, `code_review`,
`code_search` — die drei letzten nach der bubble_noop_op-Doktrin vom
14.07. bewusst entfernt, damit eine ehrliche Lücke gemeldet wird).

94 der 123 Capabilities sind damit erfasst. Von den übrigen 29 sind 10
abgeschaltet, 19 tragen keinen Space-Präfix (siehe unten).

## Der eine Satz, auf den es hinausläuft

**Die Hälfte aller schreibenden Capabilities hat keinen unabhängigen Beleg —
und die andere Hälfte liegt vollständig in zwei Spaces.**

Von 54 schreibenden haben 27 einen `truth:`-Validator: 22 in `ideas`, 5 in
`bubbles`. In den neun anderen Spaces: **null**. Die Verdrahtung vom 14.07.
(21 Capabilities auf Re-Query umgestellt) hat `ideas` und `bubbles` erreicht
und ist dort stehen geblieben.

## Was die Messung dabei sichtbar gemacht hat

### 1. Der Beleg existiert oft — nur an der falschen Stelle

Drei Spaces prüfen ihren eigenen Schreibvorgang bereits unabhängig nach,
ohne dass die Capability-Ebene etwas davon weiß:

* **flowzen** — `accept_op` schreibt nach `/flowzen_activity` und liest die
  Zeile danach per `GET ?id=eq.{id}` zurück, mit Vergleich des Inhalts
  (`flowzen_ops.py:78`). Schlägt das fehl, ist die Operation `failed`.
* **video** — `_run_job` zieht vor dem Lauf einen Schnappschuss der
  Artefakt-Wurzeln, vergleicht danach und prüft jeden Pfad mit
  `is_file()`. Ohne Nachweis wird der Job `failed`.
* **schedule** — `cancel`/`modify`/`snooze` lesen über `update()` zurück
  (am 11.09. festgestellt).

Von der Capability-Ebene aus ist keiner dieser drei von einem
Selbstberichter zu unterscheiden. **Das ist der eigentliche Auftrag: nicht
27 Validatoren erfinden, sondern die vorhandenen sichtbar machen.**

Bei `video` geht das heute nicht einmal: `execute_video` kehrt sofort mit
`accepted` zurück, der Beleg landet im Job-Datensatz. Ein
`truth:file_exists` direkt danach schlüge fehl. Dort fehlt ein **Check-Typ**
(„lies den Job-Beleg nach"), kein Eintrag — `world_observer` hat 8 Checks,
keiner liest eine Job-Datei.

### 2. Das Provenance-Tor greift fast nirgends

```
Registry-Ereignisse gesamt : 129
davon execution.kind: mcp  :   6
davon mit Provenance       :   6
```

Nur `bubble.create`, `idea.connect`, `research.summarize`,
`research.to_idea`, `rowboat.status`, `minibook.status` laufen unter dem
geschlossenen Tor. Der Grund ist mechanisch: `canonical_space_event_id`
kennt nur 18 fest eingetragene Namen (`capability_targets.py:1268`); jede
andere Capability bildet auf sich selbst ab, trifft keinen dotted
Registry-Schlüssel, und `_mcp_authority` — die einzige Stelle, die
Provenance erzwingt (`:1156`) — wird nie betreten.

Nebenwirkung: `research.summarize` und `research.to_idea` **deklarieren**
geschlossene Provenance in der Registry, aber die Capabilities heißen
`research_summarize`/`research_to_idea` und gehen über `research:` —
die Deklaration feuert nie.

### 3. Namen lügen in beide Richtungen

* `rowboat_email_draft`, `rowboat_deck`, `rowboat_meeting_brief` klingen
  erzeugend und legen **nichts** ab — alle drei setzen nur einen Prompt ab.
* `mirofish.graph.search` und `mirofish.interview` sind POSTs und schreiben
  trotzdem nicht (die Handler enthalten kein commit/INSERT).
* `idea_expand` schreibt (`create_canvas_node`), obwohl „expand" nach Lesen
  klingt — am 11.09. gefunden.

Jede dieser Einordnungen stammt aus dem Code, keine aus dem Namen.

## Die zwei Spaces ohne Vertrag

**`n8n`** — registriert, `enabled: true`, Agent `brain-n8n`, acht Ereignisse
(`n8n.delete`, `n8n.activate`, `n8n.deactivate`, `n8n.execute` und weitere).
**Null Capabilities.** Keine einzige Capability in `capabilities.yaml` trägt
den Präfix `n8n` oder zeigt auf `brain-n8n`. Der Space ist über die
Capability-Ebene nicht erreichbar — es gibt nichts zu beanspruchen, also
auch keinen Vertrag.

**`agentfarm`** — `agent: null`, `generate_agent: false`, Laufzeit als MCP
unter `spaces/captain_cook`. Zwei Ereignisse (`agentfarm.deliver`,
`agentfarm.status`) zeigen auf echte Werkzeuge in
`scripts/mcp_servers/captain_cook_mcp.py`. Die acht Capabilities mit dem
Namen `agentfarm_*` sind aber **alle abgeschaltet und ohne Ziel**, und
`spaces/agentfarm/agentfarm/` enthält nur `__init__.py`. `writes` ließe sich
für sie nur raten — es gibt keinen Code dahinter. Deshalb kein Vertrag,
sondern diese Notiz.

## Die 19 ohne Space

```
security_scan · incident_response · log_analysis · knowledge_query
email_action · site_check · chitchat · architecture_question
component_requirements · component_note_write
coding_task · coding_task_anthropic · browser_automation
openfang_agent_create · buergergeld_parse_eingang
buergergeld_status_abgleich · som_plan · som_resume · som_execute
```

Sieben davon haben gar kein Ausführungsziel. `openfang_agent_create` zeigt
auf einen Agenten `fungus-search`, den es nicht gibt (am 10.09. gemeldet).
`component_note_write` schreibt (`supabase:idea.create`) und gehört
vermutlich zu `ideas` — das ist eine Zuordnungsfrage, keine Messfrage.

## Belege

* `space_cli intake` gegen alle elf Verträge, Zahlen oben.
* 158 Space-Tests grün (`pytest tests/test_space_*.py`).
* OpenFang lief bei dieser Messung **nicht** — alles, was einen laufenden
  Daemon bräuchte (existiert `skill-coordinator` wirklich?), ist offen und
  als offen gekennzeichnet.
