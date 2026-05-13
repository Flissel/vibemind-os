---
name: word-write-text
description: Schreibe einen Text in ein Word-Dokument.
app: word
requires_approval: true
agents: ["*"]
inputs:
  - name: text
    type: string
expected_state:
  description: Der Text 'Hallo Word vom Coordinator' ist im Word-Editor sichtbar.
  verification_tool: vision_analyze
confidence: 1.0
attempts: 1
successes: 1
last_adjusted: 2026-05-04T11:12:00+02:00
---

## Schritte
1. Starte oder fokussiere Word.
2. Warte 4 Sekunden, um sicherzustellen, dass Word geladen ist.
3. Prüfe den Fokus auf das Word-Fenster.
4. Wenn der 'Neues Dokument'-Auswahlscreen sichtbar ist, bestätige mit Enter und warte 2 Sekunden.
5. Tippe den Text 'Hallo Word vom Coordinator'.
6. Validiere, dass der Text im Word-Editor sichtbar ist.