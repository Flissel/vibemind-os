---
agents:
- '*'
app: excel
attempts: 1
confidence: 0.0
description: Schreibt =MITTELWERT(A1:A3).
expected_state:
  description: Der Wert in B1 zeigt den Mittelwert der Zellen A1 bis A3 an, was 20,00
    € entsprechen sollte.
  verification_tool: vision_analyze
inputs:
- description: A1:A3
  name: range
  type: string
- description: B1
  name: target
  type: string
last_adjusted: '2026-05-04T12:22:00'
name: excel-formula-average
requires_approval: true
successes: 0
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Anfrage zur Genehmigung des Lernlaufs gestellt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Home wurde ausgefuehrt.
4. **Daten einfügen**: Werte 10, 20, 30 in Zellen A1, A2, A3 eingefügt.
5. **Formel eingeben**: =MITTELWERT(A1:A3) in Zelle B1 eingegeben.
6. **Ergebnis validieren**: Der Wert in B1 konnte nicht erfasst werden.

# FAILURES
- Der Wert in B1 wurde nicht korrekt angezeigt oder erfasst. Weitere Anpassungen sind notwendig.
