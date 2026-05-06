---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Schreibt =SUMME(A1:A3) und validiert das Ergebnis.
expected_state:
  description: Der Wert in A4 zeigt die Summe der Zellen A1 bis A3 an, was 60,00 €
    entspricht.
  verification_tool: vision_analyze
inputs:
- description: A1:A3
  name: range
  type: string
- description: A4
  name: target
  type: string
last_adjusted: '2026-05-04T12:20:00'
name: excel-formula-sum
requires_approval: true
successes: 1
---

## Schritte zur Ausfuehrung des Skills

1. **Approval einholen**: Anfrage zur Genehmigung des Lernlaufs gestellt.
2. **Excel starten oder fokussieren**: Excel wurde erfolgreich fokussiert.
3. **Cursor in sauberen Zustand bringen**: Strg+Home wurde ausgefuehrt.
4. **Daten einfügen**: Werte 10, 20, 30 in Zellen A1, A2, A3 eingefügt.
5. **Formel eingeben**: =SUMME(A1:A3) in Zelle A4 eingegeben.
6. **Ergebnis validieren**: Der Wert in A4 wurde als 60,00 € angezeigt.
