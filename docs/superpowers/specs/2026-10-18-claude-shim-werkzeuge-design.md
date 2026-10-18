# Claude-Code-Shim mit Werkzeugaufrufen (Design)

Stand: 2026-10-18. Folgt auf Teil 2/3 der Rowboat-Einbettung. Rowboat laeuft seit heute auf der Claude-Code-Subscription ueber eine eigene Shim-Instanz (`:8121`, Task `ClaudeCodeShimRowboat`), aber nur mit Text.

## Absicht

Der User hat nur die Claude-Code-Subscription, kein API-Budget (Memory „Nur Claude Code, kein API-Budget“).

Der Shim (`claude_code_openai_shim.py`) macht aus der Claude-Code-CLI einen OpenAI-kompatiblen Endpunkt. Er gibt aber nie `tool_calls` zurueck. Agenten, die ueber ihn laufen, bleiben deshalb ohne Werkzeuge:
- Rowboats Copilot kann Notizen weder suchen noch schreiben und keine Mails, Agenten usw. anstossen.
- openclaw und andere OpenAI-Clients haben dieselbe Luecke.

Ziel: Der Shim bietet echte Werkzeugaufrufe im OpenAI-Format an. Der Aufrufer (Rowboat) fuehrt sie selbst aus, mit seinen eigenen Pruefungen, und reicht die Ergebnisse zurueck, bis Claude eine Endantwort gibt. Damit arbeitet Rowboat voll auf der Subscription.

## Ziele

1. **Rueckgabe von `tool_calls`:** Bietet der Aufrufer `tools` an, darf Claude statt einer Antwort Werkzeugaufrufe zurueckgeben, eines oder mehrere pro Antwort. Der Shim liefert sie als OpenAI-`tool_calls`, nicht-streamend wie streamend (SSE-Deltas), mit `finish_reason: "tool_calls"`.
2. **Vollstaendiger Verlauf:** Werkzeugergebnisse (`role: "tool"`) und fruehere Werkzeugaufrufe (`assistant.tool_calls`) erscheinen im Verlauf, den Claude sieht, mit Name, Argumenten und Ergebnis.
3. **Pruefung jedes Aufrufs:** Ein Aufruf eines nicht angebotenen Werkzeugs oder mit Argumenten, die nicht zum JSON-Schema passen (Pflichtfelder, Grundtypen), wird nicht weitergegeben. Stattdessen fragt der Shim Claude genau einmal mit einer Korrektur nach. Scheitert auch das, kommt der Text als normale Antwort zurueck.
4. **`tool_choice`:** `none` bedeutet keine Werkzeuge; bei `auto` (Standard) entscheidet Claude; `required` bzw. ein benanntes Werkzeug muss aufgerufen werden.
5. **Gleiches Verhalten fuer alte Aufrufer:** Ohne `tools` oder mit ausgeschaltetem Schalter verhaelt sich der Shim byte-gleich wie heute.
6. **Rowboat-Abnahme:**
   - Im echten Rowboat beantwortet der Copilot eine Frage mit Werkzeug korrekt, z. B. „Welche Notizen liegen unter knowledge/People?“ → `file-list`.
   - Er legt auf Bitte eine Notiz an.

## Nicht-Ziele

- Keine Werkzeuge in der CLI-Schleife: Werkzeuge laufen beim Aufrufer, nicht in der CLI. Das bestehende `SHIM_EXTRA_MCP_CONFIG` bleibt unberuehrt und davon getrennt.
- Kein Umbau von Hermes, Captain oder Marketing: Die Instanzen `:8114` und `:8117` bleiben unveraendert, bis jemand sie bewusst umstellt.
- Kein Anthropic-API-Zugang, keine bezahlten Schluessel.
- Keine eigene Werkzeugausfuehrung im Shim.

## Anti-Ziele (das waere ein Fehlschlag, auch wenn es technisch laeuft)

- **Erfundene Ergebnisse:** Claude erfindet Werkzeugergebnisse, statt einen Aufruf zurueckzugeben, und der Shim merkt es nicht.
- **Zerstoerte Aufrufe:** Ein fehlerhaft geparster Aufruf fuehrt bei Rowboat zu einer falschen Aktion, etwa dem Schreiben an den falschen Pfad. Lieber Text zurueckgeben als einen zweifelhaften Aufruf.
- **Stiller Umbau:** Hermes oder Captain bekommen ungefragt ein anderes Verhalten.
- **Laufzeit:** Ein Werkzeug-Zug, der nicht spuerbar besser ist als „nur Text“, weil jede Runde mehrere Minuten dauert. Die Laufzeit wird gemessen und berichtet (siehe Leistung).

## Festlegungen

### Verdrahtung und Format

- **Schalter:**
  - `SHIM_TOOL_CALLS=1` aktiviert die Werkzeugunterstuetzung pro Instanz.
  - Gesetzt wird er nur in `claude-code-shim-rowboat.cmd` (`:8121`).
  - Ohne Schalter werden `tools` wie heute nur protokolliert.
- **Protokoll zwischen Shim und Claude** (Textvertrag, weil die CLI keinen Werkzeugmodus fuer fremde Werkzeuge hat):
  - Der Shim haengt an den System-Prompt einen Abschnitt „Verfuegbare Werkzeuge“ an: Name, Beschreibung und das JSON-Schema der Parameter je Werkzeug.
  - Dazu kommt ein fester Antwortvertrag. Entweder kommt eine normale Antwort, oder ausschliesslich ein Block

    ```
    <tool_calls>
    [{"name": "<werkzeug>", "arguments": { ... }}]
    </tool_calls>
    ```

    ohne Text davor oder danach.
  - Ein klar abgegrenzter Tag ist gewaehlt, weil ein JSON-Block im Fliesstext mehrdeutig waere.
- **Erkennen und Umwandeln:**
  - Der Shim erkennt den Block in `result`, parst ihn und prueft jeden Aufruf (Ziel 3).
  - Jeder gueltige Aufruf bekommt eine ID `call_<hex>`.
  - Antwort-Format: `message: {role: "assistant", content: null, tool_calls: [{id, type: "function", function: {name, arguments: "<JSON-String>"}}]}`.
  - Beim Streaming geht das als ein oder mehrere Deltas mit `index`, dann `finish_reason: "tool_calls"` und `[DONE]`.
- **Verlauf rendern:**
  - `assistant` mit `tool_calls` → `Assistant (Werkzeugaufruf): <name>(<args-json>) [id]`.
  - `tool` → `Werkzeugergebnis [<id>, <name>]: <inhalt>`. Ein einzelnes Ergebnis wird auf 20 000 Zeichen gekuerzt, mit dem Vermerk „(gekuerzt, N Zeichen)“; eine Obergrenze ist noetig, weil Notizen gross sein koennen.
  - Der Name wird ueber die `tool_call_id` aus dem vorigen Assistant-Aufruf zugeordnet.

### Ort, Tests, Betrieb

- **Ort des Codes:**
  - Kanonische Quelle bleibt `Captain_cook/.worktrees/runtime-composition/scripts/claude_code_openai_shim.py`.
  - Danach wird nach `~\.local\bin\claude_code_openai_shim.py` kopiert, wie bisher (Memory: Drift-Hinweis).
  - Die Werkzeug-Logik kommt in eine eigene Moduldatei neben dem Shim (`claude_code_openai_tools.py`, nur Standardbibliothek). Der Shim bleibt damit lesbar, und die Logik ist ohne HTTP und CLI testbar.
- **Tests:**
  - `unittest`/pytest, nur Standardbibliothek: Parser, Validierung, Verlauf, OpenAI-Antwort und SSE.
  - Eine Ende-zu-Ende-Probe gegen den HTTP-Server mit einer gefaelschten CLI (`CLAUDE_CODE_CLI` zeigt auf ein Skript, das vorgegebene JSON-Ausgaben liefert).
  - Ein Test belegt, dass ohne Schalter die Ausgabe exakt der heutigen entspricht.
- **Betrieb:** Nach dem Kopieren wird nur `ClaudeCodeShimRowboat` neu gestartet. Andere Instanzen laufen weiter mit der alten Kopie, bis sie selbst neu starten. Das ist unkritisch, weil der Schalter fehlt.

### Leistung

Jede Runde startet die CLI neu mit Rowboats rund 39 000 Zeichen System-Prompt; die erste Antwort heute brauchte gut 2 Minuten.
- Teil des Auftrags ist, je Runde zu messen und zu berichten: Startzeit, `duration_api_ms` aus der CLI-Ausgabe, Cache-Treffer (`cache_read_input_tokens`).
- Massnahmen nur, wenn die Messung sie rechtfertigt, und nur ohne Aenderung am Vertrag.
- Kandidaten: Die CLI-Sitzung zwischen den Runden eines Zugs per `--resume` weiterfuehren, oder fuer Zwischenschritte ein schnelleres Modell.

### Entscheidungen des Controllers (vom User zu pruefen)

- **Fehlerfaelle beim Parsen.**
  - Ist nach der einen Korrektur noch ein Aufruf ungueltig, gibt der Shim keinen einzigen Aufruf weiter.
  - Antwort ist dann der Text ausserhalb des Blocks. Ist der leer: „Ich konnte das benoetigte Werkzeug nicht korrekt aufrufen.“
  - Der rohe `<tool_calls>`-Block erscheint nie im Inhalt.
  - Steht trotz Vertrag Text um einen gueltigen Block, zaehlen die Aufrufe; der Begleittext wird verworfen und protokolliert.
  - Ein unvollstaendiger Satz an Aufrufen wird nie weitergegeben, denn ein teilweise ausgefuehrter Plan ist gefaehrlicher als keiner.
- **`tool_choice`-Konflikte.**
  - Bei `required` bzw. einem benannten Werkzeug und Text auch nach der Korrektur kommt der Text zurueck (kein HTTP-Fehler) und der Fall wird protokolliert. Rowboat zeigt dann wenigstens die Antwort.
  - Bei `none` mit einem Block wird der Block entfernt; der Resttext oder die feste Meldung oben kommt zurueck.
- **Zeit.**
  - Die Korrekturrunde laeuft nur, wenn mindestens die Haelfte des Zeitlimits pro Anfrage (`timeout_seconds`, 600 s) noch uebrig ist; sonst gilt der Fehlerfall oben.
  - Rowboats eigenes Zeitlimit prueft der Builder einmal im Code (`packages/core/src/models`) und nennt es im Bericht.
- **Doppelklammern `[[ ]]`.**
  - Neutralisiert wird wie heute nur der System-Prompt, und nur mit `SHIM_NEUTRALIZE_DOUBLE_BRACKETS=1` (fuer `:8121` nicht gesetzt).
  - Werkzeugergebnisse und Argumente bleiben unveraendert, sodass Wikilinks und Pfade heil bleiben.
  - Die Live-Abnahme enthaelt bewusst eine Notiz mit `[[Wikilink]]`. Tritt der falsche „out of extra usage“-Fehler auf, wird nachgebessert (Neutralisieren und Zurueckwandeln nur im Verlauf).
- **Eingebaute CLI-Werkzeuge.** Mit eingeschaltetem Schalter schaltet der Shim die eingebauten Werkzeuge der CLI ab (Bash, Read, Write, …), etwa ueber `--tools ""` oder `--disallowedTools`; welche Option die installierte CLI kann, prueft der Builder mit `claude --help`. Claude kann dann nichts selbst ausfuehren und Ergebnisse nicht vortaeuschen (Anti-Ziel). Ohne Schalter bleibt die Kommandozeile wie heute.
- **Byte-Gleichheit.** Ohne Schalter bleibt alles wie heute, auch bei Anfragen mit `tools` und mit `tool`- oder `assistant.tool_calls`-Nachrichten im Verlauf. Das neue Rendern des Verlaufs haengt ebenfalls am Schalter.

## Dem Builder ueberlassen

- Die genaue Formulierung des Werkzeug-Abschnitts im Prompt, solange der Vertrag eingehalten ist.
- Umfang der Schema-Pruefung ueber Pflichtfelder und Grundtypen hinaus.
- Ob `content` beim Werkzeugaufruf `null` oder `""` ist; massgeblich ist, was Rowboats Client (ai-sdk `openai-compatible`) sauber verarbeitet. Das wird gegen den echten Rowboat geprueft.
- Struktur der Tests.

## Offene Fragen an den User (vor dem Bau)

- **Uncommitted Aenderungen:** In der kanonischen Datei liegen 46 nicht committete Zeilen (`SHIM_EXTRA_MCP_CONFIG`, Klammer-Fix u. a.), identisch mit der laufenden Kopie. Sollen sie zuerst als eigener Commit gesichert werden (Herkunft: fruehere Sessions), oder bleibt der Shim-Umbau ausserhalb von Captain_cook?
