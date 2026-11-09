# Pilot `ideas` — Quellen- und Suchprotokoll

Stand: vibemind-os@3f5e15dab3e1, vibemind-os/voice@a506ca2e6452, vibemind-os/openfang@1d5f8967791f
Erhoben: 2026-09-24

Zweck: Hier steht, woher jede Aussage im Dokument stammt. Das ist das Messmaterial für Teil C
(Spec, Abschnitt 4 C, Schritt 7). `docs/spaces/` ist aus jedem Suchkorpus ausgeschlossen, damit
die Simulation diese Datei nicht abschreibt.

Vorgehen: Jede Frage ging zuerst an context-v2 (`context_search`), dann an den Code. Gelesen wurde
nur der committete Stand über `git show <rev>:<pfad>` und `git grep <rev>`, nie das
Arbeitsverzeichnis. Die Erhebung am Code übernahm ein Such-Agent, der nur liest. Seine tragenden
Belege wurden danach von Hand gegengeprüft: Registry 62-101, `vibemind_db_mcp.py` 64-81 und
246-259, `capabilities.yaml` 538-573, Migration 11-25/66-89, Symbolzeilen per `git grep`.

## Korpora von context-v2 (2026-09-24)

| Korpus | Dokumente | Inhalt |
| --- | --- | --- |
| brain-code | 16.497 | nur `the_brain` |
| secondbrain | 192 | Vault-Notizen |
| sessions | 5.025 | Session-Zusammenfassungen |

Es gibt keinen Korpus über `vibemind-os/spaces`, `voice` oder `supabase/migrations`. Die Suche ist
reines BM25 (Satz-Einbettungen abgeschaltet).

## Fragen je Vorlagenabschnitt

| # | Abschnitt | Frage | context-v2: bester Treffer | brauchbar? | tatsächliche Quelle |
| --- | --- | --- | --- | --- | --- |
| 1 | Steckbrief | Agent, MCP-Server, Präfixe | brain-code `introspection.py` `agent_tools` (44,1) | nein, nur generischer Agentenleser | `config/space_agent_registry.yml:62-70` |
| 2 | Steckbrief | Was stellt `spaces/ideas/mcp_server.py` bereit? | brain-code `capability_targets.supported_kinds` (37,0) | teilweise, nennt `supabase:idea.connect` | `spaces/ideas/mcp_server.py:22-181` |
| 3 | Steckbrief | Wo liegen die `db_ideas_*`-Tools? | brain-code `seed_self_awareness.collect_sources` (21,7) | nein | `voice/python/vibemind_db_mcp.py:64-81, 246-259` |
| 4 | Ecosystem Fit | Wer ruft `ideas` auf, wen ruft `ideas` auf? | brain-code `brain_server._lifespan` (30,3) | ja, Rowboat-Ingest in den KG | `brain_server.py:1361-1395`, `rowboat_reader.py:79-92` |
| 5 | Events | Welche `idea.*`-Events haben einen Handler? | brain-code `SupabaseExecutor` (27,5) | ja, zweiter Ausführungsweg entdeckt | `capability_targets.py:1636-1721`, `capabilities.yaml` |
| 6 | Events | Abdeckung laut `/api/events/mapping` | brain-code `agent_yaml_registry.auto_seed` (28,0) | teilweise | Live-Messung: HTTP 500, nicht messbar |
| 7 | Datenverarbeitung | Eingangsfelder | brain-code `RowboatIdea` (53,8) | teilweise (Rowboat-Seite) | `vibemind_db_mcp.py:70-74`, `mcp_server.py:110-124` |
| 8 | Datenverarbeitung | Schritte Event → DB-Zeile | brain-code `SupabaseExecutor._call` (53,9), `enter_op` (53,2) | ja | `capability_targets.py:1636-1721`, `supabase_ideas_ops.py:293-430` |
| 9 | Datenverarbeitung | Tabellen, Speicherorte | brain-code `IdeasClient._supabase_state` (60,8) | ja, nennt die vier Tabellen | `supabase/migrations/20260411_init_vibemind.sql:11-89` |
| 10 | Datenverarbeitung | Personenbezogene Felder | brain-code `brain_classify` (21,4) | nein | Migration `20260411_init_vibemind.sql:11-25, 66-77` |
| 11 | Roadmap | Pläne und Specs zu `ideas` | secondbrain `Roadmap_MOC` (13,1) | nein | `docs/superpowers/` (Such-Agent, Dateiliste) |
| 12 | Umsetzungsstand | Was ist belegt oder bekannt kaputt? | brain-code Test `test_exec_hop_verified_truth_validator` (33,1) | nein | `supabase_ideas_ops.py:250-291`, `capabilities.yaml:552-556` |

Bilanz: **context-v2 war bei 4 von 12 Fragen brauchbar** (4, 5, 8, 9) und bei 3 teilweise (2, 6, 7).
Bei allen 12 musste der Code direkt gelesen werden, um einen Beleg zum festen Stand zu bekommen.

## Befunde, die überraschten

1. **Der context-v2-Korpus ist veraltet.** `SupabaseExecutor` steht im Korpus bei Zeile 706, zum
   Stand `e65766f` aber bei Zeile 1636. Treffer aus context-v2 taugen damit als Wegweiser, nicht
   als Beleg. Für Teil C heißt das: Der Korpus muss zum Stand des Dokuments gebaut werden.
2. **`ideas` hat zwei Ausführungswege, die in verschiedene Tabellen schreiben.** Die Registry
   nennt für `idea.create` das Tool `db_ideas_create`, das in die Tabelle `ideas` schreibt (dort
   stehen Bubbles). Die Brain-Capability für dasselbe Event (`idea_add`, `supabase:idea.create`)
   schreibt in `canvas_nodes` (dort stehen die Ideen-Notizen). Ohne den zweiten Weg hätte das
   Dokument den Space falsch beschrieben.
3. **Parameternamen passen nicht zusammen.** Die Registry verlangt `idea_id`, die Tools verlangen
   `id`. `idea.find` verlangt `query`, das Tool kennt nur `filter`.
4. **Die Agentenvorlage `brain-ideas` nennt `provider = "openai"`, `gpt-4o-mini`.** Das passt
   nicht zur Vorgabe „kein API-Budget“ und kommt auf die Prüfliste.
5. **`/api/events/mapping` antwortet mit HTTP 500.** Die Abdeckung nach dem Brain lässt sich
   derzeit nicht messen.

## Änderungen an der Vorlage (für Teil C festhalten)

- Die Vorlage braucht mehrere Repos in `Stand:` (hier drei). Die Prüfung beherrscht das bereits.
- Die Event-Tabelle hat 25 Zeilen (die Registry-Zeilen 71-100 sind 29, weil `idea.connect` fünf Zeilen belegt). Bei so vielen Events wäre eine Gruppierung („Formatieren:
  7 Events, gleicher Weg“) lesbarer. Die Entscheidung liegt beim User (Haltepunkt 1).
- Die Prüfliste zerlegt Absätze in einzelne Zeilen (ein Absatz über drei Zeilen ergibt drei Punkte). Sie sollte ganze Absätze als einen Punkt listen.
