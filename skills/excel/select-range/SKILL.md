---
name: excel-select-range
description: Markiere einen Zellbereich in Excel (z.B. A1:C5).
app: excel
agents: ["*"]
trigger: "markiere bereich|select range|excel range"
inputs:
  - {name: range, type: string, description: "Range in A1:Z9-Notation, z.B. 'A1:C5'"}
expected_state:
  description: "Die Name Box (links unten) zeigt {range} oder die Anzahl der markierten Zellen passt zur Range."
  verification_tool: vision_analyze
secrets: []
confidence: 0.0
attempts: 0
successes: 0
last_adjusted: null
---

# Steps

1. **Fokus prüfen** — wie `excel-goto-cell`.
2. **Name Box ansprechen** — `handoff_action(action_type="hotkey", keys="alt+f3")` (Alt+F3 fokussiert die Name Box; alternativ Ctrl+G und in der Range-Form schreiben).
   - Falls Alt+F3 in der lokalen Excel-Version nicht greift: Fallback auf `ctrl+g` und in den Reference-Input des Goto-Dialogs den Range eingeben.
3. **Range eingeben** — `handoff_action(action_type="type", text="{range}")`.
4. **Bestätigen** — `handoff_action(action_type="press", key="enter")`.
5. **Validieren** — `vision_analyze(mode="state_analysis", prompt="Welcher Range ist aktuell selektiert? Antwort als JSON {range: <string>}.")`. Erfolg wenn `range == "{range}"`.
