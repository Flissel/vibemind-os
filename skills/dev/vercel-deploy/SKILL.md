---
agents:
- '*'
app: dev
attempts: 0
confidence: 1.0
description: Deployt ein Coding-Projekt auf Vercel. Liest code_path aus dem Projekt-Manifest
  (.rowboat/knowledge/Coding-Projects/<slug>.md), führt `vercel --yes` aus, schreibt
  die Deployment-URL zurück ins Manifest (deployment.provider=vercel, deployment.url).
inputs:
- name: slug
  type: string
  required: true
- name: prod
  type: boolean
  default: false
expected_state:
  description: Vercel-Deployment ist live (URL erreichbar), Manifest hat deployment.url
    + deployment.provider=vercel.
  verification_tool: shell_exec
name: vercel-deploy
requires_approval: false
successes: 0
last_adjusted: null
---

## Wann diesen Skill nutzen

Wenn der User sagt:
- "deploy <projekt> auf vercel"
- "vercel deploy <projekt>"
- "<projekt> live nehmen" (im Kontext von Vercel)
- "<projekt> nach prod deployen"

## Voraussetzungen

- `vercel` CLI installiert und authentifiziert. Check: `vercel whoami`
- Projekt-Code unter `.rowboat/projects/<slug>/` mit irgendwas das Vercel deployen kann (Next.js, React-Build, statisches HTML, oder Functions). Vanilla Python ohne `vercel.json` wird NICHT deployen — Vercel ist primär für Frontend/Edge.

## Schritte

1. **Slug ermitteln** (siehe `/github-create-repo` — gleiche Logik).

2. **Prod oder Preview entscheiden**:
   - Default: Preview-Deploy
   - Wenn User "prod", "production", "live" sagt → `--prod`

3. **Owner ermitteln** (analog zu `/github-create-repo`):
   - **Default für VibeMind-Coding-Projekte**: `--owner Vibemind-LAB` (deployt unters Team `vibe-mind-lab`, nutzt Token aus `VERCEL_TOKEN_VIBEMIND_LAB`)
   - Persönliche Projekte: `--owner` weglassen (nutzt interaktive `vercel login`-Identity)

4. **Skript aufrufen**:
   ```bash
   python scripts/vercel_deploy.py --slug <slug> [--owner Vibemind-LAB] [--prod]
   ```

4. **Bei ERSTEM Deploy**: Vercel CLI fragt interaktiv nach project-link. Das Skript nutzt `--yes` für auto-accept, aber wenn was hängt → mit `cd .rowboat/projects/<slug> && vercel link` einmalig manuell linken, dann nochmal den Skill aufrufen.

5. **Output verifizieren**: JSON enthält `url` (z.B. `https://test-bootstrap-abc.vercel.app`). Sag dem User die URL. Wenn `manifest_updated: false`, war kein vercel.app-Link im Output — wahrscheinlich ein Fehler, schau dir den stderr an.

## Fehlerbehandlung

- `vercel CLI not found`: User soll `npm i -g vercel` ausführen.
- `deploy failed`: zeig dem User stderr. Häufige Ursachen:
  - Kein `package.json` oder Vercel kann den Stack nicht erkennen → User muss eine `vercel.json` anlegen
  - Auth abgelaufen → `vercel login`
- `Timeout nach 5 Minuten`: meist Build hängt. User soll im Vercel-Dashboard nachschauen.

## Verifikation

```bash
curl -sI <returned-url> | head -1   # sollte HTTP/2 200 zeigen
```

Plus Manifest-Check:
```bash
cat C:/Users/User/.rowboat/knowledge/Coding-Projects/<slug>.md | head -20
```

## Was diesen Skill NICHT betrifft

- **Custom-Domains**: nicht automatisiert. User muss im Vercel-Dashboard verlinken.
- **Env-Vars**: nicht automatisch hochgeladen. Falls Secrets nötig: `vercel env add` separat.
- **Rollback**: nicht implementiert. User kann via `vercel rollback` im Repo-Ordner.
