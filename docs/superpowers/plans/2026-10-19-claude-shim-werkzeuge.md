# Claude-Code-Shim mit Werkzeugaufrufen – Umsetzungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der OpenAI-kompatible Claude-Code-Shim gibt echte `tool_calls` zurueck. Rowboat arbeitet damit auf der Subscription mit seinen Werkzeugen.

**Architecture:**
- Ein neues reines Modul `claude_code_openai_tools.py` (nur Standardbibliothek) uebernimmt den Werkzeug-Abschnitt im Prompt, das Parsen und Pruefen des `<tool_calls>`-Blocks, das Rendern des Verlaufs mit Werkzeugnachrichten und das Bauen der OpenAI-Antwort bzw. der SSE-Deltas.
- Der Shim nutzt es nur bei `SHIM_TOOL_CALLS=1`, mit hoechstens einer Korrekturrunde und abgeschalteten eingebauten CLI-Werkzeugen (`--tools ""`).
- Ohne Schalter bleibt alles byte-gleich.

**Tech Stack:** Python 3.11 (Standardbibliothek), pytest ueber `Captain_cook/.worktrees/runtime-composition/.venv`, Claude Code CLI `~\.local\bin\claude.exe`.

**Spec:** `vibemind-os/.worktrees/setup-agent/docs/superpowers/specs/2026-10-18-claude-shim-werkzeuge-design.md`. Bindend inklusive des Abschnitts „Entscheidungen des Controllers“ (User-OK 19.10.).

## Repo und Pfade

- **Code:** `C:\Users\User\Desktop\Captain_cook\.worktrees\runtime-composition` (`CC` im Plan), Branch `feat/runtime-entrypoint-composition`, Basis `2696937`.
- **Fremde Aenderung:** Im Worktree liegt eine fremde uncommittete Aenderung `scripts/run-live-demo.ps1`. Nie anfassen, nie stagen.
- **Tests:** Dateien liegen in `CC\tests\scripts\` und laden Skripte per `importlib.util.spec_from_file_location`.
- **Testaufruf:** `CC\.venv\Scripts\python.exe -m pytest tests/scripts/<datei> -q` aus `CC`.
- **Betrieb:**
  - Kopie `C:\Users\User\.local\bin\claude_code_openai_shim.py` (+ neues Modul daneben);
  - Starter `C:\Users\User\.local\bin\claude-code-shim-rowboat.cmd`;
  - Task `ClaudeCodeShimRowboat` (`:8121`).
  - `:8114` (Hermes/Captain) und `:8117` (Marketing) bleiben unberuehrt.

## Global Constraints

- **Schalter:** `SHIM_TOOL_CALLS=1` aktiviert alles Neue. Ohne Schalter ist die Ausgabe byte-gleich wie heute, auch bei Anfragen mit `tools` bzw. `tool`- und `assistant.tool_calls`-Nachrichten.
- **Werkzeug-Block im Antwortvertrag:** `<tool_calls>` … `</tool_calls>` mit einem JSON-Array `[{"name": "...", "arguments": {...}}]`.
- **OpenAI-Antwort bei Aufrufen:**
  - `message: {role: "assistant", content: null, tool_calls: [{id: "call_<hex>", type: "function", function: {name, arguments: "<JSON-String>"}}]}`, `finish_reason: "tool_calls"`.
  - Ob `content` `null` oder `""` sein muss, wird in Task 3 gegen Rowboats Client geprueft; Vorgabe `null`.
- **Verlauf:**
  - `Assistant (Werkzeugaufruf): <name>(<args-json>) [<id>]`;
  - `Werkzeugergebnis [<id>, <name>]: <inhalt>`.
- **Laengengrenze fuer Ergebnisse:** 20 000 Zeichen pro Ergebnis, Vermerk `(gekuerzt, N Zeichen)`.
- **Ungueltige Aufrufe:**
  - Hoechstens eine Korrekturrunde; ein ungueltiger Aufruf → keiner geht weiter.
  - Antwort ist dann der Text ausserhalb des Blocks, sonst exakt `Ich konnte das benoetigte Werkzeug nicht korrekt aufrufen.`
  - Der rohe Block erscheint nie im Inhalt.
- **Korrekturrunde und Zeit:** Die Korrekturrunde laeuft nur, wenn ≥ 50 % von `timeout_seconds` (Vorgabe 600 s) uebrig sind.
- **`tool_choice`:**
  - `none` → keine Werkzeuge im Prompt; ein trotzdem gelieferter Block wird entfernt.
  - `auto`/fehlend → frei.
  - `required` bzw. `{"type":"function","function":{"name":X}}` → Pflicht laut Prompt; Text auch nach Korrektur → Text zurueck, protokolliert.
- **Eingebaute CLI-Werkzeuge:** Mit Schalter zusaetzlich `--tools ""` (laut `claude --help`: `""` schaltet alle eingebauten Werkzeuge ab). MCP-Werkzeuge aus `SHIM_EXTRA_MCP_CONFIG`/Artefakten bleiben, wie sie sind.
- **Doppelklammern:** Neutralisiert wird weiterhin nur der System-Prompt, nur mit `SHIM_NEUTRALIZE_DOUBLE_BRACKETS=1`; Werkzeugergebnisse und Argumente bleiben unveraendert.
- **Code-Regeln:**
  - Nur Standardbibliothek.
  - Kein Token oder Schluessel im Log.
  - Conventional Commits mit `Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>`.
  - Nie `git stash`, nur eigene Dateien stagen, nicht pushen.

## Review Focus

1. **Groesse von Argumenten oder Ergebnissen:** Argumente mit Anfuehrungszeichen, Zeilenumbruechen, Umlauten und `[[Wikilinks]]` muessen exakt erhalten durch Parsen und Rendern kommen. Test in Task 1 (`test_arguments_roundtrip_exact`).
2. **Mehrere `<tool_calls>`-Bloecke oder einer im Code-Zaun:**
   - Mehr als ein Block ist ungueltig und geht in die Korrekturrunde.
   - Ein Block innerhalb von ```` ``` ```` zaehlt trotzdem, wenn er der einzige ist.
   - Test in Task 1 (`test_two_blocks_invalid`, `test_block_in_code_fence`).
3. **Unbekannte `tool_call_id` im Verlauf:** Ein `tool`-Ergebnis ohne passenden vorigen Aufruf wird mit Name `?` gerendert, nicht verworfen und ohne Absturz. Test in Task 1 (`test_tool_result_without_call`).
4. **Streaming mit Aufrufen und abbrechendem Client:** Bricht der Client die SSE-Verbindung ab, darf der Server-Thread keinen unbehandelten Fehler werfen. Test in Task 2 (`test_stream_client_disconnect_is_quiet`).
5. **Werkzeuge ohne `parameters` oder mit leerem Schema:** werden angeboten; Argumente `{}` sind dann gueltig. Test in Task 1 (`test_tool_without_parameters`).

---

### Task 1: Modul `claude_code_openai_tools.py` (rein, ohne HTTP/CLI)

**Files:**
- Create: `CC\scripts\claude_code_openai_tools.py`
- Create: `CC\tests\scripts\test_claude_code_openai_tools.py`

**Interfaces – Produces:**
- `TOOL_FAIL_TEXT: str = "Ich konnte das benoetigte Werkzeug nicht korrekt aufrufen."`
- `RESULT_LIMIT: int = 20000`
- `normalize_tools(tools: list | None) -> list[dict]`: liefert `[{name, description, parameters}]`; akzeptiert OpenAI-Form `{type:"function", function:{...}}` und flache Form.
- `tool_choice_mode(tool_choice) -> tuple[str, str | None]`: `("none"|"auto"|"required", forced_name | None)`.
- `tools_prompt_section(tools: list[dict], mode: str, forced: str | None) -> str`: Abschnitt fuer den System-Prompt (Werkzeugliste + Antwortvertrag); leer bei `mode == "none"` oder ohne Werkzeuge.
- `correction_prompt(errors: list[str]) -> str`: Nachsatz fuer die Korrekturrunde (nennt die Fehler, wiederholt den Vertrag).
- `render_messages_with_tools(messages: list[dict]) -> tuple[str, str]`: wie `render_messages` des Shims (gleiche Rollen-Praefixe `Assistant:`, `User:` …), zusaetzlich `assistant.tool_calls` und `role: "tool"` laut Global Constraints, mit Kuerzung auf `RESULT_LIMIT`.
- `@dataclass ParsedReply: calls: list[dict]` (je `{id, name, arguments: dict}`), `text: str` (Text ausserhalb des Blocks, getrimmt), `errors: list[str]`, `had_block: bool`.
- `parse_reply(text: str, tools: list[dict]) -> ParsedReply`: findet genau einen Block, prueft JSON, Werkzeugname, Pflichtfelder und Grundtypen (`string`, `number`, `integer`, `boolean`, `object`, `array`) der obersten Ebene. Fehler kommen nach `errors`; bei Fehlern ist `calls` leer.
- `completion_with_tool_calls(calls: list[dict], model: str, usage: dict) -> dict`: OpenAI-`chat.completion` laut Global Constraints; `usage` im Format des Shims (`prompt_tokens`, `completion_tokens`, `total_tokens`).
- `stream_chunks_for_tool_calls(completion: dict) -> list[dict]`:
  - erster Chunk `delta: {role: "assistant", tool_calls: [{index, id, type, function: {name, arguments}}…]}`;
  - letzter Chunk `delta: {}`, `finish_reason: "tool_calls"`;
  - `object: "chat.completion.chunk"`, gleiche `id`/`created`/`model`.

- [ ] **Step 1: Failing tests schreiben**, unter anderem:
  - `test_normalize_openai_and_flat_tools`
  - `test_tool_without_parameters` (Review Focus 5)
  - `test_tool_choice_modes` (`None`/`"auto"` → `auto`, `"none"`, `"required"`, benannte Funktion → `("required", "x")`)
  - `test_prompt_section_lists_tools_and_contract` (enthaelt jeden Namen, das Schema als JSON und die Tags `<tool_calls>`/`</tool_calls>`; leer bei `none`)
  - `test_parse_valid_single_and_multiple_calls` (IDs beginnen mit `call_`, eindeutig)
  - `test_parse_plain_text_no_block` (`had_block False`, `calls []`, `errors []`)
  - `test_parse_unknown_tool_is_error`
  - `test_parse_missing_required_and_wrong_type_are_errors`
  - `test_parse_invalid_json_is_error`
  - `test_two_blocks_invalid` (Review Focus 2)
  - `test_block_in_code_fence` (Review Focus 2)
  - `test_text_around_block_is_kept_separately` (`text` enthaelt den Begleittext, `calls` die Aufrufe)
  - `test_arguments_roundtrip_exact` (Review Focus 1): `{"path": "knowledge/Notes/Ä \"x\".md", "content": "Zeile1\n[[Wikilink]]"}` exakt erhalten
  - `test_render_tool_call_and_result` (exakte Zeilen laut Global Constraints)
  - `test_render_result_truncated` (20001 Zeichen → 20000 + ` (gekuerzt, 20001 Zeichen)`)
  - `test_tool_result_without_call` (Review Focus 3)
  - `test_render_without_tool_messages_equals_shim_render_messages` (gleicher Output wie `claude_code_openai_shim.render_messages` fuer eine Liste nur mit system/user/assistant-Text)
  - `test_completion_with_tool_calls_shape` (`content is None`, `finish_reason == "tool_calls"`, `arguments` ist ein JSON-String)
  - `test_stream_chunks_shape`
- [ ] **Step 2:** `.venv\Scripts\python.exe -m pytest tests/scripts/test_claude_code_openai_tools.py -q` → FAIL (Modul fehlt).
- [ ] **Step 3:** Modul implementieren.
  - Beim Parsen nach dem Muster `<tool_calls>(.*?)</tool_calls>` (DOTALL) suchen; umgebende Code-Zaeune entfernen; ein vorhandener Treffer muss ein JSON-Array von Objekten mit `name` (str) und `arguments` (dict) sein.
- [ ] **Step 4:** Tests gruen; zusaetzlich `-m pytest tests/scripts -q -k claude_code` gruen.
- [ ] **Step 5:** Commit `feat(shim): Modul fuer Werkzeugaufrufe (Prompt, Parser, Verlauf, OpenAI-Form)`.

### Task 2: Shim-Integration hinter `SHIM_TOOL_CALLS`

**Files:**
- Modify: `CC\scripts\claude_code_openai_shim.py` (`run_claude`, `do_POST`, `_send_stream`, Import des Moduls aus dem eigenen Verzeichnis)
- Create: `CC\tests\scripts\test_claude_code_openai_shim_tools.py`
- Create: `CC\tests\scripts\fake_claude_cli.py`: gefaelschte CLI. Sie liest eine Antwortliste aus der Datei in `FAKE_CLAUDE_REPLIES` (JSON-Array von `result`-Texten), gibt je Aufruf die naechste als CLI-JSON aus (`{"type":"result","result":..., "usage":{...}, "duration_api_ms":..., "is_error":false}`) und protokolliert argv + stdin in `FAKE_CLAUDE_LOG`.

**Interfaces:**
- **Consumes:** alles aus Task 1.
- **Produces (fuer Task 3):**
  - `run_claude(..., disable_builtin_tools: bool = False)` haengt bei `True` `--tools ""` an. Ohne Flag bleibt die Kommandozeile unveraendert.
  - Eine Log-Zeile pro CLI-Runde: `round: n=<1|2> wall_ms=<..> api_ms=<duration_api_ms> cache_read=<cache_read_input_tokens> outcome=<text|tool_calls|retry|fail>`.

- [ ] **Step 1: Failing tests** (Server im Thread auf freiem Port, `CLAUDE_CODE_CLI` = `python fake_claude_cli.py` ueber eine `.cmd`-Huelle bzw. das Skript direkt, wie der Shim die CLI aufrufen kann; `timeout_seconds` klein waehlen):
  - `test_switch_off_byte_identical` (Global Constraint): gleiche Anfrage mit `tools` und Werkzeugnachrichten, Schalter aus → Antwort identisch zur Antwort ohne `tools` bis auf `id`/`created`; die argv der CLI enthaelt kein `--tools`.
  - `test_tool_call_nonstream`: Fake liefert einen gueltigen Block → `finish_reason == "tool_calls"`; die argv enthaelt `--tools` gefolgt von `""`; der System-Prompt (Datei aus `--system-prompt-file` bzw. stdin laut Shim) enthaelt den Werkzeug-Abschnitt.
  - `test_tool_call_stream`: SSE mit Werkzeug-Delta, dann `finish_reason: "tool_calls"`, dann `data: [DONE]`.
  - `test_tool_result_round`: Anfrage mit `assistant.tool_calls` + `tool`-Ergebnis → der Transcript an die CLI enthaelt die gerenderten Zeilen; Fake antwortet mit Text → normale Antwort.
  - `test_invalid_then_corrected`: Fake: 1. ungueltiger Block, 2. gueltiger → Aufrufe; die CLI lief genau zweimal; im zweiten Prompt steht der Korrektursatz.
  - `test_invalid_twice_falls_back`: zweimal ungueltig → Inhalt = Text ausserhalb des Blocks bzw. exakt `TOOL_FAIL_TEXT`; der Inhalt enthaelt kein `<tool_calls>`.
  - `test_no_retry_when_time_short`: `timeout_seconds` so setzen, dass nach Runde 1 weniger als 50 % uebrig sind (Fake mit kuenstlicher Verzoegerung) → keine zweite Runde.
  - `test_tool_choice_none_strips_block`.
  - `test_required_but_text_returns_text`.
  - `test_stream_client_disconnect_is_quiet` (Review Focus 4): Client schliesst nach dem ersten Chunk; der Server-Thread loggt hoechstens eine Zeile, keine Exception im Log.
  - `test_round_log_line`: stderr enthaelt `round: n=1` mit `api_ms=` und `cache_read=`.
- [ ] **Step 2:** Tests → FAIL.
- [ ] **Step 3:** Integration implementieren. Ablauf in `do_POST` bei Schalter an und Werkzeugen ungleich `none`:
  1. `render_messages_with_tools`, dann `tools_prompt_section` an den System-Prompt haengen.
  2. Runde 1; `parse_reply`.
  3. Bei Fehlern und genug Zeit: Runde 2 mit `correction_prompt` am Ende des Transcripts, erneut parsen.
  4. Danach Aufrufe → `completion_with_tool_calls`, sonst Text-Antwort. Inhalt ist `ParsedReply.text`, bei leerem Text nach Fehlern `TOOL_FAIL_TEXT`, sonst der Claude-Text ohne Block.
  - Bei `stream`: `stream_chunks_for_tool_calls`, sonst der bestehende `_send_stream`.
  - Abbruch des Clients (`BrokenPipeError`/`ConnectionResetError`) beim Schreiben ruhig behandeln.
- [ ] **Step 4:**
  - Neue Tests gruen.
  - Task-1-Tests gruen.
  - Vor dem Commit einmal den bestehenden Shim mit `--help` starten (Syntax/Import ok).
  - Rowboats Zeitlimit fuer Modellaufrufe einmal im Fork nachlesen (`C:\Users\User\ClaudeWork\wt-rowboat-embed\apps\x\packages\core\src\models\`), Ergebnis in den Report.
- [ ] **Step 5:** Commit `feat(shim): echte tool_calls hinter SHIM_TOOL_CALLS (Korrekturrunde, --tools "", Rundenprotokoll)`.

### Task 3: Ausrollen auf `:8121` und Live-Abnahme mit Rowboat (mit dem User)

**Files:**
- Modify: `C:\Users\User\.local\bin\claude-code-shim-rowboat.cmd` (`set "SHIM_TOOL_CALLS=1"` vor dem Python-Aufruf)
- Copy: `CC\scripts\claude_code_openai_shim.py` und `CC\scripts\claude_code_openai_tools.py` → `C:\Users\User\.local\bin\`

- [ ] **Step 1:** Vor dem Kopieren beide Zieldateien sichern (`*.bak-<datum>`). Kopieren. Danach `diff` Quelle ↔ Kopie leer.
- [ ] **Step 2:** Nur den Task `ClaudeCodeShimRowboat` neu starten (Stop/Start). `GET http://127.0.0.1:8121/v1/models` → 200. Prozess `--port 8114` unveraendert (PID vorher/nachher gleich).
- [ ] **Step 3: Live-Abnahme ueber MCP `rowboat_chat_send`** (Rowboat laeuft per Task `VibeMind Rowboat (Einbettung)`):
  - a) „Welche Notizen liegen unter knowledge/People? Nenne drei Namen.“ → Antwort nennt echte Namen. Im Shim-Log steht `outcome=tool_calls`, dann `outcome=text`.
  - b) „Lege die Notiz knowledge/Notes/Shim-Test.md mit dem Inhalt ‚Test [[Welcome]]‘ an.“ → Die Datei existiert (`rowboat_note_read`), der Inhalt enthaelt `[[Welcome]]` unveraendert, kein „out of extra usage“.
  - c) Die Rundenzeiten (`round:`-Zeilen) fuer a) und b) in den Report.
  - d) Ob `content: null` sauber verarbeitet wurde: keine Fehlermeldung in Rowboat. Falls doch, auf `""` umstellen (Global Constraint) und Task 2 mit Test nachziehen.
- [ ] **Step 4:** Die Testnotiz nach Abnahme durch den User loeschen lassen bzw. mit seinem OK entfernen.
- [ ] **Step 5:**
  - Memory `project_shim_tool_bridge_and_bracket_bug.md` aktualisieren: Werkzeuge jetzt per `SHIM_TOOL_CALLS`, nur `:8121`.
  - `project_claude_code_only_no_api_budget.md` um „Rowboat auf :8121“ ergaenzen.
  - Kein Push von Captain_cook ohne User-OK.
