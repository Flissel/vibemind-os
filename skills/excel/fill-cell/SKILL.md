---
name: excel-fill-cell
description: Schreibe einen Wert in eine bestimmte Excel-Zelle.
app: excel
agents: ["*"]
trigger: "fülle zelle|setze zelle|excel write cell|excel fill"
inputs:
  - {name: cell, type: string, description: "Zielzelle in A1-Notation, z.B. 'A1'"}
  - {name: value, type: string, description: "Wert oder Formel (mit '=' am Anfang für Formel)"}
expected_state:
  description: "In Zelle {cell} steht der Wert {value} und der Zell-Cursor ist eine Zeile unter {cell} (Excel rückt nach Enter eine Zeile vor)."
  verification_tool: vision_analyze
secrets: []
confidence: 0.0
attempts: 0
successes: 0
last_adjusted: null
---

# Steps

1. **Vorbedingung** — Skill `excel-goto-cell` mit `cell={cell}` aufrufen. Wenn das fehlschlägt, abbrechen.
2. **Wert eingeben** — `handoff_action(action_type="type", text="{value}")`. Das überschreibt den vorhandenen Inhalt der Zelle.
3. **Bestätigen** — `handoff_action(action_type="press", key="enter")`.
4. **Zurück zur Zelle** — `handoff_action(action_type="hotkey", keys="ctrl+g")` + Zelle erneut eingeben + Enter, damit der Validator den Endzustand vergleichen kann (Cursor ist sonst eine Zeile darunter).
5. **Validieren** — `vision_analyze(mode="state_analysis", prompt="Welcher Wert steht in Zelle {cell}? Antwort als JSON {cell: <string>, value: <string>}.")`. Erfolg wenn `value == "{value}"`.

# Adjustments (vom Coordinator gepflegt)

- Wenn Excel einen anderen Editor-Modus hat (z.B. eine Formel hinterlässt einen `=`-prefix nach `Esc`), nicht mit Enter sondern Tab bestätigen.
- Bei Sprachversionen mit Komma als Dezimaltrennzeichen (DE-DE) muss `1,5` statt `1.5` getippt werden — sonst interpretiert Excel den Wert als Datum.
