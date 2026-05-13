---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Macht die selektierte Zelle fett (Strg+B).
expected_state:
  description: Der Text in Zelle A1 ist fett formatiert.
  verification_tool: vision_analyze
inputs: Keine spezifischen Eingaben erforderlich, da der Cursor bereits in der Zelle
  A1 ist.
name: excel-make-bold
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T10:16:32.172663+00:00'
---

## Schritte

1. **Approval einholen**: `handoff_approval_request(action='Lerne excel-make-bold: Macht die selektierte Zelle fett (Strg+B).', timeout_seconds=60, default_on_timeout='approved')`.
2. **App starten oder fokussieren**: `app_launch_or_focus(app='excel', title_hint='Excel')`. Sleep 5s.
3. **Cursor in sauberen Zustand bringen**: `handoff_action(hotkey, keys='ctrl+home')`, Sleep 0.3s.
4. **Setup**: A1 = 'FETT' via `excel_paste_table`. Cursor in A1.
5. **Test**: `handoff_action(hotkey, keys='ctrl+b')`, Sleep 0.3s.
6. **Validierung**: `vision_analyze(prompt='Ist der Text in Zelle A1 fett (bold) formatiert? JSON {a1_bold:bool}')`.

## Ergebnis
Der Text in Zelle A1 ist fett formatiert.
