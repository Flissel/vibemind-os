---
name: excel-goto-cell
description: Springe in Microsoft Excel zu einer bestimmten Zelle (z.B. A1, B5, AB42).
app: excel
agents: ["*"]
trigger: "gehe zu zelle|navigiere zelle|excel goto|jump to cell"
inputs:
  - {name: cell, type: string, description: "A1-Notation der Zielzelle, z.B. 'B5'"}
expected_state:
  description: "Die Statusleiste links unten (Name Box) zeigt {cell} und der Zellfokus liegt auf {cell}."
  verification_tool: vision_analyze
secrets: []
confidence: 0.0
attempts: 0
successes: 0
last_adjusted: null
---

# Steps

1. **Fokus prüfen** — `handoff_get_focus`. Wenn der Window-Title nicht „Excel" enthält, zurückgeben mit `{success: false, reason: "Excel ist nicht im Vordergrund"}`.
2. **Goto-Dialog öffnen** — `handoff_action(action_type="hotkey", keys="ctrl+g")`. Excel öffnet das Dialog-Fenster „Gehe zu" / „Go To".
3. **Zelle eingeben** — `handoff_action(action_type="type", text="{cell}")`.
4. **Bestätigen** — `handoff_action(action_type="press", key="enter")`.
5. **Validieren** — `vision_analyze(mode="state_analysis", prompt="Ist Zelle {cell} aktuell selektiert? Antwort als JSON {selected_cell: <string>}.")`. Erfolg wenn das LLM `selected_cell == "{cell}"` reportet.
