---
name: word-find-replace
description: Suchen-und-Ersetzen in Word (Strg+H), ersetzt alle Vorkommen eines Strings durch einen anderen.
app: word
agents: ["*"]
trigger: "word ersetzen|find replace|text ersetzen"
inputs:
  - {name: find_text, type: string, description: "Zu findender Text"}
  - {name: replace_text, type: string, description: "Ersatztext"}
expected_state:
  description: "Word zeigt eine Bestaetigungs-Snackbar oder Statusmeldung 'Alle <n> Vorkommen ersetzt' und der Ersetzen-Dialog ist geschlossen."
  verification_tool: vision_analyze
secrets: []
confidence: 0.0
attempts: 0
successes: 0
last_adjusted: null
---

# Steps

1. **Fokus pruefen** — Word muss aktiv sein (`handoff_get_focus`).
2. **Ersetzen-Dialog oeffnen** — `handoff_action(action_type="hotkey", keys="ctrl+h")`.
3. **Dialog warten** — sleep 1s.
4. **Such-Feld** — der Cursor ist im Suchen-Feld nach Ctrl+H. `handoff_action(type="type", text="{find_text}")`.
5. **In Ersetzen-Feld wechseln** — `handoff_action(press, key="tab")`. Tab springt vom Such- zum Ersetzen-Feld.
6. **Ersatztext** — `handoff_action(type="type", text="{replace_text}")`.
7. **Alle ersetzen** — `handoff_action(action_type="hotkey", keys="alt+a")` (Alt+A = "Alle ersetzen" in DE-Word; in EN: alt+a fuer 'Replace All').
8. **Info-Dialog schliessen** — Word zeigt "X Ersetzungen vorgenommen". `handoff_action(press, key="enter")`.
9. **Dialog schliessen** — `handoff_action(press, key="escape")`.
10. **Validieren** — `vision_analyze(mode="state_analysis", prompt="Ist der Suchen-und-Ersetzen-Dialog geschlossen? Wurde eine Snackbar/Meldung angezeigt? JSON {dialog_closed: bool, replacement_count: int}")`. Erfolg wenn dialog_closed=true und replacement_count > 0.

# Adjustments
- Englische Word-Version: alt+a triggert evtl. "Replace All" nicht direkt — Alternative `handoff_action(hotkey, keys="alt+l")` oder Klick auf Button via vision_analyze element_detection.
- Wenn `find_text` nicht vorkommt, zeigt Word "0 Ersetzungen" — wir betrachten das als Failure (replacement_count=0).
