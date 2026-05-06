---
agents:
- '*'
app: excel
attempts: 1
confidence: 1.0
description: Pastet die komplette VibeMind-Skill-Library als 2D-Tabelle nach Excel
  A1
expected_state:
  description: Excel zeigt eine 3-Spalten-Tabelle Name/App/Description in A1:C<n+1>
    mit allen Skill-Namen
inputs: []
name: excel-paste-skill-list-table
requires_approval: true
successes: 1
last_adjusted: '2026-05-04T09:43:59.269749+00:00'
---

## Skill Steps

1. **Approval Request**: Request approval for the action 'Lernlauf excel-paste-skill-list: Excel oeffnen + komplette Skill-Library-Tabelle nach A1 pasten + validieren'.
2. **Skill List Retrieval**: Retrieve the current skill list using `skill_list` without an app filter.
3. **2D List Construction**: Construct a 2D list with the first row as headers [Name, App, Description], followed by each skill as a row.
4. **Excel Launch/Focus**: Launch or focus Excel and wait 5 seconds for the workbook to load.
5. **New Workbook Screen Check**: If Excel shows a 'Neue Mappe' screen, confirm with Enter and wait 2 seconds.
6. **Paste Table**: Use `excel_paste_table` with the constructed 2D list, starting at cell A1.
7. **Validation**: Use `vision_analyze` to check if the headers in A1, B1, C1 are correct.
8. **Success Criteria**: The skill is successful if `header_correct` is true.
