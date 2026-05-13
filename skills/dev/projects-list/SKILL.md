---
agents:
- '*'
app: dev
attempts: 0
confidence: 1.0
description: Listet alle Coding-Projekte aus dem .rowboat/knowledge/Coding-Projects/
  Manifest-Verzeichnis mit Stack, Git-Status, Deploy-URL, und Tags. Ideal um zu sehen
  was an Projekten existiert. Nutzt project_status.py mit --all.
inputs:
- name: refresh
  type: boolean
  default: false
  description: Wenn true, synct vorher git state (branch + last_commit + dirty) aus
    jedem Repo neu ins Manifest
expected_state:
  description: Markdown-Bericht aller Projekte mit Steckbrief pro Projekt
  verification_tool: shell_exec
name: projects-list
requires_approval: false
successes: 0
last_adjusted: null
---

## Wann diesen Skill nutzen

Wenn der User sagt:
- "welche projekte hab ich?"
- "list meine coding projekte"
- "show all projects"
- "übersicht über alle repos"

## Schritte

1. **Mit oder ohne refresh entscheiden**:
   - Default: ohne `--refresh` — nur Manifest lesen, schnell
   - Wenn User "frisch", "aktuell", "synct mal", "refresh" sagt: `--refresh` anhängen damit der Skill vor dem Listing `git rev-parse` macht und veraltete `last_commit`-Felder synct

2. **Skript aufrufen**:
   ```bash
   python scripts/project_status.py --all [--refresh]
   ```

3. **Output zurückgeben**: das Skript gibt markdown-formatted Output, der User kann den direkt lesen. Nicht zusätzlich umformatieren.

## Verwandte Skills

- `/project-status <slug>` — Status nur eines Projekts
- `/project-bootstrap` — neues Projekt anlegen
