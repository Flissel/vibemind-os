---
name: word-new-document
description: Erstelle ein leeres neues Word-Dokument (Strg+N) und optional tippe einen Anfangstext.
app: word
agents: ["*"]
trigger: "neues word dokument|word new document|leeres dokument"
inputs:
  - {name: initial_text, type: string, description: "Optionaler Text der nach dem Anlegen direkt eingetippt wird (leer = kein Text)"}
expected_state:
  description: "Word zeigt ein leeres oder mit {initial_text} gefuelltes Dokument im Vordergrund. Der Window-Title enthaelt 'Dokument' oder 'Document' und keinen vorherigen Dateinamen."
  verification_tool: vision_analyze
secrets: []
confidence: 0.0
attempts: 0
successes: 0
last_adjusted: null
---

# Steps

1. **Fokus pruefen** — `handoff_get_focus`. Wenn Window-Title nicht „Word" enthaelt, abbrechen mit `{success: false, reason: "Word ist nicht aktiv"}`.
2. **Neues Dokument** — `handoff_action(action_type="hotkey", keys="ctrl+n")`. Word oeffnet ein leeres Dokument. Bei manchen Word-Versionen ploppt vorher die "Neues Dokument"-Auswahl auf — in dem Fall einen kurzen Sleep einbauen und Enter druecken (default: Leeres Dokument).
3. **Auf Dialog warten** — `handoff_action(action_type="sleep", seconds=1)`.
4. **Falls Auswahl-Screen** — `vision_analyze(mode="state_analysis", prompt="Sehe ich einen 'Neues Dokument'-Auswahlscreen mit Vorlagen? JSON {template_picker_open: bool}")`. Wenn ja: `handoff_action(press, key="enter")` (default-Auswahl ist 'Leeres Dokument') + 1s sleep.
5. **Initial-Text** — wenn `{initial_text}` nicht leer ist: `handoff_action(action_type="type", text="{initial_text}")`.
6. **Validieren** — `vision_analyze(mode="state_analysis", prompt="Welcher Text steht im aktuellen Word-Dokument? Ist es ein leeres Dokument? JSON {is_empty: bool, first_line: <string>}")`. Erfolg wenn (a) initial_text leer und is_empty=true, oder (b) first_line beginnt mit ersten Zeichen von initial_text.
