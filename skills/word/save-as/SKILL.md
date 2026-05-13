---
name: word-save-as
description: Speichere das aktive Word-Dokument unter einem neuen Pfad (F12).
app: word
agents: ["*"]
trigger: "word speichern unter|word save as"
inputs:
  - {name: path, type: string, description: "Vollstaendiger Pfad inkl. .docx-Endung"}
expected_state:
  description: "Der Word-Window-Title zeigt den neuen Dateinamen (Basename von {path}) und kein Save-As-Dialog ist mehr offen."
  verification_tool: vision_analyze
secrets: []
confidence: 0.0
attempts: 0
successes: 0
last_adjusted: null
---

# Steps

1. **Fokus pruefen** — `handoff_get_focus`, Window-Title muss „Word" enthalten.
2. **Save-As oeffnen** — `handoff_action(action_type="hotkey", keys="f12")`. F12 = Speichern unter in Office.
3. **Dialog warten** — `handoff_action(action_type="sleep", seconds=2)` (Datei-Dialog kann 1-2s brauchen).
4. **Pfad eingeben** — `handoff_action(action_type="type", text="{path}")`. Modernes Office akzeptiert vollstaendigen Pfad mit Endung im Dateiname-Eingabefeld.
5. **Bestaetigen** — `handoff_action(action_type="press", key="enter")`.
6. **Falls Ueberschreiben-Dialog** — `vision_analyze(mode="state_analysis", prompt="Erscheint ein Bestaetigungsdialog 'Datei existiert bereits ueberschreiben?'? JSON {overwrite_dialog: bool}")`. Wenn ja: `handoff_action(press, key="left")` + Enter (Ja waehlen).
7. **Validieren** — `handoff_get_focus`. Erfolg wenn Window-Title den Basename von {path} (ohne Pfad) enthaelt.

# Adjustments
- Bei Office 365 unter OneDrive-Mode kann F12 zur Cloud-Speichern-Liste fuehren — Workaround: alt+f, s, s (Datei → Speichern unter → Speichern unter).
- Manche Word-Versionen erzwingen erst Tab-Druck um in das Dateinamen-Feld zu kommen.
