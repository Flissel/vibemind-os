---
agents:
- '*'
app: dev
attempts: 0
confidence: 1.0
description: Zeigt den Status eines einzelnen Coding-Projekts aus dem .rowboat-Manifest
  inkl. Pfad, Stack, Git-Branch+Commit (live aus dem Repo), Remote-URL, Deploy-URL,
  Tags. Optional vorher `--refresh` für frischen Git-Sync.
inputs:
- name: slug
  type: string
  required: true
- name: refresh
  type: boolean
  default: true
expected_state:
  description: Markdown-Bericht des einen Projekts
  verification_tool: shell_exec
name: project-status
requires_approval: false
successes: 0
last_adjusted: null
---

## Wann diesen Skill nutzen

Wenn der User sagt:
- "status von <projekt>"
- "wie steht es um <projekt>"
- "info über <projekt>"
- "show <projekt>"
- "<projekt>: status?" (Projekt-Präfix-Style aus project_resolver)

## Schritte

1. **Slug ermitteln**:
   - Aus User-Prompt extrahieren
   - Falls unklar / Mehrdeutigkeit: `python scripts/project_resolver.py --list` zeigt alle slugs

2. **Skript aufrufen**:
   ```bash
   python scripts/project_status.py --slug <slug> --refresh
   ```

   `--refresh` ist Default bei diesem Skill (Single-Project ist schnell zu refreshen), damit `last_commit` immer aktuell ist.

3. **Output zurückgeben**: markdown-formatted Status-Block direkt anzeigen.

## Fehlerbehandlung

- `manifest not found`: Slug existiert nicht. Sag dem User welche slugs es gibt mit `python scripts/project_resolver.py --list`.
- `code_path missing`: Projekt-Manifest zeigt auf nicht-existenten Pfad. Wahrscheinlich wurde der Code-Ordner manuell gelöscht. User muss entscheiden ob Manifest auch weg soll oder Pfad korrigiert wird.

## Verwandte Skills

- `/projects-list` — alle Projekte
- `/project-bootstrap` — neues Projekt
- `/github-create-repo`, `/vercel-deploy` — Manifest mit Remote/Deploy-Info auffüllen
